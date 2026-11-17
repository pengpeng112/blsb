#!/usr/bin/env python3
"""DTR-FIRSTBILL-EXEC-20261021 · FB-07 部署 + FB-05 真实配置发布与只读联调驱动。

复用既有严格通道（跳板嵌套 ssh、指纹先核）与上棒 backup/upgrade 流程。
Secret 纪律：AppSecret 仅 worker env（本脚本不接触）；引导令牌只经
runtime.env → root-only 临时文件 → docker stdin → 容器内存，不进 argv/日志；
ref_ent_id 同通道；全部回执只落白名单字段。

步骤：
  transfer   新发行 fb-20261021T164800Z 经跳板中继上载+哈希校验
  backup     升级前一致性备份 --with-objects（短停写入口；系统当前0业务数据）
  upgrade    install.sh 新发行 → status
  postcheck  动态 readiness（新 CLI）+ worker 侧布尔 + 迁移台账
  api-setup  经 API：发布真实目的地配置（set_real+publish）、批准
             bill_type/query_bill_type 字典（官方2026.01指南证据）、
             登记 EXT-09 query 证据；前后 readiness 对比
  diagnose   真实 worker→mTLS→DMZ→平台 只读诊断查询（探针单号，零上传）
             + DMZ 侧平台 Date 头取平台权威时间（EXT-05 佐证）
用法：python deploy_fb05.py <step>
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
IMPL = ROOT / "implementation"
RELEASE_ID = "fb-20261021T164800Z"
LOCAL_REL = IMPL / "var" / RELEASE_ID          # transfer 用（先复制 rel-fb）
STAGING = "<prod-path>-staging"
REL_DIR = f"{STAGING}/{RELEASE_ID}"
EVID = HERE / "evidence"
TENANT = "first-hospital"
DEST_ID = "345df8f4-410e-4c89-a258-a34957eb4151"   # baseline 快照确认的零售目的地
PROFILE_RETAIL = "alihealth_yljg_retail_v1"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


probe = module("fb05_probe", ROOT / "docs/deployment/reporting-20261015/probe_ssh.py")
deploy = module("fb05_deploy", ROOT / "docs/deployment/reporting-20261015/deploy_remote.py")


def creds():
    return probe.credentials()


def save_evidence(name, payload):
    EVID.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    p = EVID / f"fb05-{name}-{ts}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    print(f"[evidence] {p.name}")


def nested(jump, script, timeout=900):
    return deploy.nested_exec(jump, creds(), script, timeout=timeout)


def feed_file(jump, remote_path: str, data: bytes, mode: str = "600"):
    """pty stdin 安全送文件（stty -echo；base64 分块 ≤3000 字符/行绕开
    tty 规程 ~4096 行长上限；transcript 不回显内容；行首 ^D 结束 cat）。"""
    import base64
    import re
    import shlex
    b64 = base64.b64encode(data).decode()
    chunks = [b64[i:i + 3000] for i in range(0, len(b64), 3000)] or [""]
    target = creds()["target"]
    remote = ("LC_ALL=C ssh -tt -p 22 -o ConnectTimeout=10 "
              "-o NumberOfPasswordPrompts=1 -o StrictHostKeyChecking=ask "
              "-o UserKnownHostsFile=/dev/null -o GlobalKnownHostsFile=/dev/null "
              "-o LogLevel=INFO "
              + shlex.quote(f"{target['user']}@{target['host']}") + " "
              + shlex.quote(
                  f"stty -echo; cat > {remote_path}.b64; stty echo; "
                  f"base64 -d {remote_path}.b64 > {remote_path}; "
                  f"rm -f {remote_path}.b64; chmod {mode} {remote_path}; "
                  "echo __FEED_OK__"))
    chan = jump.get_transport().open_session()
    chan.get_pty(term="vt100", width=200, height=50)
    transcript, sent_pw, accepted = "", False, False
    pin = json.loads((ROOT / ".ai/private/reporting-20261015/"
                      "target-nested-hostkey.json").read_text())["sha256"]
    chan.exec_command(remote)
    deadline = time.monotonic() + 240
    done = False
    while time.monotonic() < deadline:
        if chan.recv_ready():
            transcript += chan.recv(65536).decode("utf-8", "replace")
            if "continue connecting" in transcript and not accepted:
                m = re.search(r"key fingerprint is (SHA256:[A-Za-z0-9+/=]+)",
                              transcript)
                if not m or m.group(1) != pin:
                    raise RuntimeError(f"fingerprint mismatch: {m and m.group(1)}")
                chan.send("yes\n")
                accepted = True
            if re.search(r"password:\s*$", transcript, re.I) and not sent_pw:
                chan.send(target["password"] + "\n")
                sent_pw = True
                time.sleep(2.0)          # 等 stty -echo 生效再送内容
                for chunk in chunks:
                    chan.send(chunk + "\n")
                    time.sleep(0.15)
                time.sleep(0.3)
                chan.send("\x04")        # 行首 EOT：cat 收到 EOF
                done = True
        if done and "__FEED_OK__" in transcript:
            break
        if chan.exit_status_ready() and not chan.recv_ready():
            break
        time.sleep(0.05)
    rc = chan.recv_exit_status() if chan.exit_status_ready() else -1
    chan.close()
    if "__FEED_OK__" not in transcript:
        raise RuntimeError(f"feed failed rc={rc}: {transcript[-300:]}")
    return rc


# ================================================================ 步骤实现
def step_transfer():
    import shutil
    if not LOCAL_REL.exists():
        shutil.copytree(IMPL / "var/rel-fb", LOCAL_REL)
        print(f"[transfer] 复制 rel-fb → {LOCAL_REL.name}")
    sys.argv = ["deploy_remote.py", "transfer", str(LOCAL_REL)]
    return deploy.main()


def step_backup():
    jump = deploy.connect_jump(creds()["jump"])
    try:
        print("[backup] 升级前一致性备份 --with-objects（短停写入口；当前0业务数据）")
        rc, tr = nested(jump,
                        f"cd {REL_DIR}/implementation/deploy/linux && "
                        f"bash backup.sh --with-objects 2>&1 | tail -25", 3000)
        out = tr.replace("\r\n", "\n")
        print(out[-2000:])
        rc2, tr2 = nested(jump, "ls -t <prod-path>/backups/ | head -1", 60)
        bdir = [l.strip() for l in tr2.splitlines() if l.strip()]
        bdir = bdir[-1] if bdir else "?"
        rc3, tr3 = nested(
            jump, f"cd <prod-path>/backups/{bdir} && sha256sum --quiet -c "
                  f"SHA256SUMS && echo BACKUP_HASH_OK", 600)
        ok = "BACKUP_HASH_OK" in tr3
        save_evidence("backup", {"rc": rc, "backup_dir": f"<prod-path>/backups/{bdir}",
                                 "hash_ok": ok, "output": out[-3000:]})
        assert ok, "备份哈希校验失败"
        print(f"[backup] 完成 <prod-path>/backups/{bdir}")
        return 0
    finally:
        jump.close()


def step_upgrade():
    jump = deploy.connect_jump(creds()["jump"])
    try:
        rc, tr = nested(jump,
                        f"cd {REL_DIR}/implementation && docker compose "
                        f"--project-name blyb --env-file /etc/blyb/runtime.env "
                        f"-f deploy/compose/docker-compose.yml "
                        f"-f deploy/compose/drug-trace.override.yml config --quiet "
                        f"&& echo STAGED_CFG_OK", 300)
        assert "STAGED_CFG_OK" in tr, tr[-500:]
        print("[upgrade] staged 渲染校验通过")
        rc, tr = nested(jump,
                        f"bash {REL_DIR}/implementation/deploy/linux/install.sh "
                        f"{REL_DIR} 2>&1 | tail -40", 3600)
        out = tr.replace("\r\n", "\n")
        print(out[-2500:])
        save_evidence("upgrade", {"rc": rc, "output": out[-6000:]})
        return rc
    finally:
        jump.close()


POSTCHECK = r"""
cd /opt/blyb/current/implementation || exit 9
printf '__RELEASE__\n'; readlink -f /opt/blyb/current
printf '__STATUS__\n'; bash deploy/linux/status.sh 2>&1 | head -14
printf '__READINESS__\n'
docker compose --project-name blyb --env-file /etc/blyb/runtime.env -f deploy/compose/docker-compose.yml -f deploy/compose/drug-trace.override.yml exec -T core python -m apps.init_drug_trace --readiness --tenant first-hospital 2>&1 | tail -40
printf '__SIGNATURE_BOOL__\n'
docker exec <worker-container> python -c "from destinations.alihealth_drugtrace.protocol import SIGNATURE_VERIFIED; print('SIGNATURE_VERIFIED=', SIGNATURE_VERIFIED)"
printf '__WORKER_BOOL__\n'
docker exec <worker-container> python -c "import os,json;print(json.dumps({'uid':os.getuid(),'secret':bool(os.environ.get('BLYB_ALIHEALTH_APP_SECRET')),'dsn_empty':os.environ.get('BLYB_DRUGTRACE_ORACLE_DSN','')=='','sigver':__import__('destinations.alihealth_drugtrace.protocol',fromlist=['SIGNATURE_VERIFIED']).SIGNATURE_VERIFIED}))"
printf '__MIGRATIONS__\n'
docker exec blyb-db-1 psql -U blyb -d blyb -At -c "BEGIN READ ONLY; SELECT step,name FROM schema_migrations ORDER BY step; ROLLBACK;" | tail -12
printf '__MTLS_HEALTH__\n'
docker exec <worker-container> python -c "
import ssl,socket,json
ctx=ssl.create_default_context(cafile='/etc/blyb/certs/dmz-ca.crt')
ctx.load_cert_chain('/etc/blyb/certs/client.crt','/etc/blyb/certs/client.key')
try:
 with socket.create_connection(('<relay-net-host>',443),8) as s:
  with ctx.wrap_socket(s,server_hostname='<relay-net-host>') as ss:
   ss.sendall(b'GET /health HTTP/1.1\r\nHost: health\r\nConnection: close\r\n\r\n')
   d=b''
   while True:
    p=ss.recv(4096)
    if not p: break
    d+=p
 print(json.dumps({'status_line':d.split(b'\r\n',1)[0].decode('latin-1'),'UP':b'\"status\":\"UP\"' in d.replace(b' ',b'')}))
except Exception as e: print(json.dumps({'error':type(e).__name__}))
"
printf '__END__\n'
"""


def step_postcheck():
    jump = deploy.connect_jump(creds()["jump"])
    try:
        rc, tr = nested(jump, POSTCHECK, 900)
        out = tr.replace("\r\n", "\n")
        print(out[-6000:])
        save_evidence("postcheck", {"rc": rc, "output": out[-12000:]})
        return 0 if "__END__" in out else 1
    finally:
        jump.close()


# ---- api-setup：容器内经 localhost:8000 调 API（令牌经 root-only 文件→stdin）
API_SETUP_ARGS = {
    "dest_id": DEST_ID,
    "config": json.loads((HERE / "real-destination-config.json").read_text()),
    "dictionaries": [
        {"namespace": "drugtrace.bill_type",
         "version": "official-202601-r1",
         "items": [
             {"code": "PATIENT_DISPENSE", "target": "321",
              "label": "使用出库（官方指南2026.01：321=使用出库）",
              "evidence": "docs/development/drug-trace-firstbill-exec-20261021"
                          "/reference/official-guide-202601.md"}],
         "approve": True},
        {"namespace": "drugtrace.query_bill_type",
         "version": "official-202601-r1",
         "items": [
             {"code": "PATIENT_DISPENSE", "target": "A",
              "label": "A=全部（官方API页：A全部/AI全部入库/AO全部出库）",
              "evidence": "docs/development/platform-live-test-20261020"
                          "/evidence/official-query-billstatus.txt"}],
         "approve": True},
    ],
    "evidence": {
        "profile": PROFILE_RETAIL, "capability": "query",
        "destination_id": DEST_ID,
        "items": [{
            "key": "EXT-09",
            "evidence_ref": "official-guide-202601.md「上传单据后处理状态查询」"
                            "（result_type 0/1/3/4、sub_process_flag 51/52、"
                            "部分成功判定与限流退避；快照 2026-10-21）+ "
                            "fb02-our-signer-query-receipt-r2.json 平台受理实测",
            "provider": "码上放心官方运营文档（医疗机构接口对接指南-2026.01）",
            "captured_at": "2026-10-21"}],
        "valid_until": "2027-10-21T00:00:00+00:00"},
}

API_SETUP_CODE = r'''
import json, sys, urllib.request
tok = sys.stdin.readline().strip()
args = json.loads(sys.stdin.read())
H = {"Authorization": "Bearer " + tok, "Content-Type": "application/json"}
BASE = "http://localhost:8000/api/v1"

def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=H, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")

out = {}
def slim(resp):
    body = resp[1] if isinstance(resp, tuple) else resp
    d = body.get("data") if isinstance(body, dict) else {}
    if not isinstance(d, dict):
        d = {}
    return {k: v for k, v in d.items()
            if k in ("profiles", "layers", "destination",
                     "destination_ambiguous", "capabilities",
                     "dictionaries", "signature_verified",
                     "config_revision_id", "state", "created",
                     "dictionary_id", "evidence_id", "status",
                     "destination_is_mock", "note", "overall",
                     "error", "message")}
out["readiness_before"] = slim(call("GET", "/drug-trace/readiness"))
cfg = dict(args["config"]); cfg["publish"] = True; cfg["set_real"] = True
out["config_publish"] = slim(call(
    "POST", f"/drug-trace/destinations/{args['dest_id']}/config-revisions", cfg))
out["dictionaries"] = [slim(call("POST", "/dictionaries", d))
                       for d in args["dictionaries"]]
out["evidence_query"] = slim(call("POST", "/drug-trace/evidence",
                                  args["evidence"]))
out["readiness_after"] = slim(call(
    "GET", f"/drug-trace/readiness?destination_id={args['dest_id']}"))
print("__FB05_API_RESULT__" + json.dumps(out, ensure_ascii=False,
                                         default=str) + "__END__")
'''


def step_api_setup():
    jump = deploy.connect_jump(creds()["jump"])
    try:
        args_bytes = json.dumps(API_SETUP_ARGS, ensure_ascii=False).encode()
        rc = feed_file(jump, "/root/fb05-args.json", args_bytes)
        print(f"[api-setup] 参数文件已送（rc={rc}；含 ref_ent_id 受限保护）")
        rc2 = feed_file(jump, "/root/fb05-apisetup.py",
                        API_SETUP_CODE.encode())
        print(f"[api-setup] 容器内执行脚本已送（rc={rc2}）")
        # 令牌：runtime.env → root-only 临时文件；脚本经 docker cp 进容器
        # （docker cp 保留 root:600，容器内以 -u 0 一次性执行读取）；
        # stdin 只送 令牌行+参数JSON（均不进任何 argv/日志）
        cmd = (
            "docker cp /root/fb05-apisetup.py blyb-core-1:/tmp/fb05-apisetup.py"
            " && sed -n 's/^BLYB_BOOTSTRAP_ADMIN_TOKEN=//p' "
            "/etc/blyb/runtime.env > /tmp/.fbtok && chmod 600 /tmp/.fbtok && "
            "cat /tmp/.fbtok /root/fb05-args.json | "
            "docker exec -i -u 0 blyb-core-1 python /tmp/fb05-apisetup.py ; "
            "rc=$?; rm -f /tmp/.fbtok; docker exec -u 0 blyb-core-1 rm -f "
            "/tmp/fb05-apisetup.py; exit $rc")
        rc3, tr = nested(jump, cmd, 300)
        text = tr.replace("\r\n", "\n")
        if "__FB05_API_RESULT__" in text:
            result = json.loads(
                text.split("__FB05_API_RESULT__", 1)[1].split("__END__")[0])
            save_evidence("api-setup", {"rc": rc3, "result": result})
            print(json.dumps(result, ensure_ascii=False, indent=1)[:3000])
            return 0
        save_evidence("api-setup", {"rc": rc3, "output": text[-4000:]})
        print(text[-2000:])
        return 1
    finally:
        jump.close()


DIAG_CODE = r'''
import json, sys, os
sys.path[:0] = ["/app/plugins", "/app/packages"]
args = json.loads(open("/tmp/fb05-diag-args.json").read())
from datetime import datetime, timedelta, timezone
from core_db import make_engine, make_session_factory
engine = make_engine(); sf = make_session_factory(engine)
from domains.drug_trace import service as S
with sf() as db:
    from core_db.models import Destination, Tenant
    t = db.query(Tenant).filter_by(code="first-hospital").first()
    dest = db.get(Destination, args["dest_id"])
    out = {"uid": os.getuid(), "dest": {"code": dest.code, "is_mock": bool(dest.is_mock),
                                        "config_revision": dest.config_revision}}
    client = S.build_client(db, t.id, dest, purpose="query")
    cn = datetime.now(timezone(timedelta(hours=8)))
    from uuid import uuid4
    probe = "BLYB_FB05_" + uuid4().hex[:14].upper()
    biz = {"ref_ent_id": args["ref_ent_id"], "bill_code": probe,
           "bill_type": "A",
           "begin_date": cn.strftime("%Y-%m-%d"),
           "end_date": cn.strftime("%Y-%m-%d"), "page": "1", "page_size": "20"}
    r = client.query_bill_status(biz)
    out["query"] = {"probe_bill_code": probe,
                    "found": r.get("found"), "total_num": r.get("total_num"),
                    "unknown": r.get("unknown"), "error": r.get("error"),
                    "reason": r.get("reason"),
                    "entry_count": len((r.get("bills") or {}).get("list") or []),
                    "timestamp_gmt8": cn.strftime("%Y-%m-%d %H:%M:%S")}
    out["signature_verified"] = __import__(
        "destinations.alihealth_drugtrace.protocol",
        fromlist=["SIGNATURE_VERIFIED"]).SIGNATURE_VERIFIED
    print("__FB05_DIAG__" + json.dumps(out, ensure_ascii=False) + "__END__")
engine.dispose()
'''


def step_diagnose():
    """真实 worker→mTLS→DMZ→平台 只读诊断 + DMZ 取平台 Date 头（时钟佐证）。"""
    import re as _re
    from uuid import uuid4
    # ref_ent_id 从本机 profile 读（不打印）
    profile = json.loads((ROOT / ".ai/private/alihealth-drugtrace-20261020"
                          "/profile.json").read_text())
    diag_args = {"dest_id": DEST_ID, "ref_ent_id": profile["ref_ent_id"]}
    jump = deploy.connect_jump(creds()["jump"])
    receipt = {"captured_at": datetime.now(timezone.utc).isoformat(),
               "route": "<prod-host>_worker->mTLS->DMZ_forwarder->platform",
               "method": "query.billstatus", "uploads": 0, "status": "RUNNING"}
    try:
        rc = feed_file(jump, "/root/fb05-diag-args.json",
                       json.dumps(diag_args).encode())
        rc2 = feed_file(jump, "/root/fb05-diag.py", DIAG_CODE.encode())
        print(f"[diagnose] 参数/脚本已送 rc={rc},{rc2}")
        # 先送参数进容器（docker cp 后 root:600 → 容器内以 -u 0 一次性执行）
        rc_cp, tr_cp = nested(
            jump, "docker cp /root/fb05-diag-args.json "
                  "<worker-container>:/tmp/fb05-diag-args.json && echo ARG_CP_OK", 60)
        assert "ARG_CP_OK" in tr_cp, tr_cp[-200:]
        # 同窗口取 <prod-host> 时钟（与平台 Date 头对照，EXT-05 佐证）
        rc_clk, tr_clk = nested(
            jump, "date -u '+%Y-%m-%dT%H:%M:%SZ'", 30)
        clk = [l.strip() for l in tr_clk.replace("\r\n", "\n").splitlines()
               if l.strip().startswith("2026-")]
        receipt["target_8156_clock_utc"] = clk[-1] if clk else None
        rc3, tr = nested(
            jump, "cat /root/fb05-diag.py | docker exec -i -u 0 -w /app "
                  "<worker-container> python - ; echo DIAG_RC=$?", 300)
        text = tr.replace("\r\n", "\n")
        m = _re.search(r"__FB05_DIAG__(\{.*?\})__END__", text, _re.S)
        if m:
            diag = json.loads(m.group(1))
            receipt.update(status="CAPTURED", diagnostic=diag)
            print(json.dumps(diag, ensure_ascii=False, indent=1))
        else:
            receipt.update(status="ERROR", output=text[-1500:])
            print(text[-1200:])
        # DMZ：平台 Date 头 + DMZ 时钟（EXT-05 佐证）
        helper = module("fb05_dmz", ROOT / "docs/deployment/"
                        "drug-trace-install-20261021/deploy_dmz.py")
        client = helper.connect()
        try:
            rc_d, out_d, _ = helper.run(
                client, "date -u '+%Y-%m-%dT%H:%M:%SZ'; "
                        "curl -4 --noproxy '*' -sI --connect-timeout 5 "
                        "--max-time 15 https://eco.taobao.com/router/rest "
                        "| grep -i '^date:' | head -1", 30)
            receipt["dmz_platform_date_probe"] = {"rc": rc_d, "out": out_d.strip()}
            print("[diagnose] DMZ/平台时间:", out_d.strip())
        finally:
            client.close()
        return 0 if receipt["status"] == "CAPTURED" else 1
    finally:
        save_evidence("diagnose", receipt)
        jump.close()


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in (
            "transfer", "backup", "upgrade", "postcheck", "api-setup",
            "diagnose"):
        print(__doc__)
        return 2
    return {"transfer": step_transfer, "backup": step_backup,
            "upgrade": step_upgrade, "postcheck": step_postcheck,
            "api-setup": step_api_setup, "diagnose": step_diagnose}[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())
