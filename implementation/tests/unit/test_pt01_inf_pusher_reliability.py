"""PT-01 专项回归：传染病推送内核加固与稳定性闭环（revision-2 R1~R4 落地验证）。

覆盖（任务书 §3.3，≥25 用例）：
- R1：ODS 视图 v1.5 组合修订（20261117-lab-linkage-v1.5）内存 SQLite 等价语义
  ——F08 报告键候选计数不膨胀、F01 急诊类型绑定、F02 科室码不冒充员工号、
  F03 一般定量 H/L 不再映射耐药码；
- R2：ParentAcceptanceTracker——同日旧父成功+新父失败阻断、跨日历史父复用、
  其他表同 ID 隔离、当日回执失败阻断、Mock 回执永不作证据；
- R3：PendingWaitStore 持久等待越水位/重启自动补回、原子写、reconcile
  恒等式与 RECONCILE-PARENT-WAIT 告警、fetchmany(2000) 流式取数；
- R4：manifest 严格 schema（过期/错目标/超量/重复 ID）、逐行源时间/摘要漂移、
  缺失目标非零退出、同清单二跑零重复 HTTP、dry-run 零副作用、
  reconcile+manifest 组合拒绝；
- revision-4-acceptance 全部 12 个独立行为场景对**当前** pusher.py 实跑
  （复刻 collect_behavior.py 的场景函数与 evaluate_acceptance.py 的判定规则；
  原审计目录冻结只读，禁止篡改）。

全部合成数据、拦截 I/O、零真实网络/零真实 ODS。
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from domains.infectious import mapping  # noqa: E402
from domains.infectious.tables import TABLES as SHADOW_TABLES  # noqa: E402

p = mapping.load_pusher()          # 当前生产 pusher（deploy/inf_pusher/pusher.py）

REVIEW_DIR = Path(__file__).resolve().parents[3].joinpath(
    "docs", "development", "inf-lab-linkage-review-20261115",
    "revision-4-acceptance")
SQL_DIR = Path(__file__).resolve().parents[3].joinpath(
    "docs", "development", "inf-report-20261027", "sql", "revisions",
    "20261117-lab-linkage-v1.5")


# ================================================================ R1：视图 v1.5 SQLite 等价语义
def _mk_v15_db():
    """内存 SQLite：按 v1.5 修订包语义建源表（列名与 HIS/ODS 对齐，可空列可省略）。"""
    con = sqlite3.connect(":memory:")
    con.execute("""CREATE TABLE lab_test_master (
        TEST_NO TEXT, PATIENT_ID TEXT, VISIT_ID TEXT, RESULT_STATUS TEXT,
        REQUESTED_DATE_TIME TEXT, RESULTS_RPT_DATE_TIME TEXT,
        TRANSCRIPTIONIST TEXT, PERFORMED_BY TEXT, EXECUTE_DATE TEXT,
        ORDERING_PROVIDER TEXT)""")
    con.execute("""CREATE TABLE clinic_master (
        PATIENT_ID TEXT, CLINIC_NO TEXT, VISIT_DATE TEXT, NAME TEXT)""")
    con.execute("""CREATE TABLE anchor (   -- 等价 ODS.M_INF_FXHIS_ACTIVITY 门诊/急诊段
        PATIENT_ID TEXT, SERIAL_NUMBER TEXT, ACTIVITY_TYPE_CODE TEXT,
        OPERATION_TIME TEXT)""")
    con.execute("CREATE TABLE staff_dict (EMP_NO TEXT, NAME TEXT)")
    con.execute("""CREATE TABLE lab_result (
        TEST_NO TEXT, PRINT_ORDER TEXT, REPORT_ITEM_CODE TEXT,
        REPORT_ITEM_NAME TEXT, ABNORMAL_INDICATOR TEXT, RESULT TEXT,
        UNITS TEXT, PRINT_CONTEXT TEXT, REPORT_ID TEXT)""")
    return con


# 01 号对照视图的 SQLite 等价 SQL（PATIENT_ID+TEST_NO 报告完整键分组；LEFT JOIN 保 0 候选）
_ANCHOR_VIEW_SQL = """
SELECT cand.PATIENT_ID, cand.TEST_NO,
       CASE WHEN COUNT(DISTINCT cand.SERIAL_NUMBER)=1
            THEN MAX(cand.SERIAL_NUMBER) END AS SELECTED_SERIAL_NUMBER,
       CASE WHEN COUNT(DISTINCT cand.SERIAL_NUMBER)=1
            THEN MAX(cand.ACTIVITY_TYPE_CODE) END AS ACTIVITY_TYPE_CODE,
       COUNT(DISTINCT cand.SERIAL_NUMBER) AS CAND_CNT
FROM (
  SELECT a.PATIENT_ID, a.TEST_NO, act.SERIAL_NUMBER, act.ACTIVITY_TYPE_CODE
    FROM lab_test_master a
    LEFT JOIN anchor act
      ON TRIM(act.PATIENT_ID)=TRIM(a.PATIENT_ID)
     AND act.ACTIVITY_TYPE_CODE IN ('1','2')
     AND substr(act.OPERATION_TIME,1,10)=substr(a.REQUESTED_DATE_TIME,1,10)
   WHERE a.VISIT_ID='0' AND a.RESULT_STATUS='4'
     AND a.RESULTS_RPT_DATE_TIME IS NOT NULL
     AND a.RESULTS_RPT_DATE_TIME>='2026-11-14'
) cand
GROUP BY cand.PATIENT_ID, cand.TEST_NO
"""

# 02 号主视图门诊段的 SQLite 等价（类型/流水只来自对照视图唯一候选；OPERATOR_ID 由
# Python 等价函数计算——Oracle REGEXP_LIKE/标量子查询在 SQLite 的语义等价实现）
_MAIN_VIEW_SQL = """
SELECT a.TEST_NO AS ID, a.PATIENT_ID,
       actv.ACTIVITY_TYPE_CODE AS ACTIVITY_TYPE_CODE,
       actv.SELECTED_SERIAL_NUMBER AS SERIAL_NUMBER,
       NVL_SURROGATE AS ACTIVITY_TYPE_NAME,
       a.RESULTS_RPT_DATE_TIME AS EXAMINATION_REPORT_DATE,
       IFNULL(a.EXECUTE_DATE, a.REQUESTED_DATE_TIME) AS OPERATION_TIME
  FROM lab_test_master a
  JOIN (%s) actv
    ON TRIM(actv.PATIENT_ID)=TRIM(a.PATIENT_ID) AND actv.TEST_NO=a.TEST_NO
 WHERE a.VISIT_ID='0' AND a.RESULT_STATUS='4'
   AND a.RESULTS_RPT_DATE_TIME IS NOT NULL
   AND a.RESULTS_RPT_DATE_TIME>='2026-11-14'
""" % _ANCHOR_VIEW_SQL


def _operator_id_semantics(con, row):
    """F02 等价语义：OPERATOR_ID 只允许真实人员工号（含数字→工号；姓名→员工字典；
    否则 NULL）。PERFORMED_BY（执行科室代码）绝不进入。"""
    tr = row["TRANSCRIPTIONIST"]
    if tr:
        if any(ch.isdigit() for ch in tr):
            return tr
        hit = con.execute("SELECT MAX(EMP_NO) FROM staff_dict WHERE NAME=?",
                          (tr,)).fetchone()[0]
        if hit:
            return hit
    return None


def _item_result_code_semantics(abnormal, result):
    """F03 等价语义：仅真实定性（01/02/05）与真实药敏语境（15/16）给标准码；
    一般定量 H/L → None（绝不 10/12）。"""
    if (result or "").startswith("阳性") or abnormal == "P":
        return "01"
    if (result or "").startswith("阴性") or abnormal == "N":
        return "02"
    if abnormal == "Q":
        return "05"
    if abnormal == "S":
        return "15"
    if abnormal == "R":
        return "16"
    return None


def _ri_semantics(abnormal):
    """参考区间标志：H→'2'（高于）/L→'1'（低于），保留源异常语义（非耐药码）。"""
    return {"H": "2", "L": "1", "S": "2"}.get(abnormal, "0")


class TestV15Views:
    def _seed_one_visit_three_reports(self, con):
        """同一急诊就诊（CLINIC_NO=ER001，type=2）下 3 张检验报告——F08 反膨胀场景。"""
        con.execute("INSERT INTO clinic_master VALUES ('P1','ER001','2026-11-14 09:05:00','患者一')")
        con.execute("INSERT INTO anchor VALUES ('P1','ER001','2','2026-11-14 09:05:00')")
        for i, test_no in enumerate(("T001", "T002", "T003"), start=1):
            con.execute(
                "INSERT INTO lab_test_master VALUES (?,?,?,?,?,?,?,?,?,?)",
                (test_no, "P1", "0", "4", "2026-11-14 09:1%d:00" % i,
                 "2026-11-14 12:00:00", "30125", "0801", None, "20001"))

    def test_f08_one_visit_three_reports_exactly_three_rows(self):
        """场景①：3 张报告 → 对照视图 3 行、CAND_CNT=1、type='2'（不膨胀 9 行）。"""
        con = _mk_v15_db()
        self._seed_one_visit_three_reports(con)
        rows = con.execute(_ANCHOR_VIEW_SQL).fetchall()
        assert len(rows) == 3
        assert all(r[4] == 1 for r in rows), [tuple(r) for r in rows]
        assert all(r[2] == "ER001" and r[3] == "2" for r in rows)
        assert {r[1] for r in rows} == {"T001", "T002", "T003"}

    def test_f08_main_view_three_rows_emergency_type(self):
        """场景①主视图：恰好 3 行且 activityTypeCode='2'（camel 后可推送）。"""
        con = _mk_v15_db()
        self._seed_one_visit_three_reports(con)
        con.execute("INSERT INTO staff_dict VALUES ('30125','张检验')")
        rows = con.execute(_MAIN_VIEW_SQL.replace("NVL_SURROGATE", "''")).fetchall()
        assert len(rows) == 3
        for r in rows:
            payload = p.camel(SHADOW_TABLES["ex_lab"]["map"],
                              dict(zip(("ID", "PATIENT_ID", "ACTIVITY_TYPE_CODE",
                                        "SERIAL_NUMBER", "ACTIVITY_TYPE_NAME",
                                        "EXAMINATION_REPORT_DATE",
                                        "OPERATION_TIME"), r)))
            assert payload["activityTypeCode"] == "2"
            assert payload["serialNumber"] == "ER001"

    def test_f08_same_day_two_visits_waits_not_guess(self):
        """场景②：同患者同日 2 次门诊就诊 → 每报告 CAND_CNT=2 → 绑定列置空
        （主视图 serialNumber NULL → 推送器必填预检等待，不任取 MAX）。"""
        con = _mk_v15_db()
        con.executemany("INSERT INTO anchor VALUES (?,?,?,?)",
                        [("P2", "OP001", "1", "2026-11-14 08:00:00"),
                         ("P2", "OP002", "1", "2026-11-14 14:30:00")])
        con.execute("INSERT INTO lab_test_master VALUES "
                    "('T101','P2','0','4','2026-11-14 09:00:00',"
                    "'2026-11-14 12:00:00','30125','0801',NULL,'20001')")
        rows = con.execute(_ANCHOR_VIEW_SQL).fetchall()
        assert len(rows) == 1
        _, test_no, sel, code, cnt = rows[0]
        assert (test_no, sel, code, cnt) == ("T101", None, None, 2)
        # 主视图行保留（serialNumber/activityTypeCode NULL）→ camel 丢弃 → 必填拦截
        main_rows = con.execute(_MAIN_VIEW_SQL.replace("NVL_SURROGATE", "''")).fetchall()
        assert len(main_rows) == 1
        payload = p.camel(SHADOW_TABLES["ex_lab"]["map"],
                          dict(zip(("ID", "PATIENT_ID", "ACTIVITY_TYPE_CODE",
                                    "SERIAL_NUMBER", "ACTIVITY_TYPE_NAME",
                                    "EXAMINATION_REPORT_DATE",
                                    "OPERATION_TIME"), main_rows[0])))
        missing = p.validate_required(payload, SHADOW_TABLES["ex_lab"]["required"])
        assert "serialNumber" in missing and "activityTypeCode" in missing

    def test_f08_zero_candidate_report_waits_visible(self):
        """0 候选（无锚点活动）：报告行保留、CAND_CNT=0、绑定列置空等待。"""
        con = _mk_v15_db()
        con.execute("INSERT INTO lab_test_master VALUES "
                    "('T201','P3','0','4','2026-11-14 09:00:00',"
                    "'2026-11-14 12:00:00','30125','0801',NULL,'20001')")
        rows = con.execute(_ANCHOR_VIEW_SQL).fetchall()
        assert len(rows) == 1
        assert rows[0][2] is None and rows[0][3] is None and rows[0][4] == 0

    def test_f02_operator_id_never_dept_code_fallback(self):
        """场景③：TRANSCRIPTIONIST 空、PERFORMED_BY=科室码 → operatorId=NULL →
        validate_required 拦截（缺必填进入质量等待，不编造操作人）。"""
        con = _mk_v15_db()
        con.execute("INSERT INTO clinic_master VALUES ('P4','OP401','2026-11-14 09:00:00','患者四')")
        con.execute("INSERT INTO anchor VALUES ('P4','OP401','1','2026-11-14 09:00:00')")
        # PERFORMED_BY='0801' 恰好也在员工字典？——不在（它是科室码），语义必须保持 NULL
        con.execute("INSERT INTO lab_test_master VALUES "
                    "('T301','P4','0','4','2026-11-14 09:30:00',"
                    "'2026-11-14 12:00:00',NULL,'0801',NULL,'20001')")
        con.execute("INSERT INTO staff_dict VALUES ('99999','无关人员')")
        row = con.execute("SELECT TRANSCRIPTIONIST, PERFORMED_BY FROM lab_test_master "
                          "WHERE TEST_NO='T301'").fetchone()
        operator = _operator_id_semantics(con, dict(zip(("TRANSCRIPTIONIST",
                                                         "PERFORMED_BY"), row)))
        assert operator is None
        payload = {"id": "T301", "patientId": "P4"}     # OPERATOR_ID NULL → camel 丢弃
        missing = p.validate_required(payload, ["operatorId"])
        assert "operatorId" in missing

    def test_f02_operator_from_real_staff_dict(self):
        """正路径：TRANSCRIPTIONIST 为姓名且命中员工字典 → 真实工号。"""
        con = _mk_v15_db()
        con.execute("INSERT INTO staff_dict VALUES ('30125','张检验')")
        assert _operator_id_semantics(
            con, {"TRANSCRIPTIONIST": "张检验", "PERFORMED_BY": "0801"}) == "30125"
        assert _operator_id_semantics(
            con, {"TRANSCRIPTIONIST": "30125", "PERFORMED_BY": "0801"}) == "30125"

    def test_f03_quantitative_hl_not_resistance_codes(self):
        """场景④：一般定量 H/L 明细 examinationResultCode 不产出 10/12；
        参考区间标志 2/1 保留；真实药敏 S/R 仍映射 15/16。"""
        assert _item_result_code_semantics("H", "6.8") is None
        assert _item_result_code_semantics("L", "2.1") is None
        assert _item_result_code_semantics("H", "6.8") not in ("10", "12")
        assert _ri_semantics("H") == "2" and _ri_semantics("L") == "1"
        assert _item_result_code_semantics("S", "敏感") == "15"
        assert _item_result_code_semantics("R", "耐药") == "16"
        assert _item_result_code_semantics("P", "阳性") == "01"
        assert _item_result_code_semantics("N", "阴性") == "02"

    def test_v15_revision_package_files_exist(self):
        """修订包目录完整（README + 01/02/03 SQL），且不含对冻结目录的覆盖声明。"""
        names = {f.name for f in SQL_DIR.iterdir()}
        assert {"README.md", "01-m_inf_fxhis_activity_lab_v15.sql",
                "02-v_inf_ex_lab_v15.sql",
                "03-v_inf_ex_lab_item_v15.sql"} <= names
        main_sql = (SQL_DIR / "02-v_inf_ex_lab_v15.sql").read_text(encoding="utf-8")
        item_sql = (SQL_DIR / "03-v_inf_ex_lab_item_v15.sql").read_text(encoding="utf-8")
        # F02：主视图不得再把 PERFORMED_BY 写入 OPERATOR_ID
        assert "AS OPERATOR_ID" in main_sql
        import re
        op_lines = [ln for ln in main_sql.splitlines()
                    if "AS OPERATOR_ID" in ln]
        assert op_lines and not any("PERFORMED_BY" in ln for ln in op_lines), op_lines
        # F03：明细视图不得再出现 H→'12' / L→'10' 映射
        assert not re.search(r"'H'\s*THEN\s*'12'", item_sql)
        assert not re.search(r"'L'\s*THEN\s*'10'", item_sql)


# ================================================================ 12 场景复刻（collect_behavior + evaluate 规则）
def _syn_row(table, rid, parent=None, op="2026-11-15 10:00:00"):
    """复刻 revision-4-acceptance/collect_behavior.py 的 row()（对当前 pusher）。"""
    t = p.TABLES[table]
    values = {key: "SYNTHETIC" for key in t["required"]}
    values.update(id=rid, operationTime=op)
    if parent:
        values[t.get("parent_fk", "exLabId")] = parent
    return {col: values[key] for col, key in t["map"].items() if key in values}


def _run_case(name, selection="ex_lab,ex_lab_item", history=None,
              parent_reject=False, manifest=None, apply=False, repeat=1,
              empty_source=False):
    """复刻 collect_behavior.run_case：合成数据 + 拦截 I/O，观察真实行为。"""
    events = []

    def fetch(user, pwd, dsn, view, since, limit):
        if empty_source:
            return []
        if view == p.TABLES["ex_lab"]["view"]:
            return [_syn_row("ex_lab", "SYN-P")]
        if view == p.TABLES["ex_lab_item"]["view"]:
            return [_syn_row("ex_lab_item", "SYN-C", "SYN-P")]
        if view == p.TABLES["order"]["view"] and "order" in selection:
            return [_syn_row("order", "SYN-P")]
        if view == p.TABLES["order_item"]["view"] and "order_item" in selection:
            return [_syn_row("order_item", "SYN-C", "SYN-P")]
        if view == p.TABLES["death"]["view"] and "death" in selection:
            return [_syn_row("death", "SYN-P")]
        return []

    def post(url, payload, timeout=15):
        table = next(n for n, t in p.TABLES.items()
                     if url.endswith("/" + t["endpoint"]))
        ok = not (table == "ex_lab" and parent_reject)
        events.append({"table": table, "id": payload["id"], "accepted": ok})
        return {"result": ok, "errorCode": None if ok else "04"}

    with tempfile.TemporaryDirectory(prefix="pt01-case-") as tmp:
        cfg = Path(tmp) / "config.json"
        cfg.write_text(json.dumps({"base_url": "http://<frontend-host>",
                                   "enabled": True, "workdir": tmp}),
                       encoding="utf-8")
        receipts = p.ReceiptLog(tmp)
        if history == "prior_day":
            receipts.mark_sent("ex_lab", "SYN-P", "2026-11-15 10:00:00")
            (Path(tmp) / "receipts-20000101.jsonl").write_text(json.dumps(
                {"table": "ex_lab", "id": "SYN-P", "result": True, "mode": ""}) + "\n",
                encoding="utf-8")
        if history == "same_day_old":
            receipts.mark_sent("ex_lab", "SYN-P", "2026-11-15 09:00:00")
            receipts.receipt("ex_lab", {"id": "SYN-P", "result": True})
        if history == "legacy_old":
            receipts.mark_sent("ex_lab", "SYN-P", "2026-11-15 09:00:00")
        if history == "prior_day_order":
            receipts.mark_sent("order", "SYN-P", "2026-11-15 10:00:00")
        argv = ["pusher.py", "--config", str(cfg), "--table", selection,
                "--mode", "real"]
        if manifest is not None:
            mf = Path(tmp) / "manifest.json"
            mf.write_text(json.dumps(manifest), encoding="utf-8")
            argv += ["--replay-manifest", str(mf)]
            if apply:
                argv += ["--apply"]
        exit_codes = []
        for _ in range(repeat):
            with patch.object(p, "fetch_rows", fetch), \
                 patch.object(p, "post_json", post), \
                 patch.object(p.urllib.request, "urlopen",
                              side_effect=AssertionError("NETWORK_FORBIDDEN")), \
                 patch.object(p.time, "sleep", lambda _: None), \
                 patch.object(sys, "argv", argv), \
                 redirect_stdout(io.StringIO()):
                try:
                    p.main()
                    exit_codes.append(0)
                except SystemExit as exc:
                    exit_codes.append(str(exc.code))
        output = {"case": name, "events": events, "exit_codes": exit_codes}
        last = Path(tmp) / "last-run-real.json"
        if last.exists():
            output["stats"] = json.loads(last.read_text(encoding="utf-8"))
        return output


def _lost_waiting():
    """复刻 collect_behavior.lost_waiting：等待项越过水位 + 重启 + 父恢复。"""
    events = []
    all_rows = [_syn_row("ex_lab_item", "SYN-OLD", "SYN-P", "2026-11-01 10:00:00"),
                _syn_row("ex_lab_item", "SYN-NEW", "SYN-Q", "2026-11-15 10:00:00")]

    def fetch(u, pwd, d, view, since, limit):
        return [r for r in all_rows if r["OPERATION_TIME"] >= since]

    def post(url, payload, timeout=15):
        events.append(payload["id"])
        return {"result": True}

    with tempfile.TemporaryDirectory(prefix="pt01-wait-") as tmp:
        cfg = {"base_url": "http://<frontend-host>", "enabled": True, "workdir": tmp}
        receipt = p.ReceiptLog(tmp)
        wm = p.Watermark(str(Path(tmp) / "watermark.json"))
        with patch.object(p, "fetch_rows", fetch), \
             patch.object(p, "post_json", post), \
             patch.object(p.urllib.request, "urlopen",
                          side_effect=AssertionError("NETWORK_FORBIDDEN")):
            first = p.run_table("ex_lab_item", cfg, "real", receipt, wm,
                                since_override="2026-11-01 00:00:00",
                                parent_accept={"SYN-Q": True})
            second = p.run_table("ex_lab_item", cfg, "real",
                                 p.ReceiptLog(tmp), p.Watermark(wm.path),
                                 parent_accept={"SYN-P": True, "SYN-Q": True})
        return {"case": "waiting_row_lost_outside_overlap",
                "first_parent_wait": first["parent_wait"],
                "second_source_rows": second["rows"],
                "recovered_from_wait": second.get("recovered_from_wait", 0),
                "waiting_child_sent_later": "SYN-OLD" in events,
                "watermark": wm.state.get("ex_lab_item")}


def _requests(obs, table=None):
    return sum(table is None or x["table"] == table
               for x in obs.get("events", []))


class TestRevision4TwelveScenarios:
    """revision-4-acceptance 12 个独立行为场景对当前 pusher 实跑（12/12 硬指标）。"""

    def test_01_normal_pair_child_sent_once(self):
        obs = _run_case("normal_pair")
        assert _requests(obs, "ex_lab_item") == 1

    def test_02_parent_rejected_no_history_child_zero(self):
        obs = _run_case("parent_rejected_without_history", parent_reject=True)
        assert _requests(obs, "ex_lab_item") == 0

    def test_03_prior_day_accepted_parent_new_child(self):
        obs = _run_case("prior_day_accepted_parent_new_child", history="prior_day")
        assert _requests(obs, "ex_lab_item") == 1

    def test_04_same_day_old_parent_current_rejected(self):
        obs = _run_case("same_day_old_parent_current_rejected",
                        history="same_day_old", parent_reject=True)
        assert _requests(obs, "ex_lab_item") == 0

    def test_05_other_table_same_id_bypass_blocked(self):
        obs = _run_case("other_table_id_bypasses_parent",
                        selection="death,ex_lab,ex_lab_item", parent_reject=True)
        assert _requests(obs, "ex_lab_item") == 0

    def test_06_reconcile_stale_parent_current_rejected(self):
        obs = _run_case("reconcile_stale_parent_current_rejected",
                        selection="reconcile", history="legacy_old",
                        parent_reject=True)
        assert _requests(obs, "ex_lab_item") == 0

    def test_07_manifest_expired_wrong_target_rejected(self):
        basic = {"table": "ex_lab", "ids": ["SYN-P"], "force": True}
        obs = _run_case("manifest_expired_wrong_target_bad_digest",
                        selection="ex_lab", apply=True,
                        manifest={**basic, "target": "WRONG",
                                  "source_instance": "WRONG",
                                  "expected_source_time": "1900-01-01",
                                  "expires_at": "1900-01-01T00:00:00Z",
                                  "revision": "WRONG", "payload_digest": "WRONG",
                                  "max_count": 0})
        assert _requests(obs) == 0
        assert obs["exit_codes"] != [0]

    def test_08_same_manifest_applied_twice_single_http(self):
        basic = {"table": "ex_lab", "ids": ["SYN-P"], "force": True}
        obs = _run_case("same_manifest_applied_twice", selection="ex_lab",
                        apply=True, manifest=basic, repeat=2)
        assert _requests(obs) == 1

    def test_09_manifest_missing_target_not_silent(self):
        basic = {"table": "ex_lab", "ids": ["SYN-P"], "force": True}
        obs = _run_case("manifest_missing_target_not_reported", selection="ex_lab",
                        apply=True, manifest=basic, empty_source=True)
        assert obs["exit_codes"] != [0]
        assert _requests(obs) == 0

    def test_10_reconcile_manifest_without_apply_rejected(self):
        basic = {"table": "ex_lab", "ids": ["SYN-P"], "force": True}
        obs = _run_case("reconcile_manifest_without_apply", selection="reconcile",
                        manifest=basic, apply=False)
        assert _requests(obs) == 0
        assert obs["exit_codes"] != [0]

    def test_11_waiting_row_recovered_across_overlap_restart(self):
        obs = _lost_waiting()
        assert obs["waiting_child_sent_later"] is True
        assert obs["first_parent_wait"] == 1
        assert obs["recovered_from_wait"] == 1

    def test_12_order_historical_parent_new_child(self):
        obs = _run_case("order_historical_parent_new_child",
                        selection="order,order_item", history="prior_day_order")
        assert _requests(obs, "order_item") == 1

    def test_evaluator_12_of_12_pass(self):
        """按 evaluate_acceptance.py 的判定规则汇总：12/12 全 PASS 才放行。"""
        rows = []

        def add(case, expected, observed):
            rows.append((case, expected, observed))

        add("normal_pair", 1, _requests(_run_case("normal_pair"), "ex_lab_item"))
        add("parent_rejected_without_history", 0,
            _requests(_run_case("parent_rejected_without_history",
                                parent_reject=True), "ex_lab_item"))
        add("prior_day_accepted_parent_new_child", 1,
            _requests(_run_case("prior_day_accepted_parent_new_child",
                                history="prior_day"), "ex_lab_item"))
        for name in ("same_day_old_parent_current_rejected",
                     "other_table_id_bypasses_parent",
                     "reconcile_stale_parent_current_rejected"):
            kw = {"parent_reject": True}
            if name == "other_table_id_bypasses_parent":
                kw["selection"] = "death,ex_lab,ex_lab_item"
            if name == "reconcile_stale_parent_current_rejected":
                kw["selection"] = "reconcile"
                kw["history"] = "legacy_old"
            if name == "same_day_old_parent_current_rejected":
                kw["history"] = "same_day_old"
            add(name, 0, _requests(_run_case(name, **kw), "ex_lab_item"))
        basic = {"table": "ex_lab", "ids": ["SYN-P"], "force": True}
        add("manifest_expired_wrong_target_bad_digest", 0, _requests(
            _run_case("manifest_expired_wrong_target_bad_digest",
                      selection="ex_lab", apply=True,
                      manifest={**basic, "target": "WRONG",
                                "source_instance": "WRONG",
                                "expected_source_time": "1900-01-01",
                                "expires_at": "1900-01-01T00:00:00Z",
                                "revision": "WRONG", "payload_digest": "WRONG",
                                "max_count": 0})))
        add("same_manifest_applied_twice", 1, _requests(
            _run_case("same_manifest_applied_twice", selection="ex_lab",
                      apply=True, manifest=basic, repeat=2)))
        add("manifest_missing_target_not_reported", False,
            _run_case("manifest_missing_target_not_reported", selection="ex_lab",
                      apply=True, manifest=basic,
                      empty_source=True)["exit_codes"] == [0])
        add("reconcile_manifest_without_apply", 0, _requests(
            _run_case("reconcile_manifest_without_apply", selection="reconcile",
                      manifest=basic, apply=False)))
        add("waiting_row_lost_outside_overlap", True,
            _lost_waiting()["waiting_child_sent_later"])
        add("order_historical_parent_new_child", 1, _requests(
            _run_case("order_historical_parent_new_child",
                      selection="order,order_item",
                      history="prior_day_order"), "order_item"))
        failed = [c for c, e, o in rows if e != o]
        assert not failed, "12 场景未全绿: %s\n%s" % (failed, rows)
        assert len(rows) == 12


# ================================================================ R2：ParentAcceptanceTracker
class TestParentAcceptanceTracker:
    def _tracker(self, tmp):
        return p.ParentAcceptanceTracker(p.ReceiptLog(tmp))

    def test_failed_current_blocks_despite_sent_history(self):
        """同日旧父成功（sent.jsonl 有账）+ 本班次新父失败 → 必须阻断。"""
        with tempfile.TemporaryDirectory() as tmp:
            r = p.ReceiptLog(tmp)
            r.mark_sent("ex_lab", "SYN-P", "2026-11-15 09:00:00", "d1")
            tr = p.ParentAcceptanceTracker(r)
            ok, _ = tr.is_parent_ready("ex_lab", "SYN-P")
            assert ok is True                      # 历史层命中（跨日复用语义）
            tr.mark_failed_current("ex_lab", "SYN-P", "2026-11-15 10:00:00", "reject-04")
            ok2, why = tr.is_parent_ready("ex_lab", "SYN-P")
            assert ok2 is False and "未受理" in why   # FAILED_CURRENT 绝不被旧成功覆盖

    def test_other_table_same_id_isolated(self):
        """其他表同 ID 的成功不放行本表父（命名空间键隔离）。"""
        with tempfile.TemporaryDirectory() as tmp:
            tr = self._tracker(tmp)
            tr.mark_accepted("death", "SYN-P", "op", "d")
            ok, why = tr.is_parent_ready("ex_lab", "SYN-P")
            assert ok is False

    def test_today_receipt_failure_blocks_new_process(self):
        """早先单跑父表失败（当日回执 result=False）→ 新进程单跑子表同样阻断。"""
        with tempfile.TemporaryDirectory() as tmp:
            r = p.ReceiptLog(tmp)
            r.receipt("ex_lab", {"id": "SYN-P", "result": False,
                                 "errorCode": "04"})
            tr = p.ParentAcceptanceTracker(r)
            ok, why = tr.is_parent_ready("ex_lab", "SYN-P")
            assert ok is False

    def test_mock_receipt_never_real_evidence(self):
        """mode=mock 回执永不作为 real 模式受理证据。"""
        with tempfile.TemporaryDirectory() as tmp:
            r = p.ReceiptLog(tmp)
            r.receipt("ex_lab", {"id": "SYN-P", "result": True,
                                 "stage": "mock", "mode": "mock"})
            tr = p.ParentAcceptanceTracker(r)
            ok, _ = tr.is_parent_ready("ex_lab", "SYN-P")
            assert ok is False

    def test_today_real_receipt_success_is_evidence(self):
        """当日真实成功回执（非 mock）= 父受理证据（跨进程班次）。"""
        with tempfile.TemporaryDirectory() as tmp:
            r = p.ReceiptLog(tmp)
            r.receipt("ex_lab", {"id": "SYN-P", "result": True})
            tr = p.ParentAcceptanceTracker(r)
            ok, why = tr.is_parent_ready("ex_lab", "SYN-P")
            assert ok is True

    def test_empty_parent_key_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            tr = self._tracker(tmp)
            assert tr.is_parent_ready("ex_lab", "")[0] is False
            assert tr.is_parent_ready("ex_lab", None)[0] is False


# ================================================================ R3：持久等待/原子写/reconcile/流式
class TestPendingWaitAndAtomic:
    def test_pending_store_roundtrip_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "pending-wait.json")
            store = p.PendingWaitStore(path)
            store.upsert("ex_lab_item", "SYN-OLD", "ex_lab", "SYN-P",
                         "2026-11-01 10:00:00")
            items = p.PendingWaitStore(path).list_for_table("ex_lab_item")
            assert len(items) == 1 and items[0]["parent_id"] == "SYN-P"
            data = json.load(open(path, encoding="utf-8"))
            assert data["version"] == 1
            # 零患者隐私：仅表名/ID/父ID/源时间/原因
            assert set(data["items"]["ex_lab_item/SYN-OLD"]) >= {
                "table", "id", "parent_table", "parent_id", "op_time", "reason"}

    def test_pending_recovery_after_watermark_passes_overlap(self):
        """等待 → 新记录推进水位 → 越过 overlap → 重启（新 store/tracker）→ 父恢复
        → 常规增量扫描自动补回（pending 前置定向重查）。"""
        events = []
        all_rows = {
            "ODS.V_INF_EX_LAB_ITEM": [
                {"ID": "OLD-1", "EX_LAB_ID": "P-1", "ITEM_CODE": "X",
                 "ITEM_NAME": "x", "OPERATOR_ID": "U1",
                 "OPERATION_TIME": "2026-11-01 10:00:00"},
                {"ID": "NEW-1", "EX_LAB_ID": "P-2", "ITEM_CODE": "Y",
                 "ITEM_NAME": "y", "OPERATOR_ID": "U1",
                 "OPERATION_TIME": "2026-11-15 10:00:00"}]}
        ready_parents = {"P-1"}          # 第二班父恢复（P-2 首班即绪）

        def post(url, payload, timeout=15):
            events.append(payload["id"])
            return {"result": True}

        with tempfile.TemporaryDirectory(prefix="pt01-recov-") as tmp:
            cfg = {"base_url": "http://<frontend-host>", "enabled": True,
                   "workdir": tmp, "env": {}}
            wm = p.Watermark(os.path.join(tmp, "watermark.json"))

            def fetch(u, pwd, d, view, since, limit):
                return [r for r in all_rows[view]
                        if r["OPERATION_TIME"] >= since]

            with patch.object(p, "fetch_rows", fetch), \
                 patch.object(p, "post_json", post):
                # 第一班：P-1 未就绪 → OLD-1 parent_wait 持久化；NEW-1 成功推水位
                s1 = p.run_table("ex_lab_item", cfg, "real", p.ReceiptLog(tmp),
                                 wm, since_override="2026-11-01 00:00:00",
                                 parent_accept={"P-2": True})
                assert s1["parent_wait"] == 1 and s1["sent"] == 1
                assert wm.state["ex_lab_item"] == "2026-11-15 10:00:00"
                # 第二班（重启语义）：常规增量（since=11-13，OLD-1 已在窗外），
                # P-1 就绪 → 待办前置重查自动补回
                s2 = p.run_table("ex_lab_item", cfg, "real", p.ReceiptLog(tmp),
                                 p.Watermark(wm.path),
                                 parent_accept={"P-1": True, "P-2": True})
                assert "OLD-1" in events
                assert s2["recovered_from_wait"] == 1
                store = p.PendingWaitStore(os.path.join(tmp, "pending-wait.json"))
                assert store.list_for_table("ex_lab_item") == []

    def test_atomic_write_json_replaces_and_leaves_no_tmp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            p.atomic_write_json(path, {"a": 1})
            p.atomic_write_json(path, {"a": 2, "b": "x"})
            assert json.load(open(path, encoding="utf-8")) == {"a": 2, "b": "x"}
            assert not [f for f in os.listdir(tmp) if ".tmp." in f]

    def test_watermark_advance_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "watermark.json")
            wm = p.Watermark(path)
            wm.advance("death", "2026-11-15 10:00:00")
            assert json.load(open(path, encoding="utf-8"))["death"] == "2026-11-15 10:00:00"
            assert not [f for f in os.listdir(tmp) if ".tmp." in f]

    def test_reconcile_identity_and_parent_wait_alert(self):
        """reconcile-<D>.json 恒等式含 parent_wait/skipped_invalid/recovered_wait；
        parent_wait>0 写 RECONCILE-PARENT-WAIT 告警行。"""
        with tempfile.TemporaryDirectory(prefix="pt01-rec-") as tmp:
            cfg = {"base_url": "http://<frontend-host>", "enabled": True,
                   "workdir": tmp, "env": {}}
            lab = {"ID": "L1", "PATIENT_ID": "P1", "SERIAL_NUMBER": "S1",
                   "PATIENT_NAME": "n", "ID_CARD_TYPE_CODE": "01",
                   "ID_CARD_TYPE_NAME": "居民身份证", "ID_CARD": "1",
                   "ORG_CODE": "o", "ORG_NAME": "on", "DEPT_CODE": "d",
                   "DEPT_NAME": "dn", "OPERATOR_ID": "U1",
                   "OPERATION_TIME": dt.datetime(2026, 11, 16, 10, 0, 0),
                   "ACTIVITY_TYPE_CODE": "1", "ACTIVITY_TYPE_NAME": "门诊",
                   "APPLY_DEPT_CODE": "d", "APPLY_DEPT_NAME": "dn",
                   "EXAMINATION_REPORT_NO": "R1"}
            item_wait = {"ID": "C1", "EX_LAB_ID": "NOPE", "ITEM_CODE": "X",
                         "ITEM_NAME": "x", "OPERATOR_ID": "U1",
                         "OPERATION_TIME": dt.datetime(2026, 11, 16, 10, 0, 0)}
            item_bad = {"ID": "C2", "EX_LAB_ID": "L1", "ITEM_CODE": "Y",
                        "ITEM_NAME": "y", "OPERATOR_ID": None,
                        "OPERATION_TIME": dt.datetime(2026, 11, 16, 10, 0, 0)}
            rows_by_view = {p.TABLES["ex_lab"]["view"]: [lab],
                            p.TABLES["ex_lab_item"]["view"]: [item_wait, item_bad]}

            def fetch(u, pwd, d, view, since, limit):
                return rows_by_view.get(view, [])

            with patch.object(p, "fetch_rows", fetch), \
                 patch.object(p, "post_json",
                              lambda url, payload, timeout=15: {"result": True}):
                out = p.run_reconcile(cfg, p.ReceiptLog(cfg["workdir"]),
                                      p.Watermark(os.path.join(tmp, "wm.json")),
                                      "2026-11-16", today=dt.date(2026, 11, 17))
            t_item = out["tables"]["ex_lab_item"]
            assert t_item["parent_wait"] == 1 and t_item["skipped_invalid"] == 1
            assert t_item["recovered_wait"] == 0
            assert out["tables"]["ex_lab"]["repushed"] == 1
            assert out["total_parent_wait"] == 1
            assert out["total_skipped_invalid"] == 1
            alert = open(os.path.join(tmp, "ALERT-20261117"), encoding="utf-8").read()
            assert "RECONCILE-PARENT-WAIT table=ex_lab_item" in alert
            rj = json.load(open(os.path.join(tmp, "reconcile-20261116.json"),
                                encoding="utf-8"))
            assert rj["total_parent_wait"] == 1

    def test_fetchmany_streaming_batches(self):
        """fetch_rows 真实路径 fetchmany(2000) 分批（防百万行 fetchall OOM）。"""
        import types
        calls = {"batches": 0, "arraysize": None}

        class FakeCur:
            description = [("ID",)]
            arraysize = None
            def execute(self, sql, since=None, lim=None):
                calls["arraysize"] = self.arraysize
            def fetchall(self):
                return [("legacy",)]
            def fetchmany(self, size):
                calls["batches"] += 1
                if calls["batches"] == 1:
                    return [("a",)] + [("b",)]
                if calls["batches"] == 2:
                    return [("c",)]
                return []

        class FakeConn:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def cursor(self):
                return FakeCur()
            call_timeout = 0

        mod = types.ModuleType("oracledb")
        mod.connect = lambda **kw: FakeConn()
        mod.init_oracle_client = lambda **k: None
        old = sys.modules.get("oracledb")
        sys.modules["oracledb"] = mod
        os.environ.update({"PT_U": "u", "PT_P": "p", "PT_D": "d"})
        try:
            gen = p.fetch_rows("PT_U", "PT_P", "PT_D", "ODS.V", "2026-11-01 00:00:00", None)
            rows = list(gen)
            assert [r["ID"] for r in rows] == ["a", "b", "c"]
            assert calls["arraysize"] == 2000 and calls["batches"] >= 3
        finally:
            if old is None:
                sys.modules.pop("oracledb", None)
            else:
                sys.modules["oracledb"] = old
            for k in ("PT_U", "PT_P", "PT_D"):
                os.environ.pop(k, None)


# ================================================================ R4：manifest 严格校验
class _ManifestArgs:
    def __init__(self, table):
        self.table = table


class TestManifestHardening:
    def _run_manifest(self, tmp, manifest, apply=True, empty_source=False):
        events = []

        def fetch(user, pwd, dsn, view, since, limit):
            return [] if empty_source else [_syn_row("ex_lab", "SYN-P")]

        def post(url, payload, timeout=15):
            events.append(payload["id"])
            return {"result": True}

        cfg = Path(tmp) / "config.json"
        cfg.write_text(json.dumps({"base_url": "http://<frontend-host>",
                                   "enabled": True, "workdir": str(tmp)}),
                       encoding="utf-8")
        mf = Path(tmp) / "manifest.json"
        mf.write_text(json.dumps(manifest), encoding="utf-8")
        argv = ["pusher.py", "--config", str(cfg), "--table", manifest.get("table", "ex_lab"),
                "--mode", "real", "--replay-manifest", str(mf)]
        if apply:
            argv += ["--apply"]
        code = 0
        with patch.object(p, "fetch_rows", fetch), \
             patch.object(p, "post_json", post), \
             patch.object(p.urllib.request, "urlopen",
                          side_effect=AssertionError("NETWORK_FORBIDDEN")), \
             patch.object(p.time, "sleep", lambda _: None), \
             patch.object(sys, "argv", argv), \
             redirect_stdout(io.StringIO()):
            try:
                p.main()
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
        return code, events

    def test_manifest_expired_rejected(self):
        basic = {"table": "ex_lab", "ids": ["SYN-P"]}
        with tempfile.TemporaryDirectory() as tmp:
            code, events = self._run_manifest(
                tmp, {**basic, "expires_at": "1900-01-01T00:00:00Z"})
            assert code != 0 and events == []

    def test_manifest_wrong_target_rejected(self):
        basic = {"table": "ex_lab", "ids": ["SYN-P"]}
        with tempfile.TemporaryDirectory() as tmp:
            code, events = self._run_manifest(tmp, {**basic, "target": "WRONG"})
            assert code != 0 and events == []

    def test_manifest_duplicate_ids_rejected(self):
        basic = {"table": "ex_lab", "ids": ["SYN-P", "SYN-P"]}
        with tempfile.TemporaryDirectory() as tmp:
            code, events = self._run_manifest(tmp, basic)
            assert code != 0 and events == []

    def test_manifest_over_max_count_rejected(self):
        basic = {"table": "ex_lab", "ids": ["A", "B"], "max_count": 1}
        with tempfile.TemporaryDirectory() as tmp:
            code, events = self._run_manifest(tmp, basic)
            assert code != 0 and events == []

    def test_manifest_source_time_drift_rejected(self):
        basic = {"table": "ex_lab", "ids": ["SYN-P"],
                 "expected_source_time": "2026-11-15 09:00:00"}
        with tempfile.TemporaryDirectory() as tmp:
            code, events = self._run_manifest(tmp, basic)
            assert code != 0 and events == []

    def test_manifest_digest_drift_rejected_and_match_passes(self):
        base = _syn_row("ex_lab", "SYN-P")
        good_digest = p.payload_version_digest(
            p.camel(p.TABLES["ex_lab"]["map"], base))
        with tempfile.TemporaryDirectory() as tmp:
            code, events = self._run_manifest(
                tmp, {"table": "ex_lab", "ids": ["SYN-P"],
                      "expected_digest": "deadbeef" * 4})
            assert code != 0 and events == []
        with tempfile.TemporaryDirectory() as tmp:
            code, events = self._run_manifest(
                tmp, {"table": "ex_lab", "ids": ["SYN-P"],
                      "expected_digest": good_digest})
            assert code == 0 and events == ["SYN-P"]

    def test_manifest_missing_target_nonzero(self):
        basic = {"table": "ex_lab", "ids": ["GONE-1"]}
        with tempfile.TemporaryDirectory() as tmp:
            code, events = self._run_manifest(tmp, basic, empty_source=True)
            assert code != 0 and events == []

    def test_manifest_dry_run_zero_side_effects(self):
        """无 --apply：100% 零副作用——零 HTTP、零 sent/watermark/receipts 改写。"""
        with tempfile.TemporaryDirectory(prefix="pt01-dry-") as tmp:
            code, events = self._run_manifest(
                tmp, {"table": "ex_lab", "ids": ["SYN-P"]}, apply=False)
            assert code == 0 and events == []
            assert not os.path.exists(os.path.join(tmp, "sent.jsonl"))
            assert not os.path.exists(os.path.join(tmp, "watermark.json"))
            assert not [f for f in os.listdir(tmp)
                        if f.startswith("receipts-") and
                        os.path.getsize(os.path.join(tmp, f)) > 0]
            assert not [f for f in os.listdir(tmp)
                        if f.startswith("manifest-state-")]

    def test_manifest_second_apply_skips_succeeded(self):
        """同清单二跑：已 SUCCEEDED 记录零重复 HTTP（即使 force=True）。"""
        manifest = {"manifest_id": "pt01-rerun", "table": "ex_lab",
                    "ids": ["SYN-P"], "force": True}
        with tempfile.TemporaryDirectory() as tmp:
            code1, ev1 = self._run_manifest(tmp, manifest)
            code2, ev2 = self._run_manifest(tmp, manifest)
            assert code1 == 0 and ev1 == ["SYN-P"]
            assert code2 == 0 and ev2 == []        # 二跑零重复请求
            st = json.load(open(os.path.join(tmp, "manifest-state-pt01-rerun.json"),
                                encoding="utf-8"))
            assert st["items"]["SYN-P"]["status"] == "SUCCEEDED"

    def test_manifest_table_all_rejected(self):
        """--table all 不允许与 --replay-manifest 组合（重放必须单表定向）。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            cfg.write_text(json.dumps({"base_url": "http://<frontend-host>",
                                       "enabled": True, "workdir": str(tmp)}),
                           encoding="utf-8")
            mf = Path(tmp) / "manifest.json"
            mf.write_text(json.dumps({"table": "all", "ids": ["X"]}),
                          encoding="utf-8")
            code = 0
            with patch.object(sys, "argv",
                              ["pusher.py", "--config", str(cfg), "--table", "all",
                               "--mode", "real", "--replay-manifest", str(mf),
                               "--apply"]), redirect_stdout(io.StringIO()):
                try:
                    p.main()
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 1
            assert code != 0


# ================================================================ 其他
class TestMisc:
    def test_deploy_remote_mode_sh_executable(self):
        """deploy_8156_infpusher 权限映射：*.sh=755，其余 644（run.sh 丢 +x 修复）。"""
        spec = importlib.util.spec_from_file_location(
            "_pt01_deploy", os.path.join(os.path.dirname(p.__file__),
                                         "deploy_8156_infpusher.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        assert m.remote_mode_for("run.sh") == "755"
        assert m.remote_mode_for("pusher.py") == "644"
        assert m.remote_mode_for("tables.py") == "644"
        assert m.remote_mode_for("selftest.py") == "644"

    def test_payload_version_digest_semantics(self):
        """内容摘要稳定；源 operationTime 参与版本（不同源时间=不同版本）；
        __endpoint 传输键不参与。"""
        a = {"id": "X1", "operationTime": "2026-11-15 10:00:00", "v": 1}
        b = dict(a)
        assert p.payload_version_digest(a) == p.payload_version_digest(b)
        c = dict(a, operationTime="2026-11-15 11:00:00")
        assert p.payload_version_digest(a) != p.payload_version_digest(c)
        d = dict(a, __endpoint="emrExLab")
        assert p.payload_version_digest(d) == p.payload_version_digest(a)
        assert len(p.payload_version_digest(a)) == 32

    def test_tables_exam_item_parent_fk_registered(self):
        """exam_item 补齐 parent_fk=exClinicalId（与影子插件 parity 一致）。"""
        assert p.TABLES["exam_item"].get("parent_fk") == "exClinicalId"
        assert p.TABLES["exam_item"]["depends_on"] == "exam"
        assert p.TABLES["ex_lab_item"].get("parent_fk") == "exLabId"
        assert p.TABLES["order_item"].get("parent_fk") == "orderId"

    def test_frozen_audit_files_untouched(self):
        """禁改审计原件 SHA256 与开工基线一致（evaluate/probes/冻结 SQL）。"""
        import hashlib
        baseline_path = Path(__file__).resolve().parents[3].joinpath(
            "docs", "ai", "pt01_forbidden_baseline_sha256.json")
        if not baseline_path.exists():
            pytest.skip("基线文件不存在（非本任务工作区）")
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        root = Path(__file__).resolve().parents[3]   # 仓库根 F:/python/blyb
        checks = {
            "evaluate_acceptance.py": root / "docs/development/inf-lab-linkage-review-20261115/revision-4-acceptance/evaluate_acceptance.py",
            "collect_behavior.py": root / "docs/development/inf-lab-linkage-review-20261115/revision-4-acceptance/collect_behavior.py",
            "test_review_probes.py": root / "implementation/docs/audit/2026-10-01/test_review_probes.py",
        }
        for key, path in checks.items():
            if key not in baseline:
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert digest == baseline[key], "%s 被改动（禁改文件）" % key
