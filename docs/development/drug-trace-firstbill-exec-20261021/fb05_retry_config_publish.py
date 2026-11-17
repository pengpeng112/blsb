"""FB-05 配置发布重试探针：只做 destination config 发布，抓完整响应。"""
from __future__ import annotations
import importlib.util, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
deploy_fb05 = importlib.util.spec_from_file_location(
    "dfb", HERE / "deploy_fb05.py")

CODE = r'''
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
        try: body = json.loads(e.read() or b"{}")
        except Exception: body = {"raw": "non-json"}
        return e.code, body

out = {}
# 1) dry-run：完整校验结果
out["dry_run"] = call("POST",
    f"/drug-trace/destinations/{args['dest_id']}/config-revisions",
    {"content": args["config"], "dry_run": True, "set_real": True})
# 2) 正式发布（幂等：重复执行返回既有结果）
out["publish"] = call("POST",
    f"/drug-trace/destinations/{args['dest_id']}/config-revisions",
    {"content": args["config"], "publish": True, "set_real": True})
# 3) 世代清单
out["list"] = call("GET",
    f"/drug-trace/destinations/{args['dest_id']}/config-revisions")
# 4) readiness 层现状
out["readiness"] = call("GET",
    f"/drug-trace/readiness?destination_id={args['dest_id']}")
print("__FB05_RETRY__" + json.dumps(out, ensure_ascii=False, default=str)
      + "__END__")
'''


def main() -> int:
    spec = importlib.util.spec_from_file_location("dfb", HERE / "deploy_fb05.py")
    dfb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dfb)
    args = {"dest_id": dfb.DEST_ID, "config": json.loads(
        (HERE / "real-destination-config.json").read_text())}
    jump = dfb.deploy.connect_jump(dfb.creds()["jump"])
    try:
        rc = dfb.feed_file(jump, "/root/fb05-retry.py", CODE.encode())
        rc2 = dfb.feed_file(jump, "/root/fb05-retry-args.json",
                            json.dumps(args).encode())
        print(f"[retry] 脚本/参数已送 rc={rc},{rc2}")
        cmd = (
            "docker cp /root/fb05-retry.py blyb-core-1:/tmp/fb05-retry.py && "
            "sed -n 's/^BLYB_BOOTSTRAP_ADMIN_TOKEN=//p' /etc/blyb/runtime.env "
            "> /tmp/.fbtok && chmod 600 /tmp/.fbtok && "
            "cat /tmp/.fbtok /root/fb05-retry-args.json | "
            "docker exec -i -u 0 blyb-core-1 python /tmp/fb05-retry.py ; "
            "rc=$?; rm -f /tmp/.fbtok; docker exec -u 0 blyb-core-1 rm -f "
            "/tmp/fb05-retry.py; exit $rc")
        rc3, tr = dfb.nested(jump, cmd, 300)
        text = tr.replace("\r\n", "\n")
        import re
        m = re.search(r"__FB05_RETRY__(\{.*?\})__END__", text, re.S)
        if m:
            out = json.loads(m.group(1))
            slim = {k: (v if k != "readiness" else
                        {kk: vv for kk, vv in (v[1].get("data") or {}).items()
                         if kk in ("layers", "report")})
                    for k, v in out.items()}
            dfb.save_evidence("config-retry", {"rc": rc3, "result": out})
            print(json.dumps(slim, ensure_ascii=False, indent=1)[:2500])
            return 0
        print(text[-1500:])
        dfb.save_evidence("config-retry", {"rc": rc3, "output": text[-3000:]})
        return 1
    finally:
        jump.close()


if __name__ == "__main__":
    raise SystemExit(main())
