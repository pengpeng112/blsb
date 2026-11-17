"""Phase 3：r11 连接器×2 + source config + 字典（DTR-SCOPE-DEPLOY-20261028）。

住院：connector + config publish+enable（复用 r10 已批 collect 证据）。
退药：connector + config publish 不 enable（profile 闸 + 不启用双保险）。
字典：bill_type/query_bill_type 的 INPATIENT_DISPENSE→321/A approve；
      PATIENT_RETURN 仅 DRAFT（发送 fail-closed）。
幂等：按 connector name 复用；字典版本 CONFLICT 视为已建。
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, re, shlex

ROOT = Path(__file__).resolve().parents[3]

REMOTE = r'''
import json,subprocess,os
out={"phase":"config"}
TOKEN=os.environ.get("BLYB_TOKEN","")
def api(method,path,body=None):
    import urllib.request,urllib.error
    req=urllib.request.Request("http://localhost:8000/api/v1"+path,method=method,
        headers={"Authorization":"Bearer "+TOKEN,"Content-Type":"application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req,timeout=60) as r:
            return r.status,(json.load(r) or {}).get("data")
    except urllib.error.HTTPError as exc:
        try: e=json.load(exc).get("error",{})
        except Exception: e={}
        return exc.code,e
try:
    assert TOKEN,"NO_TOKEN"
    code,_=api("GET","/drug-trace/readiness?profile=alihealth_yljg_retail_v1")
    out["readiness_probe"]=code
    assert code==200,"API_NOT_READY"
    results={}
    # 幂等：按 name 找已有 connector
    code,list_=api("GET","/connectors")
    assert code==200,"LIST_CONNECTORS_FAIL"
    def ensure_connector(name):
        for c in list_ or []:
            if c.get("name")==name: return c["id"],"existing"
        code,res=api("POST","/connectors",{"direction":"source",
            "plugin_id":"sources.db_view.drug_trace","name":name,"config":{}})
        assert code==200,f"CREATE_{name}_FAIL:{res}"
        return res["connector_id"],"created"
    inp_id,inp_st=ensure_connector("his-inpatient-r11a")
    ret_id,ret_st=ensure_connector("his-return-r11a")
    results["inp"]={"id":inp_id,"st":inp_st}; results["ret"]={"id":ret_id,"st":ret_st}
    def cfg(profile,bill_view,code_view):
        return {"mode":"oracle","source_profile":profile,
                "dsn_env":"BLYB_DRUGTRACE_ORACLE_DSN",
                "objects":{"bill_view":bill_view,"code_view":code_view},
                "collection_mode":"batch"}
    # 住院：publish+enable
    code,res=api("POST",f"/drug-trace/sources/{inp_id}/config-revisions",
        {"content":cfg("blyb_drugtrace_inp_views@20261028-r11a",
                       "PHARMACY.V_DRUG_TRACE_BILL_INP","PHARMACY.V_DRUG_TRACE_CODE_INP"),
         "publish":True,"enable":True})
    results["inp_config"]={"code":code,"res":res}
    assert code==200,f"INP_CONFIG_FAIL:{res}"
    # 退药：profile 闸在 publish 阶段即拦截（SOURCE_PROFILE_UNVERIFIED=设计内预期；
    # 连接器保持 DRAFT 无配置，比"发布不启用"更彻底的 fail-closed）
    code,res=api("POST",f"/drug-trace/sources/{ret_id}/config-revisions",
        {"content":cfg("blyb_drugtrace_return_views@20261028-r11a",
                       "PHARMACY.V_DRUG_TRACE_RETURN","PHARMACY.V_DRUG_TRACE_RETURN_CODE"),
         "publish":True,"enable":False})
    results["ret_config"]={"code":code,"res":res}
    if code==422 and "SOURCE_PROFILE_UNVERIFIED" in str(res.get("message","")):
        results["ret_config"]["as_expected"]="blocked_by_profile_gate"
    else:
        assert code==200,f"RET_CONFIG_FAIL:{res}"
    # 字典
    def dict_post(ns,ver,items,approve):
        code,res=api("POST","/dictionaries",
            {"namespace":ns,"version":ver,"items":items,"approve":approve})
        return {"code":code,"res":res,"conflict_ok":code==409}
    results["dict_bill_type"]=dict_post("drugtrace.bill_type","r11-20261028",
        [{"code":"INPATIENT_DISPENSE","target":"321"}],True)
    results["dict_query"]=dict_post("drugtrace.query_bill_type","r11-20261028",
        [{"code":"INPATIENT_DISPENSE","target":"A"}],True)
    results["dict_bill_type_ret_draft"]=dict_post("drugtrace.bill_type","r11-20261028-return-draft",
        [{"code":"PATIENT_RETURN","target":"321"}],False)
    results["dict_query_ret_draft"]=dict_post("drugtrace.query_bill_type","r11-20261028-return-draft",
        [{"code":"PATIENT_RETURN","target":"A"}],False)
    assert results["dict_bill_type"]["code"] in (200,409)
    assert results["dict_query"]["code"] in (200,409)
    assert results["dict_bill_type_ret_draft"]["code"] in (200,409)
    assert results["dict_query_ret_draft"]["code"] in (200,409)
    # 验证
    code,list_=api("GET","/connectors")
    results["final_connectors"]=[{"name":c["name"],"status":c["status"]} for c in (list_ or []) if "r11a" in (c.get("name") or "")]
    code,dics=api("GET","/dictionaries")
    results["final_dicts"]=[{"ns":d["namespace"],"v":d["version"],"approved":bool(d.get("approved_by"))}
                             for d in (dics or []) if d["namespace"].startswith("drugtrace.bill_type")
                             or d["namespace"].startswith("drugtrace.query_bill_type")]
    out["result"]="PHASE3_OK"; out["exit_code"]=0; out["details"]=results
except BaseException as exc:
    out["result"]="FAILED"; out["error_type"]=type(exc).__name__; out["error"]=str(exc)[:400]; out["exit_code"]=99
    try: out["details"]=results
    except Exception: pass
finally:
    print("__DEPLOY__"+json.dumps(out,ensure_ascii=False,default=str)[:6000]+"__END__")
'''

spec = importlib.util.spec_from_file_location(
    "fb", ROOT / "docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py")
fb = importlib.util.module_from_spec(spec); spec.loader.exec_module(fb)
# 通过 host 读取 token 经 docker exec 环境变量传入（token 不落远端脚本文件）
conn = fb.deploy.connect_jump(fb.creds()["jump"])
host_read = ("T=$(grep -m1 '^BLYB_BOOTSTRAP_ADMIN_TOKEN=' /etc/blyb/runtime.env | cut -d= -f2-); "
             "docker exec -i -e PYTHONPATH=/app -e BLYB_TOKEN=\"$T\" blyb-core-1 python - <<'PYEOF'\n"
             + REMOTE + "\nPYEOF")
rc, raw = fb.nested(conn, host_read, timeout=600)
m = re.search(r"__DEPLOY__(\{.*\})__END__", raw, re.S)
ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
out_path = ROOT / "docs/development/drug-trace-scope-dev-20261027/evidence/deploy"
out_path.mkdir(parents=True, exist_ok=True)
payload = m.group(1) if m else json.dumps({"rc": rc, "raw_tail": raw[-1500:]})
(out_path / f"phase3-config-{ts}.json").write_text(payload, encoding="utf8")
print(payload[:2500])
