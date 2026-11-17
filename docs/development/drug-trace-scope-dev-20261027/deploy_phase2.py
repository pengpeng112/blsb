"""Phase 2：升级 + 驱动 v3（DTR-SCOPE-DEPLOY-20261028，2026-10-29 凌晨）。

沿用 stability-r3 升级模式：维护文件夹留证（baseline/upgrade.log/result.json），
upgrade.sh 失败走内置回退；驱动替换前备份+SHA 钉死；结束确保 timer active。
通过 jump+nested 通道在 <prod-host> 上执行 remote 脚本；token 不回显。
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, re, shlex

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]
DRIVER_LOCAL = ROOT / "docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py"
DRIVER_SHA = "2f33c0267caa22f6b7ca4c99b5b1642f0dbf8dc605e322656b0e9fc51d2cc662"
REL = "<prod-path>-staging/scope-20261028"

# 驱动按 deploy_fb05.feed_file 的等价方式送：base64 分块经 nested stdin cat 还原
driver_b64 = __import__("base64").b64encode(DRIVER_LOCAL.read_bytes()).decode()
assert len(driver_b64) < 3_000_000, "driver too large for inline transfer"

REMOTE = r'''
import json,hashlib,os,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
REL="<prod-path>-staging/scope-20261028"
FOLDER=Path("/root/blyb-maintenance-backups/upgrade-scope-20261028")
DRIVER=Path("/opt/blyb/tools/blyb-daily-upload.py")
NEW_DRIVER_B64=__DRIVER_B64__
NEW_DRIVER_SHA=__DRIVER_SHA__
out={"started_utc":datetime.now(timezone.utc).isoformat(),"phase":"upgrade"}
def run(args,timeout=120,log=None):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if log is not None and p.stdout: log.write(p.stdout)
    return p.returncode,p.stdout.strip(),p.stderr.strip()[-300:]
try:
    FOLDER.mkdir(parents=True,exist_ok=True)
    cur=Path("/opt/blyb/current").resolve().name
    out["release_before"]=cur
    drv_before=hashlib.sha256(DRIVER.read_bytes()).hexdigest()
    out["driver_sha_before"]=drv_before
    timer_before=subprocess.run(["systemctl","show","blyb-daily-upload.timer","-p","ActiveState","--value"],capture_output=True,text=True).stdout.strip()
    out["timer_before"]=timer_before
    snap=subprocess.run(["docker","exec","-e","PGOPTIONS=-c default_transaction_read_only=on","blyb-db-1","psql","-X","-U","blyb","-d","blyb","-At","-c",
        "SELECT json_build_object('states',(SELECT json_object_agg(state,n) FROM (SELECT state,count(*) n FROM submission GROUP BY state)x),'quota',(SELECT json_agg(row_to_json(q)) FROM (SELECT used,max_bills FROM drug_trace_send_quota)q),'inflight',(SELECT count(*) FROM delivery_attempt WHERE ended_at IS NULL))"],capture_output=True,text=True,timeout=30)
    (FOLDER/"baseline.json").write_text(json.dumps({"release_before":cur,"driver_sha_before":drv_before,"timer":timer_before,"db_snapshot":snap.stdout.strip()},ensure_ascii=False,indent=1))
    assert cur=="stability-20261027T064000Z-r3","RELEASE_DRIFT:"+cur
    # 发行包 staging 复验
    rc,o,e=run(["bash","-lc",f"cd {REL} && sha256sum --quiet -c SHA256SUMS && echo HASH_OK"],900)
    assert "HASH_OK" in o,"STAGING_HASH_FAIL:"+e
    out["staging_hash"]="OK"
    rc,o,e=run(["bash","-lc",f"chmod 755 {REL}/implementation/deploy/linux/*.sh && ls -l {REL}/implementation/deploy/linux/upgrade.sh | head -1"],60)
    out["chmod"]=o[-120:]
    # 升级
    subprocess.run(["systemctl","stop","blyb-daily-upload.timer"],capture_output=True)
    with (FOLDER/"upgrade.log").open("wb") as log:
        code=subprocess.run(["bash",f"{REL}/implementation/deploy/linux/upgrade.sh",REL],stdout=log,stderr=subprocess.STDOUT,timeout=1800).returncode
    out["upgrade_exit"]=code
    assert code==0,"UPGRADE_FAILED_see_upgrade.log"
    # 健康
    rc,o,e=run(["docker","ps","--format","{{.Names}} {{.Status}}"],60); out["containers"]=o
    healthy=o.count("healthy")
    assert healthy>=6,"CONTAINERS_NOT_HEALTHY:"+o[:200]
    rc,o,e=run(["docker","exec","blyb-core-1","python","-c",
        "import urllib.request,json;print(json.load(urllib.request.urlopen('http://localhost:8000/api/v1/health',timeout=10)).get('status','?'))"],60)
    out["api_health"]=o
    # 驱动 v3
    import base64
    new_bytes=base64.b64decode(NEW_DRIVER_B64)
    assert hashlib.sha256(new_bytes).hexdigest()==NEW_DRIVER_SHA,"DRIVER_SHA_MISMATCH_LOCAL"
    DRIVER.with_name("blyb-daily-upload.scope-20261028.bak.py").write_bytes(DRIVER.read_bytes())
    tmp=DRIVER.with_name("blyb-daily-upload.scope.next.py")
    assert not tmp.exists()
    tmp.write_bytes(new_bytes); os.chmod(tmp,0o755); os.replace(tmp,DRIVER)
    drv_after=hashlib.sha256(DRIVER.read_bytes()).hexdigest()
    assert drv_after==NEW_DRIVER_SHA,"DRIVER_SHA_MISMATCH_AFTER"
    out["driver_sha_after"]=drv_after
    # 迁移后核对（无新迁移预期：001-012 不变）
    rc,o,e=run(["docker","exec","blyb-db-1","psql","-X","-U","blyb","-d","blyb","-At","-c","SELECT count(*) FROM schema_migrations"],30)
    out["migration_rows"]=o
    # timer 恢复
    rc,o,e=run(["systemctl","start","blyb-daily-upload.timer"],30)
    rc2,timer_after,_=run(["systemctl","show","blyb-daily-upload.timer","-p","ActiveState","--value"],30)
    out["timer_after"]=timer_after
    rc3,nxt,_=run(["systemctl","list-timers","blyb-daily-upload.timer","--no-pager"],30)
    out["next_trigger_line"]=[l for l in nxt.splitlines() if "daily-upload" in l][:1]
    out["result"]="PHASE2_OK"
    out["exit_code"]=0
except BaseException as exc:
    subprocess.run(["systemctl","start","blyb-daily-upload.timer"],capture_output=True)
    out["result"]="FAILED"; out["error_type"]=type(exc).__name__; out["error"]=str(exc)[:400]
    out["exit_code"]=99
finally:
    (FOLDER/"phase2-result.json").write_text(json.dumps(out,ensure_ascii=False,indent=1))
    print("__DEPLOY__"+json.dumps({k:v for k,v in out.items() if k!="containers"})+"__END__")
'''.replace("__DRIVER_B64__", json.dumps(driver_b64)).replace("__DRIVER_SHA__", json.dumps(DRIVER_SHA))

spec = importlib.util.spec_from_file_location(
    "fb", ROOT / "docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py")
fb = importlib.util.module_from_spec(spec); spec.loader.exec_module(fb)
conn = fb.deploy.connect_jump(fb.creds()["jump"])
rc, raw = fb.nested(conn, "python3 -c " + shlex.quote(REMOTE), timeout=2100)
m = re.search(r"__DEPLOY__(\{.*\})__END__", raw, re.S)
ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
(HERE / "evidence/deploy" / f"phase2-upgrade-{ts}.json").write_text(
    (m.group(1) if m else json.dumps({"rc": rc, "raw_tail": raw[-1500:]})), encoding="utf8")
print((m.group(1) if m else f"NO MARKER rc={rc} tail={raw[-800:]}")[:1500])
