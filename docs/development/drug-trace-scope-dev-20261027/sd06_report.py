"""SD-06：205 张 UNKNOWN vs 原厂家 TRACE_UPLOAD_TIME_LOG 宏观对照报告生成器。

纯函数 build_comparison（供测试）+ main（读 evidence 生成 SD-06-report.md）。
边界：该日志无单号明细，只能做宏观覆盖对照；逐单确认仍需平台查询或人工材料。
不修改 205 张任何状态、不重发。
"""
from __future__ import annotations

import json
import glob
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIV = HERE.parents[2] / ".ai/private/drug-trace-scope-dev-20261027"


def build_comparison(daily_rows: list[dict], hourly_rows: list[dict],
                     our_windows: list[dict]) -> dict:
    """纯聚合：原厂家按日/按小时活动 vs 我方 205 提交时间窗。

    daily_rows: [{BUSINESS_ID, D(YYYY-MM-DD), UPLOAD_STATUS, LOGS, REC, SUCC, FAIL}]
    hourly_rows: [{BUSINESS_ID, H(YYYY-MM-DD HH24), UPLOAD_STATUS, REC, SUCC, FAIL}]
    our_windows: [{day, hours:[HH...], submissions}]
    返回 {vendor_by_day, our_windows:[...含 overlap 判定], macro_notes}
    """
    vendor_by_day: dict[str, dict] = {}
    for r in daily_rows:
        day = str(r.get("D") or "")
        biz = str(r.get("BUSINESS_ID") or "")
        d = vendor_by_day.setdefault(day, {})
        agg = d.setdefault(biz, {"logs": 0, "records": 0, "success": 0,
                                 "fail": 0, "status": set()})
        agg["logs"] += int(r.get("LOGS") or 0)
        agg["records"] += int(r.get("REC") or 0)
        agg["success"] += int(r.get("SUCC") or 0)
        agg["fail"] += int(r.get("FAIL") or 0)
        agg["status"].add(str(r.get("UPLOAD_STATUS")))
    for day in vendor_by_day:
        for biz in vendor_by_day[day]:
            vendor_by_day[day][biz]["status"] = sorted(
                vendor_by_day[day][biz]["status"])
    vendor_hours: dict[str, dict[str, int]] = {}
    for r in hourly_rows:
        hour = str(r.get("H") or "")
        biz = str(r.get("BUSINESS_ID") or "")
        h = vendor_hours.setdefault(hour, {})
        h[biz] = h.get(biz, 0) + int(r.get("REC") or 0) + int(r.get("FAIL") or 0)
    out_windows = []
    for w in our_windows:
        day = str(w.get("day") or "")
        hours = [str(x) for x in (w.get("hours") or [])]
        vendor_day = vendor_by_day.get(day, {})
        day_records = sum(v["records"] for v in vendor_day.values())
        overlap_hours = sorted({
            f"{day} {hh}" for hh in hours
            if sum(vendor_hours.get(f"{day} {hh}", {}).values()) > 0})
        out_windows.append({
            "day": day, "hours": hours,
            "submissions": int(w.get("submissions") or 0),
            "vendor_day_records_total": day_records,
            "vendor_active_same_day": day_records > 0,
            "vendor_activity_in_our_hours": overlap_hours,
            "macro_read": (
                "vendor uploaded records the same day" if day_records > 0
                else "vendor uploaded ZERO records that day"),
        })
    return {
        "vendor_by_day": vendor_by_day,
        "our_windows": out_windows,
        "macro_notes": [
            "TRACE_UPLOAD_TIME_LOG has no bill-level detail: macro coverage only",
            "no overlap in our exact send hours does NOT prove the vendor never "
            "uploaded the same bills in other windows",
            "per-bill confirmation requires platform query or vendor materials",
            "BUSINESS_ID 3503/3505 semantics unconfirmed (not guessed)",
        ],
    }


def _latest_json(pattern: str) -> dict:
    match = sorted(glob.glob(str(HERE / pattern)))
    if not match:
        raise SystemExit(f"no evidence file for {pattern}")
    return json.loads(Path(match[-1]).read_text(encoding="utf8"))


def main() -> None:
    his = _latest_json("evidence/sd01-his-2*.json")
    appdb = _latest_json("evidence/sd01-appdb-*.json")
    detail_file = sorted(PRIV.glob("sd06-unknown205-*.json"))[-1]
    detail = json.loads(detail_file.read_text(encoding="utf8"))
    daily = his["sd06_upload_log_daily"]["rows"]
    hourly = his["sd06_upload_log_hourly"]["rows"]
    from collections import Counter
    by_day = Counter(r["created_gmt8"][:10] for r in detail["submissions"])
    hour_sets: dict[str, set] = {}
    for r in detail["submissions"]:
        hour_sets.setdefault(r["created_gmt8"][:10], set()).add(
            r["created_gmt8"][11:13])
    our_windows = [{"day": d, "hours": sorted(hour_sets[d]),
                    "submissions": by_day[d]}
                   for d in sorted(by_day)]
    cmp = build_comparison(daily, hourly, our_windows)

    lines = []
    lines.append("# SD-06：205 张历史 UNKNOWN 与原厂家上传日志宏观对照报告"
                 "（DTR-SCOPE-DEV-20261027）\n")
    lines.append("**边界声明（先行）**：`TRACE_UPLOAD_TIME_LOG` 是原厂家上传程序的"
                 "时间段日志，**无单号明细**——本报告只能做宏观覆盖对照；"
                 "逐单确认必须走平台查询或人工材料。本任务**未修改 205 张任何状态、"
                 "未重发**。BUSINESS_ID 3503/3505 含义未确认，不猜测。\n")
    lines.append("## 我方 205 张提交时间窗（应用库只读实测，GMT+8）\n")
    lines.append("| 日期 | 张数 | 我方提交时段 | 原厂家当日上传(rec) | "
                 "我方时段内原厂家活动 | 宏观判读 |")
    lines.append("|---|---|---|---|---|---|")
    for w in cmp["our_windows"]:
        lines.append(
            f"| {w['day']} | {w['submissions']} | "
            f"{','.join(w['hours'])} 时 | {w['vendor_day_records_total']} | "
            f"{','.join(w['vendor_activity_in_our_hours']) or '无'} | "
            f"{w['macro_read']} |")
    lines.append("\n我方时段：10-25 04:28–04:36（204 张）、10-23 17:12（1 张）；"
                 "原厂家在该两日的对应小时均零记录（10-25 全天 rec=0）。\n")
    lines.append("## 原厂家按日上传活动（biz 3503=每小时批；3505=每日批，"
                 "10-22 起停滞）\n")
    lines.append("| 日期 | biz | status | logs | records | success | fail |")
    lines.append("|---|---|---|---|---|---|---|")
    for day in sorted(cmp["vendor_by_day"]):
        for biz, v in sorted(cmp["vendor_by_day"][day].items()):
            lines.append(f"| {day} | {biz} | {'/'.join(v['status'])} | "
                         f"{v['logs']} | {v['records']} | {v['success']} | "
                         f"{v['fail']} |")
    lines.append("\n## ⚠️ 双上报风险（持续）\n")
    lines.append("biz 3503 程序**仍在运行且每天有成功上传**（10-26=37、10-27=78 条）；"
                 "biz 3505 10-18..21 每日 2102~2899 条成功后，10-22 起仅剩失败重试行。"
                 "用户正在核实原厂家是否仍向平台传数——**核实结论前，住院/退药发送侧"
                 "保持 BLOCKED**（与执行书 §五.7 一致）。\n")
    lines.append("## 建议逐单平台核查清单\n")
    lines.append(f"- 完整 205 张逐单清单（submission_id / bill_code / source_bill_no / "
                 f"创建与末次尝试时间 / 尝试次数）已按单号级敏感约定存于受限目录：\n"
                 f"  `.ai/private/drug-trace-scope-dev-20261027/{detail_file.name}`"
                 f"（ACL 当前用户+SYSTEM）。\n")
    lines.append("- 核查口径建议：按各单 bill_type/query_bill_type 冻结身份 + "
                 "bill_code + 创建日±1 天平台查询（与 10-26 平台人工核查同法）；"
                 "NOT_FOUND 单据再比对原厂家材料决定处置；**禁止盲重发**。\n")
    lines.append("- 证据链：evidence/sd01-his-20261027T135850Z.json"
                 "（sd06_upload_log_daily/hourly）、"
                 "evidence/sd01-appdb-20261027T135459Z.json（205 时间分布）、"
                 "sd06_pull_205.py（可复跑）。\n")
    (HERE / "SD-06-report.md").write_text("\n".join(lines), encoding="utf8")
    print("written SD-06-report.md;",
          "windows:", [(w["day"], w["submissions"],
                        w["vendor_day_records_total"]) for w in cmp["our_windows"]])


if __name__ == "__main__":
    main()
