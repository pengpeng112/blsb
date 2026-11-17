"""LT-04 cron 解析器单测（≥15；含 10-31 容器 UTC 日界 bug 反例）。"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from workflow_core.cron import (CST, CronExpr, CronSyntaxError,  # noqa: E402
                                NextFireOutOfRange, in_blackout,
                                render_payload)

U = timezone.utc


def _cst(y, m, d, h, mi):
    return datetime(y, m, d, h, mi, tzinfo=CST)


def test_expr_15min_offhours_matches_current_pattern():
    e = CronExpr("*/15 0-5,9-23 * * *")          # 传染病现行
    assert e.matches(_cst(2026, 11, 10, 3, 45))
    assert not e.matches(_cst(2026, 11, 10, 6, 0))   # 6—8 点窗口外
    assert not e.matches(_cst(2026, 11, 10, 9, 7))   # 非 */15 分钟


def test_expr_daily_1am():
    e = CronExpr("0 1 * * *")                     # 药品现行 01:00
    assert e.matches(_cst(2026, 11, 10, 1, 0))
    assert not e.matches(_cst(2026, 11, 10, 1, 1))


def test_expr_2358_and_0005():
    assert CronExpr("58 23 * * *").matches(_cst(2026, 11, 10, 23, 58))
    assert CronExpr("5 0 * * *").matches(_cst(2026, 11, 11, 0, 5))


def test_expr_month_end_and_leap():
    e = CronExpr("0 0 31 * *")
    assert e.matches(_cst(2026, 1, 31, 0, 0))
    assert not e.matches(_cst(2026, 11, 30, 0, 0))
    leap = CronExpr("0 0 29 2 *")
    assert leap.matches(_cst(2028, 2, 29, 0, 0))     # 闰年


def test_expr_dow_0_and_7_sunday():
    assert CronExpr("0 0 * * 0").matches(_cst(2026, 11, 8, 0, 0))   # 周日
    assert CronExpr("0 0 * * 7").matches(_cst(2026, 11, 8, 0, 0))   # 7=周日
    assert not CronExpr("0 0 * * 7").matches(_cst(2026, 11, 9, 0, 0))


def test_expr_dow_range_step():
    e = CronExpr("0 9 * * 1-5")                      # 工作日
    assert e.matches(_cst(2026, 11, 10, 9, 0))       # 周二
    assert not e.matches(_cst(2026, 11, 8, 9, 0))    # 周日


def test_expr_dom_dow_vixie_or():
    # 日=1 且 周=周一：Vixie 语义"或"——1 号或周一都触发
    e = CronExpr("0 0 1 * 1")
    assert e.matches(_cst(2026, 11, 1, 0, 0))        # 1 号（周日）
    assert e.matches(_cst(2026, 11, 9, 0, 0))        # 周一（非 1 号）
    assert not e.matches(_cst(2026, 11, 10, 0, 0))   # 周二非 1 号


def test_syntax_errors_chinese():
    for bad in ("* * * *", "*/15 * * * * *", "61 * * * *", "* 24 * * *",
                "abc * * * *", "*/0 * * * *", "5-1 * * * *"):
        with pytest.raises(CronSyntaxError, match=r".*"):
            CronExpr(bad)


def test_next_after_utc_conversion():
    e = CronExpr("0 1 * * *")
    base = datetime(2026, 11, 10, 0, 0, tzinfo=U)     # CST 08:00
    nxt = e.next_after(base)
    assert nxt == datetime(2026, 11, 10, 17, 0, tzinfo=U)  # CST 次日 01:00


def test_next_after_cross_month():
    e = CronExpr("0 0 1 * *")
    nxt = e.next_after(datetime(2026, 11, 10, 0, 0, tzinfo=U))
    assert nxt.astimezone(CST).month == 12 and nxt.astimezone(CST).day == 1


def test_next_after_out_of_range():
    with pytest.raises(NextFireOutOfRange):
        CronExpr("0 0 30 2 *").next_after(datetime(2026, 1, 1, tzinfo=U))


def test_next_after_requires_aware():
    with pytest.raises(ValueError):
        CronExpr("* * * * *").next_after(datetime(2026, 1, 1))


def test_blackout_window():
    bo = [{"start": "06:00", "end": "08:30"}]
    assert in_blackout(datetime(2026, 11, 10, 22, 30, tzinfo=U), bo)  # CST 06:30
    assert in_blackout(datetime(2026, 11, 10, 0, 29, tzinfo=U), bo)   # CST 08:29（含）
    assert not in_blackout(datetime(2026, 11, 10, 0, 30, tzinfo=U), bo)  # CST 08:30（半开）


def test_blackout_cross_midnight():
    bo = [{"start": "23:30", "end": "00:30"}]
    assert in_blackout(datetime(2026, 11, 10, 15, 45, tzinfo=U), bo)  # CST 23:45
    assert in_blackout(datetime(2026, 11, 10, 16, 10, tzinfo=U), bo)  # CST 00:10
    assert not in_blackout(datetime(2026, 11, 10, 14, 0, tzinfo=U), bo)  # CST 22:00


def test_render_payload_fire_date_minus_1_at_0005_cst():
    """10-31 容器 UTC 日界 off-by-one 事故反例：CST 00:05 触发时
    {fire_date_minus_1}=前一天（UTC 视角是昨天 16:05）。"""
    fire_utc = datetime(2026, 10, 30, 16, 5, tzinfo=U)   # CST 10-31 00:05
    out = render_payload({"business_date": "{fire_date_minus_1}",
                          "at": "{fire_time}"}, fire_utc)
    assert out["business_date"] == "2026-10-30"
    assert out["at"].startswith("2026-10-31T00:05")


def test_render_payload_fire_date():
    fire_utc = datetime(2026, 11, 9, 17, 0, tzinfo=U)    # CST 11-10 01:00
    out = render_payload({"d": "{fire_date}", "n": 5, "s": "x{fire_date}y"},
                         fire_utc)
    assert out["d"] == "2026-11-10" and out["n"] == 5 and out["s"] == "x2026-11-10y"


def test_preview_count():
    e = CronExpr("0 */2 * * *")
    nxt = e.preview(datetime(2026, 11, 10, 0, 0, tzinfo=U), 3)
    assert len(nxt) == 3 and nxt[0] < nxt[1] < nxt[2]
