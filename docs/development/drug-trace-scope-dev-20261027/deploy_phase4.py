"""Phase 4：住院冒烟（DTR-SCOPE-DEPLOY-20261028）。

小窗采集 10-27 全天（住院连接器）→ 记 summary → DRY_RUN 预览（验 bill_type=321）
→ 单张真实发送（050121 VALID 单）→ 轮询平台终态。全部留证。
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, re

ROOT = Path(__file__).resolve().parents[3]

REMOTE = r'''
import json,os,time
out={"phase":"smoke"}
TOKEN=os.environ.get("BLYB_TOKEN","")
assert TOKEN,"NO_TOKEN"
def api(method,path,body=None,timeout=90):
    import urllib.request,urllib.error
    req=urllib.request.Request("http://localhost:8000/api/v1"+path,method=method,
        headers={"Authorization":"Bearer "+TOKEN,"Content-Type":"application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            return r.status,(json.load(r) or {}).get("data")
    except urllib.error.HTTPError as exc:
        try: e=json.load(exc).get("error",{})
        except Exception: e={}
        return exc.code,e
try:
    INP="70433754-0ce6-413d-8ed4-04d704493bc2"
    WB,WE="2026-10-27T00:00:00","2026-10-28T00:00:00"
    # 1) 采集：总是新建（r10 日批同窗口运行已存在，列表接口无连接器字段不能按窗口复用；
    #    本连接器尚无任何运行，checkpoint 分区 r11-inp-smoke 独立）
    code,res=api("POST","/drug-trace/collection-runs",
        {"connector_id":INP,"window_begin":WB,"window_end":WE,
         "lookback_seconds":600,"mode":"oracle",
         "profile":"alihealth_yljg_retail_v1",
         "checkpoint_partition":"r11-inp-smoke"})
    assert code==200,f"COLLECT_CREATE_FAIL:{res}"
    run_id=res["collection_run_id"]
    deadline=time.monotonic()+1200; state=None; summary=None
    while time.monotonic()<deadline:
        code,runs=api("GET","/drug-trace/collection-runs?limit=200")
        cur=next((r for r in (runs or []) if r["id"]==run_id),None)
        if cur:
            state=cur["state"]; summary=cur.get("summary") or {}
            if state in ("DONE","FAILED","CANCELLED"): break
        time.sleep(10)
    out["collection"]={"id":run_id,"state":state,"summary":summary}
    assert state=="DONE",f"COLLECT_NOT_DONE:{state}"
    # 2) 找一张 050121 的 VALID 住院单
    code,bills=api("GET","/drug-trace/bills?business_kind=INPATIENT_DISPENSE&pharmacy_code=050121&limit=200")
    rows=[b for b in (bills or []) if b.get("quality_state")=="VALID"
          and b.get("pharmacy_code")=="050121" and (b.get("code_count") or 0)>0]
    assert rows,"NO_VALID_INPATIENT_050121"
    bill=rows[0]
    out["picked_bill"]={"id":bill["id"],"no":bill["source_bill_no"],
                        "codes":bill["code_count"],"rev":bill["current_revision_id"]}
    # 3) DRY_RUN 预览
    code,pv=api("POST","/drug-trace/previews",{"revision_id":bill["current_revision_id"]})
    out["preview"]={"code":code,"bill_type":(pv or {}).get("bill_type"),
                    "bill_code":(pv or {}).get("bill_code"),
                    "ref_ent_id":bool((pv or {}).get("ref_ent_id")),
                    "code_count":(pv or {}).get("code_count"),
                    "blocked":(pv or {}).get("blocked")}
    assert code==200 and pv.get("bill_type")=="321",f"PREVIEW_FAIL:{pv}"
    # 4) 真实发送单张
    code,scope=api("GET","/drug-trace/send-scope?profile=alihealth_yljg_retail_v1")
    dest=scope["destination"]["id"]; out["dest"]=dest
    code,pr=api("POST",f"/drug-trace/bills/{bill['id']}/prepare",
                {"profile":"alihealth_yljg_retail_v1","destination_id":dest})
    assert code==200,f"PREPARE_FAIL:{pr}"
    sid=pr["submission_id"]; out["submission"]=sid
    code,se=api("POST",f"/drug-trace/submissions/{sid}/send",
                {"confirm":True,"reason":"r11 inpatient first-bill smoke (scope-20261028 deploy)"})
    assert code==200,f"SEND_FAIL:{se}"
    out["send_job"]=se.get("job_id")
    # 5) 轮询终态（含自动查询退避，最长 10 分钟）
    deadline=time.monotonic()+600; final=None
    while time.monotonic()<deadline:
        code,subs=api("GET","/submissions?limit=100")
        cur=next((s for s in (subs if isinstance(subs,list) else (subs or {}).get("items",[]))
                  if s.get("id")==sid),None)
        if cur and cur.get("state") in ("CONFIRMED","REJECTED","PARTIAL"):
            final=cur["state"]; break
        if cur and cur.get("state") in ("UNKNOWN","SENDING") and time.monotonic()>deadline-60:
            final=cur["state"]; break
        time.sleep(15)
    out["final_state"]=final or "PENDING_AFTER_10MIN"
    out["result"]="PHASE4_OK" if final in ("CONFIRMED",) else ("PHASE4_SENT_"+str(final) if final else "PHASE4_PENDING")
    out["exit_code"]=0
except BaseException as exc:
    out["result"]="FAILED"; out["error_type"]=type(exc).__name__; out["error"]=str(exc)[:400]; out["exit_code"]=99
finally:
    print("__DEPLOY__"+json.dumps(out,ensure_ascii=False,default=str)[:6000]+"__END__")
'''

spec = importlib.util.spec_from_file_location(
    "fb", ROOT / "docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py")
fb = importlib.util.module_from_spec(spec); spec.loader.exec_module(fb)
conn = fb.deploy.connect_jump(fb.creds()["jump"])
host_cmd = ("T=$(grep -m1 '^BLYB_BOOTSTRAP_ADMIN_TOKEN=' /etc/blyb/runtime.env | cut -d= -f2-); "
            "docker exec -i -e PYTHONPATH=/app -e BLYB_TOKEN=\"$T\" blyb-core-1 python - <<'PYEOF'\n"
            + REMOTE + "\nPYEOF")
rc, raw = fb.nested(conn, host_cmd, timeout=2100)
m = re.search(r"__DEPLOY__(\{.*\})__END__", raw, re.S)
ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
out_path = ROOT / "docs/development/drug-trace-scope-dev-20261027/evidence/deploy"
payload = m.group(1) if m else json.dumps({"rc": rc, "raw_tail": raw[-2000:]})
(out_path / f"phase4-smoke-{ts}.json").write_text(payload, encoding="utf8")
print(payload[:3000])
