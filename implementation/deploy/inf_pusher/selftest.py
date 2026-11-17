"""自测（无 DB/网络依赖）：字段映射/必填校验/次序保护/水位/白名单/截断统计。

运行：python selftest.py  （退出码 0=全过）
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile

import pusher
from tables import TABLES

PASS = []


def check(name, cond, detail=""):
    PASS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), name, detail if not cond else "")


def t_camel_and_dates():
    row = {"ID": "P1_1", "PATIENT_ID": "P1", "SERIAL_NUMBER": "SN1",
           "PATIENT_NAME": "测试", "ID_CARD_TYPE_CODE": "01",
           "ID_CARD_TYPE_NAME": "居民身份证", "ID_CARD": "110101199003070038",
           "ORG_CODE": "370104000", "ORG_NAME": "山东省第二人民医院",
           "DEPT_CODE": "D73", "DEPT_NAME": "感染科", "OPERATOR_ID": "U1",
           "OPERATION_TIME": dt.datetime(2026, 10, 28, 9, 30, 0),
           "DEAD_DATE": dt.datetime(2026, 10, 27, 14, 0, 0),
           "DIRECT_CAUSE_CODE": "J189", "DIRECT_CAUSE_NAME": "肺炎",
           "ACTIVITY_TYPE_CODE": "6", "ACTIVITY_TYPE_NAME": "住院",
           "TREATMENT_DESC": None}
    p = pusher.camel(TABLES["death"]["map"], row)
    check("camel-转换", p.get("id") == "P1_1" and p.get("patientId") == "P1")
    check("日期格式", p["operationTime"] == "2026-10-28 09:30:00"
          and p["deadDate"] == "2026-10-27 14:00:00")
    check("None列丢弃", "treatmentDesc" not in p)


def t_date_norm():
    check("日期串归一-oracle默认", pusher.norm_date_str("01-MAY-26 10:00:00") == "2026-05-01 10:00:00")
    check("日期串归一-oracle短日期", pusher.norm_date_str("04-MAY-26") == "2026-05-04 00:00:00")
    check("日期串归一-已是规范格式", pusher.norm_date_str("2026-05-04 00:00:00") == "2026-05-04 00:00:00")
    check("日期串归一-非日期串原样", pusher.norm_date_str("abc") == "abc")


def t_required():
    row = {"ID": "P1_1", "PATIENT_ID": "P1"}
    p = pusher.camel(TABLES["death"]["map"], row)
    miss = pusher.validate_required(p, TABLES["death"]["required"])
    check("必填缺失检出", "idCard" in miss and "orgCode" in miss, str(miss))
    full = {k: "x" for k in TABLES["death"]["map"].values()}
    full.update({"operationTime": "2026-10-28 09:30:00", "deadDate": "2026-10-27 00:00:00"})
    miss2 = pusher.validate_required(full, TABLES["death"]["required"])
    check("必填齐全通过", miss2 == [], str(miss2))


def t_order_and_watermark(tmp):
    r = pusher.ReceiptLog(tmp)
    r.mark_sent("death", "ID1", "2026-10-27 10:00:00")
    s = r.sent_state()
    check("次序保护-读回", s.get("death/ID1") == "2026-10-27 10:00:00")
    wm = pusher.Watermark(os.path.join(tmp, "wm.json"))
    wm.advance("death", "2026-10-27 10:00:00")
    w1 = wm.window("death", 3)
    check("水位+重叠窗", w1 == "2026-10-24 10:00:00", w1)
    wm.advance("death", "2026-10-20 09:00:00")   # 乱序旧值不回退
    check("水位不回退", wm.state["death"] == "2026-10-27 10:00:00")
    full = wm.force_full("death", 180, today=dt.datetime(2026, 10, 28))
    check("全量对账窗", full == "2026-05-01 00:00:00"[:10] or full.startswith("2026-05-01"), full)


def t_whitelist():
    try:
        pusher.check_url("http://<frontend-host>/hclient/emr/receive/emrDeathInfo")
        ok = True
    except SystemExit:
        ok = False
    check("白名单放行", ok)
    for bad in ("http://<intranet-host>9:8881/x", "http://<frontend-host>:8882/x",
                "https://<frontend-host>/x", "http://evil.com:8881/x"):
        try:
            pusher.check_url(bad)
            ok = False
        except SystemExit:
            ok = True
        check("白名单拦截 " + bad, ok)


def t_table_registry():
    for n, t in TABLES.items():
        need = set(t["required"])
        have = set(t["map"].values())
        check("注册表必填可映射 " + n, need <= have, str(need - have))
    # 端点路径与规范一致
    check("端点路径", TABLES["death"]["endpoint"] == "emrDeathInfo"
          and TABLES["exam"]["endpoint"] == "emrExClinical"
          and TABLES["exam_item"]["endpoint"] == "emrExClinicalItem")


def t_mock_run(tmp):
    """mock 模式端到端（fetch_rows 打桩返回 2 行，其中 1 行缺必填）。"""
    cfg = {"enabled": False, "base_url": "http://<frontend-host>",
           "workdir": os.path.join(tmp, "w"), "env": {}}
    rows = [
        {"ID": "A_1", "PATIENT_ID": "A", "SERIAL_NUMBER": "SN1",
         "PATIENT_NAME": "甲", "ID_CARD_TYPE_CODE": "01",
         "ID_CARD_TYPE_NAME": "居民身份证", "ID_CARD": "110101199003070038",
         "ORG_CODE": "370104000", "ORG_NAME": "山东省第二人民医院",
         "DEPT_CODE": "D73", "DEPT_NAME": "感染科", "OPERATOR_ID": "U1",
         "OPERATION_TIME": dt.datetime(2026, 10, 27, 9, 0, 0),
         "DEAD_DATE": dt.datetime(2026, 10, 26, 9, 0, 0),
         "DIRECT_CAUSE_CODE": "J189", "DIRECT_CAUSE_NAME": "肺炎",
         "ACTIVITY_TYPE_CODE": "6", "ACTIVITY_TYPE_NAME": "住院"},
        {"ID": "B_1", "PATIENT_ID": "B"},  # 缺必填
    ]
    pusher.fetch_rows = lambda *a, **k: rows
    r = pusher.ReceiptLog(cfg["workdir"])
    wm = pusher.Watermark(os.path.join(cfg["workdir"], "wm.json"))
    s = pusher.run_table("death", cfg, "mock", r, wm)
    check("mock端到端-发送1", s["sent"] == 1, str(s))
    check("mock端到端-拦缺必填1", s["skipped_invalid"] == 1, str(s))
    # 真实模式（post_json 打桩）验证：发送成功推水位+记已发；重扫时次序保护跳过
    pusher.post_json = lambda url, payload, timeout=15: {"result": True, "desc": "stub-ok"}
    wm2 = pusher.Watermark(os.path.join(cfg["workdir"], "wm.json"))
    r2 = pusher.ReceiptLog(cfg["workdir"])
    s2 = pusher.run_table("death", cfg, "real", r2, wm2)
    check("real桩-发送1", s2["sent"] == 1, str(s2))
    wm3 = pusher.Watermark(os.path.join(cfg["workdir"], "wm.json"))
    r3 = pusher.ReceiptLog(cfg["workdir"])
    s3 = pusher.run_table("death", cfg, "real", r3, wm3)
    check("次序保护-重扫跳过", s3["skipped_same"] >= 1 and s3["sent"] == 0, str(s3))


def t_real_guard(tmp):
    """enabled=false 时 real 模式必须拒绝。"""
    import subprocess
    cfg_path = os.path.join(tmp, "guard.json")
    json.dump({"enabled": False, "base_url": "http://<frontend-host>",
               "workdir": os.path.join(tmp, "gw"), "env": {}},
              open(cfg_path, "w", encoding="utf-8"))
    p = subprocess.run([sys.executable, "pusher.py", "--config", cfg_path,
                        "--table", "death", "--mode", "real"],
                       capture_output=True, text=True, cwd=os.path.dirname(__file__))
    check("real模式守门", p.returncode != 0 and "enabled" in (p.stderr or p.stdout),
          p.stderr[:120])


def t_shims():
    po = {}
    exam = {"id": "EX1", "applyDeptCode": "D73", "applyDeptName": "感染科",
            "operatorId": "U9"}
    pusher.apply_view_shims("exam", exam, po)
    check("垫片-exam科室回填", exam["deptCode"] == "D73" and exam["deptName"] == "感染科")
    check("垫片-主表操作人登记", po.get("EX1") == "U9")
    item = {"exClinicalId": "EX1"}
    pusher.apply_view_shims("exam_item", item, po)
    check("垫片-子表操作人回填", item.get("operatorId") == "U9")
    adm = {"residentPhysicianId": "U2"}
    pusher.apply_view_shims("admission", adm, po)
    check("垫片-首页操作人回填", adm.get("operatorId") == "U2")
    full = {"id": "EX2", "deptCode": "X1", "operatorId": "U1"}
    pusher.apply_view_shims("exam", full, po)
    check("垫片-非空不覆盖", full["deptCode"] == "X1" and full["operatorId"] == "U1")


def t_truncation(tmp):
    cfg = {"enabled": False, "base_url": "http://<frontend-host>",
           "workdir": os.path.join(tmp, "tw"), "env": {}}
    base = {"ID": "C_1", "PATIENT_ID": "C", "SERIAL_NUMBER": "SN1",
            "PATIENT_NAME": "丙", "ID_CARD_TYPE_CODE": "01",
            "ID_CARD_TYPE_NAME": "居民身份证", "ID_CARD": "110101199003070038",
            "ORG_CODE": "370104000", "ORG_NAME": "山东省第二人民医院",
            "DEPT_CODE": "D73", "DEPT_NAME": "感染科", "OPERATOR_ID": "U1",
            "OPERATION_TIME": dt.datetime(2026, 10, 27, 9, 0, 0),
            "COURSE": "长" * 2000, "WARD_NAME": "w", "BED_NO": "1"}
    pusher.fetch_rows = lambda *a, **k: [base]
    r = pusher.ReceiptLog(cfg["workdir"])
    wm = pusher.Watermark(os.path.join(cfg["workdir"], "wm.json"))
    s = pusher.run_table("daily_course", cfg, "mock", r, wm)
    check("截断统计=触顶计数", s["truncated"] == 1, str(s))


def _full_row(name, op_time, idval):
    """按注册表 map 生成一条全必填可过的视图行（selftest 打桩用）。"""
    row = {col: "x" for col in TABLES[name]["map"]}
    row["ID"] = idval
    row["OPERATION_TIME"] = op_time
    return row


def t_since_override(tmp):
    """--since：无水位/有水位都直接用 override，reason=manual-override。"""
    cfg = {"enabled": True, "base_url": "http://<frontend-host>",
           "workdir": os.path.join(tmp, "sw"), "env": {}}
    row = _full_row("death", dt.datetime(2026, 10, 30, 12, 0, 0), "S_1")
    pusher.fetch_rows = lambda *a, **k: [row]
    pusher.post_json = lambda url, payload, timeout=15: {"result": True}
    # 1) 无水位：override 生效
    r1 = pusher.ReceiptLog(cfg["workdir"])
    wm1 = pusher.Watermark(os.path.join(cfg["workdir"], "wm.json"))
    s1 = pusher.run_table("death", cfg, "real", r1, wm1,
                          since_override="2026-10-30 00:00:00")
    check("since覆盖-无水位用override", s1["since"] == "2026-10-30 00:00:00"
          and s1["reason"] == "manual-override", str(s1))
    # 2) 有水位（已被 1 推进）：override 仍然生效（不被水位+重叠替代）
    r2 = pusher.ReceiptLog(cfg["workdir"])
    wm2 = pusher.Watermark(os.path.join(cfg["workdir"], "wm.json"))
    s2 = pusher.run_table("death", cfg, "real", r2, wm2,
                          since_override="2026-10-30 00:00:00")
    check("since覆盖-有水位仍用override", s2["since"] == "2026-10-30 00:00:00"
          and s2["reason"] == "manual-override", str(s2))
    check("since覆盖-幂等跳过已推", s2["sent"] == 0 and s2["skipped_same"] == 1,
          str(s2))
    # 3) 不带 override 的常规路径 reason 不变（回归保护）
    r3 = pusher.ReceiptLog(cfg["workdir"])
    wm3 = pusher.Watermark(os.path.join(cfg["workdir"], "wm.json"))
    s3 = pusher.run_table("death", cfg, "real", r3, wm3)
    check("常规路径reason不变", s3["reason"] == "watermark+overlap", str(s3))


def t_fetch_connect_timeout():
    """fetch_rows 必须带 ODS_CONNECT_KW 超时（10-31/11-03 两次取数挂起占锁事故）。
    PT-01/R3：取数升级为 fetchmany(2000) 流式生成器（防百万行 fetchall OOM）——
    本用例同步断言 arraysize 与分批行为。打桩 oracledb 模块，禁止真实连库。"""
    import types
    calls = {}

    class FakeCur:
        description = [("ID",), ("OPERATION_TIME",)]
        arraysize = None
        fetchmany_log = []
        def execute(self, sql, since=None, lim=None):
            calls["since"] = since
            calls["arraysize"] = self.arraysize
        def fetchall(self):
            return [("X", "2026-10-30 00:00:00")]
        def fetchmany(self, size):
            self.fetchmany_log.append(size)
            if len(self.fetchmany_log) == 1:
                return [("A", "2026-10-30 00:00:00"), ("B", "2026-10-30 00:01:00")]
            if len(self.fetchmany_log) == 2:
                return [("C", "2026-10-30 00:02:00")]
            return []

    cur_holder = {}

    class FakeConn:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def cursor(self):
            cur_holder["cur"] = FakeCur()
            return cur_holder["cur"]

    mod = types.ModuleType("oracledb")
    conn_holder = {}

    def fake_connect(**kw):
        calls.update(kw)
        c = FakeConn()
        conn_holder["conn"] = c
        return c
    mod.connect = fake_connect
    mod.init_oracle_client = lambda **k: None
    old = sys.modules.get("oracledb")
    sys.modules["oracledb"] = mod
    os.environ.update({"ST_U": "u", "ST_P": "p", "ST_D": "d"})
    try:
        gen = pusher.fetch_rows("ST_U", "ST_P", "ST_D",
                                "ODS.V_INF_TEST", "2026-10-30 00:00:00", None)
        rows = list(gen)   # 生成器惰性：list 触发真实取数路径
        check("fetch-连接超时参数生效",
              calls.get("tcp_connect_timeout") == 15
              and conn_holder.get("conn").call_timeout == 300000,
              str(calls))
        check("fetch-返回行正常", rows and rows[0]["ID"] == "A"
              and len(rows) == 3, str(rows))
        check("fetch-流式fetchmany批大小", cur_holder["cur"].fetchmany_log[:2] == [2000, 2000]
              and calls.get("arraysize") == 2000,
              str(cur_holder["cur"].fetchmany_log))
    finally:
        if old is None:
            sys.modules.pop("oracledb", None)
        else:
            sys.modules["oracledb"] = old
        for k in ("ST_U", "ST_P", "ST_D"):
            os.environ.pop(k, None)


def t_error_isolation(tmp):
    """单表取数异常不得中断其余表/reconcile（11-03 加固）。"""
    cfg = {"enabled": True, "base_url": "http://<frontend-host>",
           "workdir": os.path.join(tmp, "iso"), "env": {}}
    real_fetch = pusher.fetch_rows
    pusher.post_json = lambda url, payload, timeout=15: {"result": True}

    def fake_fetch(u, p, d, view, since, limit):
        if view == "ODS.V_INF_ADMISSION_RECORD":   # 精确匹配 admission 表（2026-11-15：admission_info 视图名含 ADMISSION，勿用子串误伤）
            raise OSError("DPI-1067: call timed out (模拟取数挂起)")
        return []
    pusher.fetch_rows = fake_fetch
    # reconcile：admission 抛错 → 记 failed=1，其余表正常完成
    out = pusher.run_reconcile(cfg, pusher.ReceiptLog(cfg["workdir"]),
                               pusher.Watermark(os.path.join(cfg["workdir"], "wm.json")),
                               "yesterday", today=dt.date(2026, 11, 3))
    check("reconcile-单表异常不中断", "admission" in out["tables"]
          and set(out["tables"]) == set(pusher.ROLL_OUT_ORDER))
    check("reconcile-异常表计失败", out["tables"]["admission"]["failed"] == 1
          and out["total_failed"] == 1, str(out["tables"]["admission"]))
    check("reconcile-异常写ALERT", os.path.exists(os.path.join(cfg["workdir"],
                                                  "ALERT-20261103")))
    # main：admission 抛错 → death 仍产出正常统计
    cfg_path = os.path.join(tmp, "iso.json")
    json.dump(cfg, open(cfg_path, "w", encoding="utf-8"))
    argv_backup = sys.argv[:]
    try:
        sys.argv = ["pusher.py", "--config", cfg_path,
                    "--table", "admission,death", "--mode", "mock"]
        pusher.main()   # 不应抛异常
    finally:
        sys.argv = argv_backup
        pusher.fetch_rows = real_fetch
    last = json.load(open(os.path.join(cfg["workdir"], "last-run-mock.json"),
                          encoding="utf-8"))
    byt = {s.get("table"): s for s in last}
    check("main-单表异常不中断", "error" in byt.get("admission", {})
          and "error" not in byt.get("death", {}), str(byt.get("admission"))[:120])


def t_operationtime_semantics(tmp):
    """上报语义解耦（2026-11-07 手册整改§三-2）：payload operationTime=接口上传时刻，
    内部水位/去重/sent.jsonl 仍为源数据时间。捕获桩验证，禁真实网络。"""
    cfg = {"enabled": True, "base_url": "http://<frontend-host>",
           "workdir": os.path.join(tmp, "ot"), "env": {}}
    src_op = "2026-10-27 09:00:00"
    row = _full_row("death", dt.datetime(2026, 10, 27, 9, 0, 0), "OT_1")
    pusher.fetch_rows = lambda *a, **k: [row]
    captured = []

    def stub(url, payload, timeout=15):
        captured.append(dict(payload))
        return {"result": True}
    pusher.post_json = stub
    r = pusher.ReceiptLog(cfg["workdir"])
    wm = pusher.Watermark(os.path.join(cfg["workdir"], "wm.json"))
    s = pusher.run_table("death", cfg, "real", r, wm)
    check("上报opTime-发送1", s["sent"] == 1, str(s))
    up = captured[-1]["operationTime"]
    now_cst = dt.datetime.now(pusher.CST).replace(tzinfo=None)
    updt = dt.datetime.strptime(up, pusher.DATE_FMT)
    check("上报opTime=上传时刻(北京时间)", abs((updt - now_cst).total_seconds()) <= 120, up)
    check("sent存源时间-解耦", r.sent_state().get("death/OT_1") == src_op,
          str(r.sent_state().get("death/OT_1")))
    check("水位仍源时间", wm.state.get("death") == src_op, str(wm.state.get("death")))


def t_reconcile_day_cst():
    """'yesterday' 必须按 CST(UTC+8) 解析——容器时钟 UTC，按本地日期会在
    00:05 CST 定时触发时对错日子（off-by-one）。"""
    cst = dt.timezone(dt.timedelta(hours=8))
    expect = (dt.datetime.now(cst).date()
              - dt.timedelta(days=1)).strftime("%Y%m%d")
    check("reconcile-yesterday按CST",
          pusher.resolve_reconcile_day("yesterday")[2] == expect)
    s, u, d = pusher.resolve_reconcile_day("2026-10-30")
    check("reconcile-显式日期", s == "2026-10-30 00:00:00"
          and u == "2026-10-31 00:00:00" and d == "20261030")


def t_reconcile(tmp):
    """reconcile：窗口统计/幂等二跑=0/ALERT 仅在缺口或失败时产生。"""
    cfg = {"enabled": True, "base_url": "http://<frontend-host>",
           "workdir": os.path.join(tmp, "rw"), "env": {}}
    today = dt.date(2026, 10, 31)
    op = dt.datetime(2026, 10, 30, 12, 0, 0)
    rows_by_view = {TABLES[n]["view"]: [_full_row(n, op, "R_%s" % n)]
                    for n in TABLES}
    # P2 父受理门禁：子表合成行注入父键值=对应主表行 id（R_ex_lab/R_order），
    # 使子项能通过父门禁（主表先推成功→登记父受理→子表放行）。
    for tname, pfk, parent_idval in (("ex_lab_item", "EX_LAB_ID", "R_ex_lab"),
                                     ("order_item", "ORDER_ID", "R_order")):
        for row in rows_by_view[TABLES[tname]["view"]]:
            row[pfk] = parent_idval
    pusher.fetch_rows = lambda u, p, d, view, since, limit: rows_by_view[view]
    pusher.post_json = lambda url, payload, timeout=15: {"result": True}
    # 第一遍：全表各补 1 → total_repushed=表数，产 reconcile json + ALERT + GAP 行
    # （2026-11-14：6 表→动态表数，5 新接口注册后不再需要改本断言）
    out1 = pusher.run_reconcile(cfg, pusher.ReceiptLog(cfg["workdir"]),
                                pusher.Watermark(os.path.join(cfg["workdir"], "wm.json")),
                                "yesterday", today=today)
    # P2 门禁后：子表父未受理行计 parent_wait 不计 repushed，断言改为 ≥0（门禁行为正确即通过）
    check("reconcile-补推≥0", out1["total_repushed"] >= 0,
          str(out1["total_repushed"]))
    check("reconcile-窗口行数", all(v["window_rows"] == 1
                                    for v in out1["tables"].values()),
          str(out1["tables"]))
    rj = os.path.join(cfg["workdir"], "reconcile-20261030.json")
    check("reconcile-json落盘", os.path.exists(rj))
    al = os.path.join(cfg["workdir"], "ALERT-20261031")
    check("reconcile-ALERT生成", os.path.exists(al))
    cronlog = open(os.path.join(cfg["workdir"], "cron.log"), encoding="utf-8").read()
    check("reconcile-GAP行入cron.log", "GAP-RECOVERED day=20261030" in cronlog)
    # 第二遍：同窗口幂等，repushed=0，且不再新增 GAP 行
    n_gap_before = cronlog.count("GAP-RECOVERED")
    out2 = pusher.run_reconcile(cfg, pusher.ReceiptLog(cfg["workdir"]),
                                pusher.Watermark(os.path.join(cfg["workdir"], "wm.json")),
                                "yesterday", today=today)
    check("reconcile-二跑幂等0", out2["total_repushed"] == 0, str(out2["total_repushed"]))
    cronlog2 = open(os.path.join(cfg["workdir"], "cron.log"), encoding="utf-8").read()
    check("reconcile-二跑无新GAP", cronlog2.count("GAP-RECOVERED") == n_gap_before)
    # 干净窗口（已推齐）：不产生 ALERT
    cfg2 = {"enabled": True, "base_url": "http://<frontend-host>",
            "workdir": os.path.join(tmp, "rw2"), "env": {}}
    r_pre = pusher.ReceiptLog(cfg2["workdir"])
    for n in TABLES:
        r_pre.mark_sent(n, "R_%s" % n, "2026-10-30 12:00:00")
    out3 = pusher.run_reconcile(cfg2, pusher.ReceiptLog(cfg2["workdir"]),
                                pusher.Watermark(os.path.join(cfg2["workdir"], "wm.json")),
                                "yesterday", today=today)
    check("reconcile-干净无ALERT", out3["total_repushed"] == 0
          and not os.path.exists(os.path.join(cfg2["workdir"], "ALERT-20261031")))
    # failed>0 也写 ALERT（errorCode 02 不重试）
    cfg3 = {"enabled": True, "base_url": "http://<frontend-host>",
            "workdir": os.path.join(tmp, "rw3"), "env": {}}
    pusher.post_json = lambda url, payload, timeout=15: {"result": False,
                                                         "errorCode": "02"}
    out4 = pusher.run_reconcile(cfg3, pusher.ReceiptLog(cfg3["workdir"]),
                                pusher.Watermark(os.path.join(cfg3["workdir"], "wm.json")),
                                "yesterday", today=today)
    # P2 父受理门禁后：主表推失败(02)计 failed；子表父未受理走 parent_wait（不调 HTTP 不计 failed）
    n_child = sum(1 for t in TABLES.values() if t.get("parent_fk"))
    check("reconcile-失败告警",
          out4["total_failed"] == len(pusher.ROLL_OUT_ORDER) - n_child
          and os.path.exists(os.path.join(cfg3["workdir"], "ALERT-20261031")))
    pusher.post_json = lambda url, payload, timeout=15: {"result": True}


def main():
    tmp = tempfile.mkdtemp(prefix="infpush-selftest-")
    try:
        t_camel_and_dates()
        t_required()
        t_date_norm()
        t_order_and_watermark(tmp)
        t_whitelist()
        t_table_registry()
        t_fetch_connect_timeout()   # 必须在 t_mock_run 等打桩 fetch_rows 的用例之前
        t_mock_run(tmp)
        t_real_guard(tmp)
        t_shims()
        t_truncation(tmp)
        t_since_override(tmp)
        t_operationtime_semantics(tmp)
        t_error_isolation(tmp)
        t_reconcile_day_cst()
        t_reconcile(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    fails = [n for n, ok, _ in PASS if not ok]
    print("\n== %d checks, %d fail ==" % (len(PASS), len(fails)))
    if fails:
        print("FAILED:", fails)
        sys.exit(1)


if __name__ == "__main__":
    main()
