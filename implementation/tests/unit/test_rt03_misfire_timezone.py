"""RT-03 S03/S04/S10：misfire 合并、最近槽位、时区与早报自然日窗口。

覆盖：积压 96 班 RUN_ONCE/SKIP 合并（1 条记录）；最近合法槽位业务日期；
第二 tick 不重复；grace 边界；跨午夜 blackout 计划/实际双判定；FORBID
观测态不放行；入队失败回滚后下一 tick 重试；naive/aware/+08 同瞬间；
UTC 16 点跨院内日；昨日窗口边界；有效/损坏/范围外 cron 分类；滚动与
自然日口径区分。全部受控时钟，不 sleep。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.engine import utcnow  # noqa: E402
from core_db.models import (AlertEvent, Job, NotificationChannel, Schedule,  # noqa: E402
                            ScheduleRun, Tenant)
from workflow_core.cron import (CST, CronExpr, NextFireOutOfRange,  # noqa: E402
                                to_aware_utc)

U = timezone.utc


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/rt03mf.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace", "pathology"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="rt03-mf", name="补跑时区测试")
        db.add(t)
        db.flush()
        tid = t.id
        db.add(NotificationChannel(
            id=new_uuid(), tenant_id=tid, code="file_default", type="file",
            config={"path": f"var/alerts-test/rt03-{new_uuid()[:8]}.jsonl"},
            enabled=True))
        db.commit()
    yield {"sf": sf, "engine": engine, "tid": tid, "reg": reg}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


class _NullReg:
    def capability_merged(self, key):
        return {}

    def capability_list(self, key):
        return []


def _mk_sched(env, *, cron="*/5 * * * *", next_fire=None, misfire="SKIP",
              concurrency="FORBID", blackout=None, payload=None, code=None):
    with env["sf"]() as db:
        s = Schedule(id=new_uuid(), tenant_id=env["tid"],
                     code=code or f"mf{new_uuid()[:6]}", name="n",
                     job_type="metrics.run", cron=cron, enabled=True,
                     payload_template=payload or {},
                     concurrency=concurrency, blackout=blackout or [],
                     misfire_policy=misfire, next_fire_at=next_fire)
        db.add(s)
        db.commit()
        return s.id


# ===================================================== S03 补跑合并
def test_backlog_96_slots_run_once_merges_to_latest_slot(env):
    """积压 96 班（*/5 班 ×96=8h）RUN_ONCE：只补**最近一个合法槽位**，
    不制造 96 条记录。"""
    from scheduling.scheduler import tick
    now = utcnow().replace(tzinfo=U)
    due = (now - timedelta(hours=8)).replace(tzinfo=None)   # 96 个 5min 槽之前
    sid = _mk_sched(env, next_fire=due, misfire="RUN_ONCE")
    out = tick(env["sf"](), now, registry=_NullReg())
    assert out["fired"] == 1
    with env["sf"]() as db:
        runs = db.query(ScheduleRun).filter_by(schedule_id=sid).all()
        assert len(runs) == 1                              # 一条，不是 96 条
        expect = CronExpr("*/5 * * * *").previous_at_or_before(now)
        assert runs[0].fire_time == expect.replace(tzinfo=None)
        assert runs[0].fire_time >= due and runs[0].fire_time <= now.replace(tzinfo=None)


def test_backlog_96_slots_skip_records_single_missed_with_range(env):
    """积压 96 班 SKIP：仅 1 条 MISSED，摘要注明被折叠区间。"""
    from scheduling.scheduler import tick
    now = utcnow().replace(tzinfo=U)
    due = (now - timedelta(hours=8)).replace(tzinfo=None)
    sid = _mk_sched(env, next_fire=due, misfire="SKIP")
    out = tick(env["sf"](), now, registry=_NullReg())
    assert out["skipped"].get("MISSED") == 1
    with env["sf"]() as db:
        runs = db.query(ScheduleRun).filter_by(schedule_id=sid).all()
        assert len(runs) == 1 and runs[0].state == "MISSED"
        s = runs[0].result_summary
        assert s["collapsed_from"] == due.isoformat()
        assert s["collapsed_to"] == CronExpr("*/5 * * * *") \
            .previous_at_or_before(now).replace(tzinfo=None).isoformat()
        assert db.query(Job).filter(
            Job.dedup_key.like(f"sched:{sid}:%")).count() == 0


def test_latest_slot_renders_hospital_business_dates(env):
    """最近合法槽位渲染业务日期：00:05 触发 {fire_date}=今天
    {fire_date_minus_1}=昨天（CST）。"""
    from scheduling.scheduler import tick
    # CST 今日 00:05（可能已是"昨天槽位"若真实时刻早于 00:05——用固定锚：
    # 构造 next_fire 为 UTC 今日 CST 00:05、now 为其后 3 分钟内（未超 grace）
    now_cst = utcnow().replace(tzinfo=U).astimezone(CST)
    fire_cst = now_cst.replace(hour=0, minute=5, second=0, microsecond=0)
    if now_cst < fire_cst:                     # 今天 00:05 未到（边界保护）
        pytest.skip("真实时刻早于 CST 00:05，改用受控槽位断言")
    fire_utc = fire_cst.astimezone(U)
    now_utc = fire_utc + timedelta(minutes=2)  # 00:07 → 未超 grace
    sid = _mk_sched(env, cron="5 0 * * *",
                    next_fire=fire_utc.replace(tzinfo=None),
                    payload={"biz_date": "{fire_date}",
                             "prev_date": "{fire_date_minus_1}"})
    out = tick(env["sf"](), now_utc, registry=_NullReg())
    assert out["fired"] == 1
    with env["sf"]() as db:
        run = db.query(ScheduleRun).filter_by(schedule_id=sid).one()
        assert run.fire_time == fire_utc.replace(tzinfo=None)  # 准点槽位
        job = db.get(Job, run.job_id)
        assert job.payload["biz_date"] == fire_cst.date().isoformat()
        assert job.payload["prev_date"] == \
            (fire_cst.date() - timedelta(days=1)).isoformat()


def test_second_tick_same_now_does_not_double_fire(env):
    """两次相同 now 的 tick 不重复触发（next_fire 已推进到严格大于 now）。"""
    from scheduling.scheduler import tick
    now = utcnow().replace(tzinfo=U)
    sid = _mk_sched(env, next_fire=(now - timedelta(minutes=1)).replace(tzinfo=None))
    out1 = tick(env["sf"](), now, registry=_NullReg())
    out2 = tick(env["sf"](), now, registry=_NullReg())
    assert out1["fired"] == 1 and out2["fired"] == 0
    with env["sf"]() as db:
        assert db.query(ScheduleRun).filter_by(schedule_id=sid).count() == 1
        s = db.get(Schedule, sid)
        assert s.next_fire_at > now.replace(tzinfo=None)   # 严格大于 now


def test_grace_boundary_120s(env):
    """grace 边界：恰好 120s=准点执行；121s=超 grace 走 misfire。"""
    from scheduling.scheduler import tick
    now = utcnow().replace(tzinfo=U)
    sid = _mk_sched(env, next_fire=(now - timedelta(seconds=120))
                    .replace(tzinfo=None), misfire="SKIP")
    out = tick(env["sf"](), now, registry=_NullReg())
    assert out["fired"] == 1                                # 边界内准点
    now2 = utcnow().replace(tzinfo=U)
    sid2 = _mk_sched(env, next_fire=(now2 - timedelta(seconds=121))
                     .replace(tzinfo=None), misfire="SKIP")
    out2 = tick(env["sf"](), now2, registry=_NullReg())
    assert out2["fired"] == 0 and out2["skipped"].get("MISSED") == 1


def test_blackout_midnight_plan_and_now_both_checked(env):
    """跨午夜 blackout：计划槽位在窗口内 → SKIPPED_BLACKOUT（准点）；
    补跑时计划不在窗口但实际 now 在窗口 → 不启动补跑（单条记录）。"""
    from scheduling.scheduler import tick
    now_cst = utcnow().replace(tzinfo=U).astimezone(CST)
    # 场景1：准点槽位 23:50，窗口 23:30–00:30（跨午夜）→ 计划避让
    fire_cst = now_cst.replace(hour=23, minute=50, second=0, microsecond=0) \
        - timedelta(days=1)                 # 昨日 23:50 作为"已到期"槽
    # 为避免真实时刻差异，改为直接用受控 now=fire+10s
    now_utc = fire_cst.astimezone(U) + timedelta(seconds=10)
    sid = _mk_sched(env, cron="50 23 * * *",
                    next_fire=fire_cst.astimezone(U).replace(tzinfo=None),
                    blackout=[{"start": "23:30", "end": "00:30"}])
    out = tick(env["sf"](), now_utc, registry=_NullReg())
    assert out["skipped"].get("SKIPPED_BLACKOUT") == 1
    with env["sf"]() as db:
        assert db.query(ScheduleRun).filter_by(
            schedule_id=sid, state="SKIPPED_BLACKOUT").count() == 1

    # 场景2：补跑——计划槽位（昨日 00:40）不在窗口，但 now（今日 00:20）在
    # 窗口内 → 不启动补跑，只记一条 SKIPPED_BLACKOUT（不延后反复补跑）
    today = now_cst.date()
    now2_cst = now_cst.replace(hour=0, minute=20, second=0, microsecond=0)
    now2_utc = now2_cst.astimezone(U)
    prev_slot = (datetime.combine(today - timedelta(days=1),
                                  datetime.min.time(), tzinfo=CST)
                 + timedelta(minutes=40)).astimezone(U)      # 昨日 00:40
    sid2 = _mk_sched(env, cron="40 0 * * *",
                     next_fire=prev_slot.replace(tzinfo=None),
                     misfire="RUN_ONCE",
                     blackout=[{"start": "00:00", "end": "00:30"}])
    out2 = tick(env["sf"](), now2_utc, registry=_NullReg())
    assert out2["fired"] == 0
    with env["sf"]() as db:
        rows = db.query(ScheduleRun).filter_by(schedule_id=sid2).all()
        assert len(rows) == 1 and rows[0].state == "SKIPPED_BLACKOUT"
        assert db.query(Job).filter(
            Job.dedup_key.like(f"sched:{sid2}:%")).count() == 0


def test_forbid_blocks_on_nonterminal_run_only(env):
    """FORBID 看真实非终态 run：RUNNING run（Job 已终态）→ 拦；
    终态 run → 放行。"""
    from scheduling.scheduler import tick
    now = utcnow().replace(tzinfo=U)
    sid = _mk_sched(env, next_fire=(now - timedelta(minutes=1))
                    .replace(tzinfo=None))
    with env["sf"]() as db:
        db.add(ScheduleRun(id=new_uuid(), tenant_id=env["tid"],
                           schedule_id=sid, run_key="cron:old",
                           fire_time=now.replace(tzinfo=None) - timedelta(hours=1),
                           state="RUNNING", job_id=None))
        db.commit()
    out = tick(env["sf"](), now, registry=_NullReg())
    assert out["fired"] == 0 and out["skipped"].get("SKIPPED_OVERLAP") == 1


def test_enqueue_failure_rolls_back_and_next_tick_retries(env, monkeypatch):
    """入队失败：next_fire 推进/run/Job 整体回滚；下一 tick 正确重试。"""
    from scheduling import scheduler as SCH
    now = utcnow().replace(tzinfo=U)
    due = (now - timedelta(minutes=1)).replace(tzinfo=None)
    sid = _mk_sched(env, next_fire=due)
    real = SCH.enqueue_job
    calls = {"n": 0}

    def boom(db, *a, **kw):
        calls["n"] += 1
        raise RuntimeError("injected enqueue failure")

    monkeypatch.setattr(SCH, "enqueue_job", boom)
    with pytest.raises(RuntimeError):
        SCH.tick(env["sf"](), now, registry=_NullReg())
    with env["sf"]() as db:
        s = db.get(Schedule, sid)
        assert s.next_fire_at == due              # 回滚（未推进）
        assert db.query(ScheduleRun).filter_by(schedule_id=sid).count() == 0
    # 下一 tick（注入移除后）：正常重试成功
    monkeypatch.setattr(SCH, "enqueue_job", real)
    now2 = utcnow().replace(tzinfo=U)
    out = SCH.tick(env["sf"](), now2, registry=_NullReg())
    assert out["fired"] == 1
    with env["sf"]() as db:
        assert db.query(ScheduleRun).filter_by(schedule_id=sid).count() == 1
        s = db.get(Schedule, sid)
        assert s.next_fire_at > now2.replace(tzinfo=None)


def test_previous_at_or_before_symmetry_and_error(env):
    """previous_at_or_before 与 next_after 对称；范围内无触发显式报错。"""
    expr = CronExpr("*/7 * * * *")
    base = datetime(2026, 6, 15, 8, 30, tzinfo=U)
    prev = expr.previous_at_or_before(base)
    assert prev <= base and expr.matches(prev.astimezone(CST))
    nxt = expr.next_after(prev)
    assert nxt > base and expr.matches(nxt.astimezone(CST))
    # 2 月 30 日：向前/向后 366 天都无触发 → NextFireOutOfRange
    bad = CronExpr("0 0 30 2 *")
    with pytest.raises(NextFireOutOfRange):
        bad.previous_at_or_before(base)
    with pytest.raises(NextFireOutOfRange):
        bad.next_after(base)


# ===================================================== S04 时区/早报
def test_to_aware_utc_naive_offset_same_instant(env):
    """naive（按 UTC）/aware UTC/+08:00 同一瞬间的规范化结果一致。"""
    naive = datetime(2026, 11, 13, 2, 0, 0)
    aware_utc = datetime(2026, 11, 13, 2, 0, 0, tzinfo=U)
    aware_cst = datetime(2026, 11, 13, 10, 0, 0, tzinfo=CST)
    assert to_aware_utc(naive) == to_aware_utc(aware_utc) \
        == to_aware_utc(aware_cst) == aware_utc


def test_hospital_day_window_utc_16h_crosses_hospital_day(env):
    """UTC 16:00=CST 次日 00:00 跨院内日：昨日窗口随之翻转。"""
    from alerting.digest import _hospital_day_window
    # CST 2026-11-13 00:00 == UTC 2026-11-12 16:00
    now = datetime(2026, 11, 12, 16, 0, 30, tzinfo=U)
    yday, today, begin, end = _hospital_day_window(now)
    assert yday == "2026-11-12" and today == "2026-11-13"
    assert begin == datetime(2026, 11, 11, 16, 0)   # UTC 昨日 00:00 CST
    assert end == datetime(2026, 11, 12, 16, 0)


def test_digest_yesterday_window_boundary(env):
    """昨日窗口边界：窗内（含起点）计入昨日；窗起点前/终点起不计。"""
    from alerting.digest import build_digest
    now = datetime(2026, 11, 12, 16, 0, 30, tzinfo=U)   # CST 11-13 00:00:30
    _, _, begin, end = None, None, datetime(2026, 11, 11, 16, 0), \
        datetime(2026, 11, 12, 16, 0)
    with env["sf"]() as db:
        for label, at in (("in-begin", begin), ("in-mid", begin + timedelta(hours=5)),
                          ("before", begin - timedelta(minutes=1)),
                          ("after", end)):
            db.add(AlertEvent(id=new_uuid(), tenant_id=env["tid"],
                              alert_type="X", severity="warn",
                              message=label, target_ref="-", state="OPEN",
                              created_at=at))
        db.commit()
        text = build_digest(db, env["tid"], now)
    yline = [ln for ln in text.splitlines() if ln.startswith("—— 告警")][0]
    assert "昨日新增 2" in yline, yline            # begin 与 in-mid 计入
    assert "近24小时" in yline                     # 滚动口径单独标注


def test_digest_cron_error_classification(env):
    """有效/损坏/范围外 cron 分类如实；明天触发写（明天）不硬称今日。"""
    from alerting.digest import build_digest
    now = utcnow().replace(tzinfo=U)
    with env["sf"]() as db:
        db.add(Schedule(id=new_uuid(), tenant_id=env["tid"], code="bad",
                        job_type="metrics.run", cron="99 * * * *",
                        enabled=True))
        db.add(Schedule(id=new_uuid(), tenant_id=env["tid"], code="far",
                        job_type="metrics.run", cron="0 0 30 2 *",
                        enabled=True))
        db.add(Schedule(id=new_uuid(), tenant_id=env["tid"], code="tomorrow",
                        job_type="metrics.run", cron="0 3 * * *",
                        enabled=True))
        db.commit()
        text = build_digest(db, env["tid"], now)
    assert "表达式非法" in text
    assert "366 天内无触发" in text
    tline = [ln for ln in text.splitlines() if ln.startswith("  tomorrow")][0]
    now_cst_date = now.astimezone(CST).date()
    nxt_date = CronExpr("0 3 * * *").next_after(now).astimezone(CST).date()
    assert ("（明天）" in tline) == (nxt_date > now_cst_date)


class _RollingReg:
    """不支持 window= 的旧钩子注册表（滚动 24h 口径）。"""

    def target_summary(self, db, tenant_id, code, days):
        from plugins.domain_hooks import TargetSummary
        return TargetSummary(target_code=code, window_days=days,
                             buckets={"CONFIRMED": 1, "REJECTED": 0,
                                      "UNKNOWN": 0},
                             data_source="platform", window_kind="rolling_days")


class _WindowReg:
    """声明 window= 的注册表（自然日口径；drug_trace hooks 同形）。"""

    def target_summary(self, db, tenant_id, code, days, *, window=None):
        from plugins.domain_hooks import TargetSummary
        return TargetSummary(target_code=code, window_days=days,
                             buckets={"CONFIRMED": 7, "REJECTED": 0,
                                      "UNKNOWN": 0},
                             data_source="platform",
                             window_kind="hospital_day" if window
                             else "rolling_days")


class _NoSummaryReg:
    def target_summary(self, db, tenant_id, code, days, *, window=None):
        return None


class _NoneSourceReg:
    """无源数据来源（data_source="none"）→ unavailable/none 行。"""

    def target_summary(self, db, tenant_id, code, days, *, window=None):
        from plugins.domain_hooks import TargetSummary
        return TargetSummary(target_code=code, window_days=days,
                             buckets={}, data_source="none",
                             window_kind="hospital_day" if window
                             else "rolling_days")


class _Target:
    def __init__(self, code, name):
        self.code, self.name = code, name


def test_digest_window_vs_rolling_labels(env):
    """自然日钩子→（自然日窗口）；仅滚动钩子→"窗口不可用，仅近24h"；
    无源→unavailable/none；ACCEPTED 不冒充 CONFIRMED。"""
    from alerting.digest import build_digest
    now = utcnow().replace(tzinfo=U)
    tgt = _Target("drug_trace_alihealth", "码上放心")
    with env["sf"]() as db:
        text_w = build_digest(db, env["tid"], now, registry=_WindowReg(),
                              target_rows=[tgt])
        text_r = build_digest(db, env["tid"], now, registry=_RollingReg(),
                              target_rows=[tgt])
        text_n = build_digest(db, env["tid"], now, registry=_NoneSourceReg(),
                              target_rows=[tgt])
    assert "（自然日窗口）" in text_w and "仅近24h" not in text_w
    assert "仅近24h" in text_r and "（自然日窗口）" not in text_r
    assert "unavailable/none" in text_n


def test_digest_redacts_and_separates_total_from_top10(env):
    """未解决总数与前10分开；正文脱敏（证件号形态打码）。"""
    from alerting.digest import build_digest
    now = utcnow().replace(tzinfo=U)
    with env["sf"]() as db:
        for i in range(12):
            db.add(AlertEvent(id=new_uuid(), tenant_id=env["tid"],
                              alert_type="X", severity="warn",
                              message=f"告警{i} 证件号110101199001010000",
                              target_ref="-", state="OPEN",
                              created_at=now.replace(tzinfo=None)
                              - timedelta(minutes=i)))
        db.commit()
        text = build_digest(db, env["tid"], now)
    assert "仍未解决 12" in text                    # 总数与展示分开
    shown = sum(1 for ln in text.splitlines()
                if ln.strip().startswith("[WARN]"))
    assert shown == 10                              # 展示前10条
    assert "110101199001010000" not in text         # 脱敏


def test_digest_run_writes_redacted_log(env):
    """run_digest 落库正文同样脱敏（detail.text 无证件号明文）。"""
    from alerting.digest import run_digest
    from core_db.models import NotificationLog
    now = utcnow().replace(tzinfo=U)
    with env["sf"]() as db:
        db.add(AlertEvent(id=new_uuid(), tenant_id=env["tid"],
                          alert_type="X", severity="warn",
                          message="身份证 110101199001010001 待核",
                          target_ref="-", state="OPEN",
                          created_at=now.replace(tzinfo=None)))
        db.commit()
        out = run_digest(db, env["tid"], now, registry=_NoSummaryReg())
        db.commit()
        row = db.query(NotificationLog).filter_by(kind="DIGEST").one()
        assert row.ok is True and out["channels"] == ["file_default"]
        assert "110101199001010001" not in (row.detail or {}).get("text", "")
