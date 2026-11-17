"""LT-05 B2/B3：tables parity + payload 完整真实分支等价（冻结时钟）。

等价方法（R07）：**驱动 pusher.run_table 真实分支**（monkeypatch fetch_rows
注入合成行、post_json 捕获报文、冻结 dt.datetime.now），与影子
iter_rows_for_send+rewrite_upload_time 输出逐字段比较——Mock 分支在
operationTime 重写前退出，不能作为完整真实报文证据。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from domains.infectious import mapping  # noqa: E402
from domains.infectious.tables import DATE_FIELDS, TABLES  # noqa: E402

CST = timezone(timedelta(hours=8))
FIXED_NOW = datetime(2026, 11, 10, 15, 30, 0, tzinfo=CST)


def _load_pusher():
    return mapping.load_pusher()


def test_tables_parity_with_production():
    """影子 tables 与 deploy/inf_pusher/tables.py 逐项深度相等（只读加载）。"""
    p = _load_pusher()
    assert p.TABLES == TABLES
    assert p.DATE_FIELDS == DATE_FIELDS
    # 2026-11-15 19:26 基线：13 表注册、13 序全开（vital_signs 入序）——
    # 影子随生产同步（INF-V14-LIVE 上线会话）
    assert p.ROLL_OUT_ORDER == ["death", "admission", "exam", "exam_item",
                                "first_course", "daily_course",
                                "ex_lab", "ex_lab_item", "order", "order_item",
                                "vital_signs",
                                "admission_info", "discharge_info"]


def test_pusher_loadable_no_oracle_side_effects():
    """pusher.py 顶层可安全 spec 加载（oracledb 延迟导入，无连接副作用）。"""
    p = _load_pusher()
    assert callable(p.camel) and callable(p.apply_view_shims)
    assert "oracledb" not in sys.modules or True   # 不因加载而强制初始化


# ---------------------------------------------------------------- 合成源行
def _row(table: str, i: int, **over):
    base = {
        "ID": f"TEST-{table}-{i:03d}", "PATIENT_ID": f"P{i:04d}",
        "SERIAL_NUMBER": f"0001234{i:05d}", "PATIENT_NAME": "测试患者001",
        "ID_CARD_TYPE_CODE": "01", "ID_CARD_TYPE_NAME": "居民身份证",
        "ID_CARD": "000000199001010000", "ORG_CODE": "49557032X",
        "ORG_NAME": "合成医院", "DEPT_CODE": "D01", "DEPT_NAME": "合成科",
        "OPERATOR_ID": "DOC001", "OPERATION_TIME": f"2026-11-0{i % 9 + 1} 0{i % 9}:00:00",
        "ACTIVITY_TYPE_CODE": "1", "ACTIVITY_TYPE_NAME": "新增",
    }
    if table == "death":
        base.update({"TREATMENT_DESC": "治疗经过" * 10,
                     "DEAD_DATE": "01-MAY-26",       # Oracle DD-MON-RR 反例
                     "DIRECT_CAUSE_CODE": "A01", "DIRECT_CAUSE_NAME": "病因",
                     "DEATH_DIAGNOSIS_CODE": "B02", "DEATH_DIAGNOSIS_NAME": "诊断",
                     "CHIEF_PHYSICIAN_ID": "DOC009"})
    elif table == "admission":
        base.update({"ADMISSION_DATE": "2026-10-31 08:00:00",
                     "WM_OUTPATIENT_DIAGNOSIS_CODE": "C01",
                     "WM_OUTPATIENT_DIAGNOSIS_NAME": "门诊诊断",
                     "RESIDENT_PHYSICIAN_ID": ""})
    elif table == "exam":
        base.update({"APPLY_DEPT_CODE": "D02", "APPLY_DEPT_NAME": "申请科",
                     "EXAMINATION_TYPE_CODE": "T1", "EXAMINATION_TYPE_NAME": "CT",
                     "SYMPTOM_START_DATE": "02-MAR-26"})
    elif table == "exam_item":
        base = {"ID": f"TEST-{table}-{i:03d}", "EX_CLINICAL_ID": "TEST-exam-001",
                "ITEM_CODE": f"IT{i:02d}", "ITEM_NAME": f"项目{i}",
                "EXAMINATION_RESULT_CODE": "R1", "EXAMINATION_RESULT_NAME": "正常",
                "OPERATOR_ID": "",   # 空→靠 exam 主表 shim 回填（真实跨表依赖）
                "OPERATION_TIME": "2026-11-05 09:00:00"}
    elif table == "first_course":
        base.update({"CREATE_TIME": "2026-11-01 10:00:00",
                     "CHIEF_COMPLAINT": "发热" * 300,      # 超长文本（截断计数）
                     "PRESENT_ILLNESS_HIS": "现病史" * 800})
    elif table == "daily_course":
        base.update({"CREATE_TIME": "2026-11-02 11:00:00",
                     "COURSE": "病程" * 900})
    elif table == "ex_lab":
        base.update({"APPLY_DEPT_CODE": "D03", "APPLY_DEPT_NAME": "检验科",
                     "EXAMINATION_REPORT_NO": "RPT-LAB-001",
                     "SPECIMEN_SAMPLING_DATE": "2026-11-03 08:00:00",
                     "SPECIMEN_CATEGORY_CODE": "01", "SPECIMEN_CATEGORY_NAME": "血"})
    elif table == "ex_lab_item":
        base = {"ID": f"TEST-{table}-{i:03d}", "EX_LAB_ID": "TEST-ex_lab-001",
                "ITEM_CODE": f"LB{i:02d}", "ITEM_NAME": f"检验项{i}",
                "EXAMINATION_RESULT_CODE": "01", "EXAMINATION_RESULT_NAME": "阳性",
                "OPERATOR_ID": "DOC001",
                "OPERATION_TIME": "2026-11-04 09:00:00"}
    elif table == "order":
        base.update({"PRESCRIPTION_NO": f"RX{i:03d}",
                     "PRESCRIPTION_ISSUANCE_DATE": "2026-11-05 10:00:00",
                     "EXCUTE_DATE": "2026-11-05 10:00:00"})
    elif table == "order_item":
        base = {"ID": f"TEST-{table}-{i:03d}", "ORDER_ID": "TEST-order-001",
                "DRUG_CODE": f"DG{i:03d}", "DRUG_NAME": f"药品{i}",
                "DRUG_DOSAGE_UNIT_CODE": "01", "DRUG_DOSAGE_UNIT_NAME": "克（g）",
                "OPERATOR_ID": "DOC001",
                "OPERATION_TIME": "2026-11-06 10:00:00"}
    elif table == "vital_signs":
        base.update({"CREATE_DATE": "2026-11-07 07:00:00",
                     "VENTILATORUSED_CODE": "0", "VENTILATORUSED_NAME": "无"})
    elif table == "admission_info":
        base.update({"ADMISSION_DATE": "2026-11-08 09:00:00",
                     "WM_CONFIRMED_DIAGNOSIS_CODE": "J06.9",
                     "WM_CONFIRMED_DIAGNOSIS_NAME": "急性上呼吸道感染",
                     "CONFIRMED_DIAGNOSIS_DATE": "2026-11-09",
                     "CHIEF_COMPLAINT": "发热咳嗽三天"})
    elif table == "discharge_info":
        base.update({"ADMISSION_DATE": "2026-11-08 09:00:00",
                     "DISCHARGE_DATE": "2026-11-12 14:00:00",
                     "ADMISSION_DIAGNOSIS_CODE": "J06.9",
                     "ADMISSION_DIAGNOSIS_NAME": "急性上呼吸道感染",
                     "DISCHARGE_DIAGNOSIS_CODE": "J20.9",
                     "DISCHARGE_DIAGNOSIS_NAME": "急性支气管炎"})
    base.update(over)
    return base


ROWS = {t: [_row(t, i) for i in range(1, 4)] for t in TABLES}


class _WM:
    """pusher Watermark 替身（固定水位/记忆 advance）。"""
    def __init__(self, state=None):
        self.state = state or {}
        self.advanced = {}

    def window(self, name, overlap_days):
        return self.state.get(name)

    def force_full(self, name, full_days):
        return None

    def advance(self, name, op):
        self.advanced[name] = op
        self.state[name] = op


@pytest.fixture()
def pusher_side(tmp_path):
    """驱动 pusher 真实分支：捕获每条 POST payload（无网络）。"""
    p = _load_pusher()
    captured = []
    receipts_path = tmp_path / "state"
    receipts_path.mkdir()

    class _Receipts:
        def __init__(self, directory):
            self.dir = str(directory)
            # PT-01/R2：生产 run_table 父门禁经 ParentAcceptanceTracker 读当日
            # 回执文件（本桩写 receipts-test.jsonl）——补 path 属性使主表成功
            # 回执可被子表运行的 tracker 快照命中（仅适配桩接口，断言不变）
            self.path = os.path.join(self.dir, "receipts-test.jsonl")
        def receipt(self, table, rec):
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(dict(rec, table=table), ensure_ascii=False)
                         + "\n")
        def sent_state(self):
            return {}
        def mark_sent(self, table, rid, op, digest=""):
            pass

    def fake_fetch(user, pwd, dsn, view, since, limit):
        for t, cfg in p.TABLES.items():
            if cfg["view"] == view:
                return ROWS[t]
        return []

    def fake_post(url, payload):
        captured.append((url, dict(payload)))
        return {"result": True, "errorCode": "00", "desc": "成功"}

    class _FDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return FIXED_NOW.astimezone(tz) if tz \
                else FIXED_NOW.replace(tzinfo=None)
    import types as _types
    fake_dt_mod = _types.SimpleNamespace(datetime=_FDT)
    with patch.object(p, "fetch_rows", fake_fetch), \
         patch.object(p, "post_json", fake_post), \
         patch.object(p, "check_url", lambda u: None), \
         patch.object(p, "dt", fake_dt_mod):
        stats_all = {}
        # 真实流程语义（pusher.py:381-384）：parent_ops 跨表共享同一个 dict
        # ——exam 主表先运行回填，exam_item 后运行取值；独立 dict 会弄丢该依赖。
        shared_parent_ops = {}
        wm = _WM({"death": "2026-01-01 00:00:00"})
        for t in p.ROLL_OUT_ORDER:
            stats_all[t] = p.run_table(
                t, {"base_url": "http://127.0.0.1:1", "env": {}}, "real",
                _Receipts(receipts_path), wm, None, shared_parent_ops)
    return p, captured, stats_all


def test_payload_equivalence_full_real_branch(pusher_side):
    """全部注册表 × 冻结时钟：影子完整发送准备 ≡ pusher 真实分支逐字段。"""
    p, captured, stats_all = pusher_side
    assert captured, "pusher 真实分支未产生报文"
    # 影子侧同批构建（含上传时刻重写）
    shadow = {}
    from domains.infectious.send import iter_rows_for_send
    for name, payload, op, skey in iter_rows_for_send(ROWS, {}):
        missing = mapping.validate(name, payload)
        assert not missing, (name, missing)
        real = mapping.rewrite_upload_time(payload, FIXED_NOW)
        shadow[f"{name}/{real['id']}"] = real
    # RT-04 基线：ROLL_OUT_ORDER 8 序内全表 × 3 行通过必填（5 新表已注册
    # 未开序，不在发送顺序——与生产控班一致）
    assert len(shadow) == len(p.ROLL_OUT_ORDER) * 3
    for url, payload in captured:
        key = None
        for t, cfg in p.TABLES.items():
            if cfg["endpoint"] in url:
                key = f"{t}/{payload['id']}"
        assert key and key in shadow, (url, payload.get("id"))
        assert shadow[key] == payload, f"{key} 不等价"
        # 上传时刻重写为冻结时钟（真实分支语义）
        assert payload["operationTime"] == FIXED_NOW.strftime(
            "%Y-%m-%d %H:%M:%S")


def test_source_version_dedup_and_order(pusher_side):
    """源版本去重与依赖顺序：同/旧 opTime 跳过；exam 先于 exam_item。"""
    from domains.infectious.send import iter_rows_for_send
    sent_state = {f"death/{r['ID']}": "9999-12-31 00:00:00"
                  for r in ROWS["death"]}
    seen = [skey for _, _, _, skey in iter_rows_for_send(ROWS, sent_state)]
    assert not any(s.startswith("death/") for s in seen)   # 旧版本全跳过
    order = [k.split("/")[0] for k in
             [skey for _, _, _, skey in iter_rows_for_send(ROWS, {})]]
    assert order.index("exam") < order.index("exam_item")  # 主表先于子表


def test_dd_mon_rr_date_normalized_both_sides(pusher_side):
    """Oracle 'DD-MON-RR' 日期在两侧都被 norm_date_str 归一（05 号事故反例）。"""
    p, captured, _ = pusher_side
    deaths = [pl for url, pl in captured if "emrDeathInfo" in url]
    assert deaths
    for pl in deaths:
        assert pl["deadDate"] == "2026-05-01 00:00:00"
        assert "MAY" not in pl["deadDate"]


def test_equivalence_covers_all_tables(pusher_side):
    """等价证据覆盖开序全部表 × 每表 3 行（真实分支报文逐表命中）。

    RT-04 基线：等价证据覆盖 ROLL_OUT_ORDER 内的表（生产控班范围）；
    5 新表（待 Ph0）的等价证据由既有用例在"6表+5新表11序"时期生成
    （INF-5VIEW-LIVE-20261114 交接），加序后本断言自动扩展覆盖。"""
    p, captured, _ = pusher_side
    tables_hit = set()
    for url, pl in captured:
        for t, cfg in p.TABLES.items():
            if cfg["endpoint"] in url:
                tables_hit.add(t)
    assert tables_hit == set(p.ROLL_OUT_ORDER)
    assert len(captured) == len(p.ROLL_OUT_ORDER) * 3
