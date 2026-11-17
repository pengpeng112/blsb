"""真实运行栈功能测试（对已启动的 run_dev 栈做端到端业务验证）。

门控执行：BLYB_LIVE_STACK=1 时才运行（默认跳过，不混入常规回归）。
启动方式：
  BLYB_DATABASE_URL=sqlite:///.../core.sqlite3 BLYB_EXCHANGE_DB=.../exchange.sqlite3 \
  python run_dev.py &
  BLYB_LIVE_STACK=1 python -m pytest tests/functional/test_live_stack.py -q -s

覆盖：四服务探活、六主体登录、enroll随机令牌与轮换、真实Agent采集（模拟器→分片→存储）、
临床闭环（关联/质量/冻结/索引）、双目的地（推送受理≠确认、视图发布）、交换下载协议、
回执精确关联（R02）、人工绑定CAS（428/412/200）、Operation终态、metrics导入幂等、
ConfigRelease闭包、对账、审计、UI托管、跨源预检。
"""
from __future__ import annotations

import hashlib
import os
import time

import httpx
import pytest

CORE = os.environ.get("BLYB_CORE_URL", "http://127.0.0.1:8000")
STORAGE = os.environ.get("BLYB_STORAGE_URL", "http://127.0.0.1:8001")
EXCH = os.environ.get("BLYB_EXCHANGE_URL", "http://127.0.0.1:8002")
MOCK = os.environ.get("BLYB_MOCK_URL", "http://127.0.0.1:8090")

ADMIN = {"Authorization": "Bearer dev-admin-token"}
REVIEWER = {"Authorization": "Bearer dev-reviewer-token"}
COLLECTOR = {"Authorization": "Bearer dev-collector-token"}
AGENT = {"Authorization": "Bearer dev-agent-token"}

pytestmark = pytest.mark.skipif(
    os.environ.get("BLYB_LIVE_STACK") != "1",
    reason="live stack functional test; set BLYB_LIVE_STACK=1 with a running stack")


def _wait(cond, timeout=90, interval=0.5, msg="condition"):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = cond()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timeout waiting: {msg}; last={last!r}")


def test_live_stack_full_business_flow():
    c = httpx.Client(base_url=CORE, timeout=20)
    s = httpx.Client(base_url=STORAGE, timeout=30)
    x = httpx.Client(base_url=EXCH, timeout=30)
    m = httpx.Client(base_url=MOCK, timeout=15)

    # ---------------- 1. 四服务探活（mock平台此前无/health，属明确缺口）
    for cl, name in ((c, "core"), (s, "storage"), (x, "exchange"), (m, "mock")):
        r = cl.get("/health")
        assert r.status_code == 200, (name, r.status_code, r.text[:120])

    # ---------------- 2. 六主体登录 + 错误令牌拒绝
    roles = {}
    for tok, code in (("dev-admin-token", "admin"),
                      ("dev-configer-token", "configer"),
                      ("dev-reviewer-token", "reviewer"),
                      ("dev-operator-token", "operator"),
                      ("dev-auditor-token", "auditor"),
                      ("dev-collector-token", "collector")):
        r = c.post("/api/v1/auth/login", json={"token": tok})
        assert r.status_code == 200, (code, r.text)
        roles[code] = r.json()["data"]
    assert roles["admin"]["roles"] == ["platform_admin"]
    assert roles["collector"]["kind"] == "service"
    assert c.post("/api/v1/auth/login", json={"token": "wrong-token"}).status_code == 401

    # ---------------- 3. enroll：高熵随机令牌、重注册轮换、旧凭据失效
    body = {"enroll_token": "dev-enroll-token", "device_code": "FT-DEV-02",
            "device_name": "功能测试设备2"}
    e1 = c.post("/api/v1/agents/enroll", json=body).json()["data"]
    e2 = c.post("/api/v1/agents/enroll", json=body).json()["data"]
    assert e1["token"] != e2["token"], "重注册必须轮换令牌"
    assert len(e1["token"]) >= 32 and not e1["token"].startswith("agent-token-"), \
        "令牌必须为高熵随机值，不得由agent_id推导"
    assert c.post(f"/api/v1/agents/{e1['agent_id']}/heartbeat", json={},
                  headers={"Authorization": f"Bearer {e1['token']}"}).status_code == 401, \
        "轮换后旧令牌必须立即失效"
    assert c.post(f"/api/v1/agents/{e2['agent_id']}/heartbeat", json={},
                  headers={"Authorization": f"Bearer {e2['token']}"}).status_code == 200

    # ---------------- 4. 真实Agent采集：模拟器 → Agent → Storage 分片 → FILES_COMPLETE
    agents = c.get("/api/v1/agents", headers=ADMIN).json()["data"]
    agent_id = next(a["id"] for a in agents if a["device_code"] == "SCAN-WS-01")
    import _bootstrap  # noqa: F401
    from apps.edge_agent.service import AgentService
    from apps.edge_agent.scanner_simulator import demo_scenario, write_scan
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    watch = os.path.join(here, "var", "functest", "scanner-output")
    os.makedirs(watch, exist_ok=True)
    manifests = demo_scenario(watch, size_bytes=256 * 1024)
    assert len(manifests) == 4
    write_scan(watch, "scan-ft-ambiguous", "990000", size_bytes=64 * 1024)
    agent = AgentService(
        watch_root=watch, core_url=CORE, storage_url=STORAGE,
        token="dev-agent-token", agent_id=agent_id,
        local_db_path=os.path.join(here, "var", "functest", "agent-queue.sqlite3"),
        chunk_size=256 * 1024)
    states = {}
    for _ in range(60):
        agent.run_once()
        rows = agent.db.q(
            "SELECT state, COUNT(*) c FROM acquisition_event GROUP BY state")
        states = {r["state"]: r["c"] for r in rows}
        if states.get("UPLOADED", 0) >= 5:
            break
        time.sleep(0.5)
    assert states.get("UPLOADED", 0) == 5, f"5次扫描应全部上传，实际：{states}"

    acqs = c.get("/api/v1/acquisitions", headers=ADMIN).json()["data"]
    assert len(acqs) == 5, [a["source_scan_id"] for a in acqs]
    assert all(a["state"] == "FILES_COMPLETE" for a in acqs)
    assets = c.get("/api/v1/assets", headers=ADMIN).json()["data"]
    # 模拟器每次扫描产出2个文件（主WSI + label.jpg）→ 5次扫描=10资产
    assert len(assets) == 10 and all(a["state"] == "AVAILABLE" for a in assets), \
        [(a["original_name"], a["state"]) for a in assets]

    # ---------------- 5. 临床闭环：条码反查 → 关联 → 质量 → 冻结 → 索引
    def case_ready():
        rows = c.get("/api/v1/pathology/cases", headers=ADMIN,
                     params={"q": "9000001"}).json()["data"]
        if not rows:
            return None
        row = rows[0]
        if row["state"] == "ASSOCIATED" and row["clinically_ready"] \
                and row["slides"] == 3 and row["scans"] == 4:
            return row
        return None
    case_row = _wait(case_ready, timeout=120, msg="case 9000001 clinically ready")

    def _case_detail():
        d = c.get(f"/api/v1/pathology/cases/{case_row['id']}",
                  headers=ADMIN).json()["data"]
        return d if d.get("package_revisions") else None
    detail = _wait(_case_detail, timeout=60, msg="case frozen package")
    assert detail["package_revisions"][0]["state"] == "FROZEN"
    assert detail["patient"] is not None
    assert any(e["state"] == "CONFIRMED" for e in detail["encounters"]), \
        "已批准桥接（IN2026001→VISIT 1）应确认就诊"
    assert detail["index_records"], "批准合成规则下索引应产出"

    # ---------------- 6. 双目的地：推送受理 + 视图发布
    def subs_ready():
        subs = c.get("/api/v1/submissions", headers=ADMIN).json()["data"]
        push = [x for x in subs if x["destination"] == "mock-push"]
        return push and all(x["state"] in ("ACCEPTED", "CONFIRMED") for x in push), subs
    _, subs = _wait(subs_ready, timeout=90, msg="push submissions accepted")
    push_sub = next(x for x in subs if x["destination"] == "mock-push")
    # ACCEPTED（同步受理回执）≠ 业务确认：病例 target_confirmed 必须仍为 False
    assert push_sub["state"] == "ACCEPTED", push_sub
    detail = c.get(f"/api/v1/pathology/cases/{case_row['id']}",
                   headers=ADMIN).json()["data"]
    assert detail["case"]["target_confirmed"] is False, \
        "ACCEPTED回执不得推进业务确认"

    pubs = c.get("/api/v1/publications", headers=ADMIN).json()["data"]
    pub = next(p for p in pubs if p["state"] == "PUBLISHED")
    assert pub["items"] >= 1, "发布应落PublicationItem成员"
    ev = c.get(f"/api/v1/publications/{pub['id']}/evidence",
               headers=ADMIN).json()["data"]
    assert ev["data_visible"] is True
    assert ev["business_confirmed"] is False
    assert ev["download_evidence"] == [], "下载前不得有下载证据"

    # ---------------- 7. 交换网关：视图行 + 文件下载协议
    gen = x.get("/exchange/v1/generations/current", headers=COLLECTOR).json()["data"]
    assert gen and gen["row_count"] >= 2
    rows = x.get("/exchange/v1/datasets/chk_info", headers=COLLECTOR).json()["data"]["rows"]
    assert any(r["payload"].get("pathologyNo") == "9000001" for r in rows)
    arows = x.get("/exchange/v1/datasets/asset_info",
                  headers=COLLECTOR).json()["data"]["rows"]
    assert arows, "asset_info应有路由行"
    route_id = arows[0]["payload"]["routeId"]
    head = x.head(f"/exchange/v1/files/{route_id}", headers=COLLECTOR)
    assert head.status_code == 200 and "ETag" in head.headers
    total = int(head.headers["Content-Length"])
    r206 = x.get(f"/exchange/v1/files/{route_id}", headers={
        **COLLECTOR, "Range": f"bytes=0-{min(1023, total - 1)}"})
    assert r206.status_code == 206 and r206.headers["Content-Range"].startswith("bytes 0-")
    r200 = x.get(f"/exchange/v1/files/{route_id}", headers=COLLECTOR)
    assert r200.status_code == 200 and len(r200.content) == total
    rim = x.get(f"/exchange/v1/files/{route_id}", headers={
        **COLLECTOR, "Range": "bytes=0-2", "If-Range": '"stale-etag"'})
    assert rim.status_code == 200 and len(rim.content) == total, "If-Range错配应回全量"
    rmu = x.get(f"/exchange/v1/files/{route_id}", headers={
        **COLLECTOR, "Range": "bytes=0-1,3-4"})
    assert rmu.status_code == 200, "合法多段按设计忽略回全量"
    ev = c.get(f"/api/v1/publications/{pub['id']}/evidence",
               headers=ADMIN).json()["data"]
    assert ev["download_evidence"], "下载后应有证据"
    assert all(d["result"] in ("COMPLETE", "PARTIAL") for d in ev["download_evidence"])
    assert ev["business_confirmed"] is False, "下载不等于业务确认"

    # ---------------- 8. 回执精确关联（R02）：无关回执不串证据
    r = x.post("/exchange/v1/receipts", headers=COLLECTOR, json={
        "receipt_id": "FT-UNRELATED", "result": "CONFIRMED",
        "publication_id": "not-this-publication"})
    assert r.status_code == 200
    ev = c.get(f"/api/v1/publications/{pub['id']}/evidence",
               headers=ADMIN).json()["data"]
    assert ev["business_confirmed"] is False, "他人/无关回执不得计入本发布证据"
    # 精确指向本发布的CONFIRMED → 证据成立（仅视图发布口径）
    r = x.post("/exchange/v1/receipts", headers=COLLECTOR, json={
        "receipt_id": "FT-PUB-CONF", "result": "CONFIRMED",
        "publication_id": pub["id"]})
    assert r.status_code == 200
    ev = c.get(f"/api/v1/publications/{pub['id']}/evidence",
               headers=ADMIN).json()["data"]
    assert ev["business_confirmed"] is True

    # 推送提交的业务确认：CONFIRMED回执 → submission CONFIRMED + case.target_confirmed
    r = x.post("/exchange/v1/receipts", headers=COLLECTOR, json={
        "receipt_id": "FT-PUSH-CONF", "result": "CONFIRMED",
        "scope": "mock-push", "submission_key": push_sub["idempotency_key"]})
    assert r.status_code == 200
    def push_confirmed():
        cur = c.get("/api/v1/submissions", headers=ADMIN).json()["data"]
        cur_sub = next(y for y in cur if y["id"] == push_sub["id"])
        return cur_sub["state"] == "CONFIRMED" and \
            c.get(f"/api/v1/pathology/cases/{case_row['id']}",
                  headers=ADMIN).json()["data"]["case"]["target_confirmed"] is True
    _wait(push_confirmed, timeout=30, msg="push submission confirmed + target_confirmed")

    # ---------------- 9. 条码歧义 + 人工绑定CAS（428/412/200）
    acq_amb = next(a for a in acqs if a["barcode_raw"] == "990000")
    cands = c.get(f"/api/v1/pathology/acquisitions/{acq_amb['id']}"
                  f"/association-candidates", headers=REVIEWER).json()["data"]
    assert len(cands["candidates"]) >= 2, "990000应有两候选"
    v0 = cands["decision_version"]
    body_bind = {"entity_ref": f"acquisition:{acq_amb['id']}",
                 "action": "bind_slide",
                 "after": {"barcode_raw": "990000", "source_slide_id": "4"},
                 "reason": "功能测试：核对蜡块标签后选Id=4"}
    assert c.post("/api/v1/manual-decisions", json=body_bind,
                  headers=REVIEWER).status_code == 428, "缺If-Match必须428"
    stale = dict(body_bind, after={"barcode_raw": "990000", "source_slide_id": "5"})
    assert c.post("/api/v1/manual-decisions", json=stale,
                  headers={**REVIEWER, "If-Match": str(v0 + 99)}).status_code == 412, \
        "过期版本必须412"
    ok = c.post("/api/v1/manual-decisions", json=body_bind,
                headers={**REVIEWER, "If-Match": str(v0)})
    assert ok.status_code == 200, ok.text
    assert c.post("/api/v1/manual-decisions", json=stale,
                  headers={**REVIEWER, "If-Match": str(v0)}).status_code == 412, \
        "重放同版本必须412（竞争失败方无副作用）"

    def bound():
        d = c.get("/api/v1/pathology/cases", headers=ADMIN,
                  params={"q": "9000002"}).json()["data"]
        return d and d[0]["slides_linked"] and d[0]["state"] == "ASSOCIATED"
    _wait(bound, timeout=60, msg="case 9000002 manually bound")
    d2 = c.get("/api/v1/pathology/cases", headers=ADMIN,
               params={"q": "9000002"}).json()["data"][0]
    # P0002证件号PIS/HIS冲突（合成夹具F07）→ 病例已关联但临床不就绪、阻断问题在列
    assert d2["clinically_ready"] is False, "身份冲突必须阻断临床就绪"
    assert any(q["rule_code"] == "QF-ID-CONFLICT" and q["state"] == "OPEN"
               for q in c.get(f"/api/v1/pathology/cases/{d2['id']}",
                              headers=ADMIN).json()["data"]["quality_issues"])

    # ---------------- 10. 异步Operation终态（barcode反查）
    r = c.post("/api/v1/pathology/barcode-resolutions",
               json={"acquisition_id": acq_amb["id"]},
               headers={**ADMIN, "Idempotency-Key": "ft-resolve-1"})
    assert r.status_code == 202
    op_id = r.json()["data"]["operation_id"]

    def op_done():
        d = c.get(f"/api/v1/operations/{op_id}", headers=ADMIN).json()["data"]
        return d["state"] in ("SUCCEEDED", "FAILED") and d
    op = _wait(op_done, timeout=60, msg="barcode resolution operation")
    assert op["state"] == "SUCCEEDED" and op["result"].get("resolve"), op

    # ---------------- 11. metrics导入：幂等 + 错误行
    csv_ok = "机构,月份,项目,数量,金额\nYLV21051,2026-09,CT,3,120.50\n"
    imp = c.post("/api/v1/imports", json={"csv": csv_ok, "business_key": "ft-monthly"},
                 headers={**ADMIN, "Idempotency-Key": "ft-import-1"})
    assert imp.status_code == 200, imp.text
    pkg_id = imp.json()["data"]["package_id"]
    imp2 = c.post("/api/v1/imports", json={"csv": csv_ok, "business_key": "ft-monthly"},
                  headers={**ADMIN, "Idempotency-Key": "ft-import-1"})
    assert imp2.status_code == 200 and imp2.json()["data"]["package_id"] == pkg_id, \
        "同键同内容必须幂等"
    conf = c.post("/api/v1/imports", json={"csv": "机构,月份,项目,数量,金额\nY,2026-09,X,1,bad\n"},
                  headers=ADMIN, params={})
    prev = c.post("/api/v1/imports/preview", json={"csv": "机构,月份,项目,数量,金额\nY,2026-09,X,1,bad\n"},
                  headers=ADMIN)
    assert prev.status_code == 200 and prev.json()["data"]["row_errors"], \
        "错误行必须可见（不静默丢弃）"

    def metrics_run_done():
        body = c.get("/api/v1/workflow-runs", headers=ADMIN,
                     params={"workflow_key": "metrics.mock_push"}).json()["data"]
        runs = body["items"] if isinstance(body, dict) else body
        return runs and any(r["state"] == "SUCCEEDED" for r in runs)
    _wait(metrics_run_done, timeout=60, msg="metrics push run succeeded")

    # ---------------- 12. ConfigRelease闭包 + 对账 + 审计
    rel = c.post("/api/v1/config-releases", json={"note": "功能测试发布"},
                 headers=ADMIN)
    assert rel.status_code in (200, 201), rel.text
    rel_body = rel.json()["data"]
    assert rel_body.get("release_id") and rel_body.get("entries"), rel_body
    assert "dictionary" in rel_body["entry_types"], "闭包必须包含字典"
    rlist = c.get("/api/v1/config-releases", headers=ADMIN).json()["data"]
    items = rlist["items"] if isinstance(rlist, dict) else rlist
    assert any(r["id"] == rel_body["release_id"] for r in items),         "发布后的release必须可查询"

    recon = c.post("/api/v1/reconciliations", json={"destination": "mock-push"},
                   headers=ADMIN)
    assert recon.status_code == 200, recon.text
    assert recon.json()["data"]["summary"]["checked"] >= 1

    audit = c.get("/api/v1/audit", headers=ADMIN, params={"limit": 200}).json()["data"]
    actions = {a["action"] for a in audit}
    assert "agent.enroll" in actions and "agent.credential.rotate" in actions, \
        f"enroll/轮换必须留审计：{sorted(actions)}"
    assert "asset.download" in actions or "exchange.file.download" in actions, \
        "文件下载必须留审计"

    # ---------------- 13. 越权/不存在资源
    assert c.get("/api/v1/operations/does-not-exist", headers=ADMIN).status_code == 404
    assert c.get("/api/v1/submissions", headers={"Authorization": "Bearer nope"}).status_code == 401

    # ---------------- 14. UI托管 + 跨源预检
    ui = c.get("/ui/")
    assert ui.status_code == 200
    import re
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', ui.text)
    assert scripts and scripts[0].startswith("/ui/"), scripts
    assert c.get(scripts[0]).status_code == 200
    pre = s.options("/api/v1/storage/volumes", headers={
        "Origin": "http://127.0.0.1:8000", "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization"})
    assert pre.status_code in (200, 204) and \
        pre.headers.get("access-control-allow-origin") == "http://127.0.0.1:8000"

    c.close(); s.close(); x.close(); m.close()
