"""5 段 cron 解析器 + 维护窗口判断（LT-04 A 段；标准库实现，固定 UTC+8）。

中国无夏令时：CST 固定偏移 +8，避免 Windows 缺 tzdata 依赖。所有对外
datetime 均为 aware；next_after 接受/返回 UTC。搜索上限 366 天（含闰年），
超限抛 NextFireOutOfRange（不能把"有效但超范围"当非法表达式，R04）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
UTC = timezone.utc

_FIELDS = ("minute", "hour", "day", "month", "dow")
_RANGES = {"minute": (0, 59), "hour": (0, 23), "day": (1, 31),
           "month": (1, 12), "dow": (0, 7)}   # dow: 0 和 7 都是周日
_SEARCH_LIMIT_DAYS = 366


class CronSyntaxError(ValueError):
    """表达式语法非法（中文说明）。"""


class NextFireOutOfRange(ValueError):
    """表达式有效但 366 天内无触发（如 2 月 30 日）。"""


def to_aware_utc(dt: datetime) -> datetime:
    """RT-03 单一时间规范化：naive 视为 UTC 绑定；带 offset 的先转 aware UTC。

    早报/调度/告警共用——`utcnow()` 的 naive UTC 直接 `astimezone(CST)` 会被
    解释为宿主本地时区（Windows +08 机器上错 8 小时），必须先经本函数。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_field(expr: str, field: str) -> set[int]:
    lo, hi = _RANGES[field]
    out: set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            raise CronSyntaxError(f"{field} 字段含空段：{expr!r}")
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                raise CronSyntaxError(f"{field} 字段步长非法：{part!r}（须为正整数）")
            step = int(step_s)
        else:
            base = part
        if base == "*":
            a, b = lo, hi
        elif "-" in base and not base.startswith("-"):
            a_s, b_s = base.split("-", 1)
            if not (a_s.isdigit() and b_s.isdigit()):
                raise CronSyntaxError(f"{field} 字段范围非法：{part!r}")
            a, b = int(a_s), int(b_s)
        elif base.isdigit():
            a = b = int(base)
        else:
            raise CronSyntaxError(f"{field} 字段取值非法：{part!r}")
        if a < lo or b > hi or a > b:
            raise CronSyntaxError(
                f"{field} 字段越界：{part!r}（允许 {lo}—{hi}）")
        if field == "dow" and 7 in out | set(range(a, b + 1, step)):
            pass                       # 7=周日，稍后归一到 0
        out.update(range(a, b + 1, step))
    if field == "dow" and 7 in out:
        out.discard(7)
        out.add(0)
    if not out:
        raise CronSyntaxError(f"{field} 字段解析结果为空：{expr!r}")
    return out


class CronExpr:
    """标准 5 段 cron（Vixie 语义：日与周同时受限时取"或"）。"""

    def __init__(self, expr: str):
        if not isinstance(expr, str):
            raise CronSyntaxError("cron 表达式必须是字符串")
        parts = expr.split()
        if len(parts) != 5:
            raise CronSyntaxError(
                f"cron 必须为 5 段（分 时 日 月 周），当前 {len(parts)} 段：{expr!r}")
        self.expr = expr
        self.minutes = _parse_field(parts[0], "minute")
        self.hours = _parse_field(parts[1], "hour")
        self.days = _parse_field(parts[2], "day")
        self.months = _parse_field(parts[3], "month")
        self.dows = _parse_field(parts[4], "dow")
        self._day_restricted = parts[2] != "*"
        self._dow_restricted = parts[4] != "*"

    def matches(self, dt_cst: datetime) -> bool:
        if dt_cst.minute not in self.minutes or dt_cst.hour not in self.hours \
                or dt_cst.month not in self.months:
            return False
        dom = dt_cst.day in self.days
        dow = (dt_cst.weekday() + 1) % 7 in self.dows   # Mon=0 → cron 周一=1
        if self._day_restricted and self._dow_restricted:
            return dom or dow                            # Vixie "或"
        return dom and dow

    def next_after(self, dt_utc: datetime) -> datetime:
        """dt_utc 之后（不含）的下一次触发，返回 UTC（aware）。分钟粒度。"""
        if dt_utc.tzinfo is None:
            raise ValueError("next_after 需要 aware datetime（UTC）")
        cur = dt_utc.astimezone(CST).replace(second=0, microsecond=0) \
            + timedelta(minutes=1)
        limit = dt_utc.astimezone(CST) + timedelta(days=_SEARCH_LIMIT_DAYS)
        while cur <= limit:
            if self.matches(cur):
                return cur.astimezone(UTC)
            cur += timedelta(minutes=1)
        raise NextFireOutOfRange(
            f"表达式有效但 {_SEARCH_LIMIT_DAYS} 天内无触发：{self.expr!r}")

    def previous_at_or_before(self, dt_utc: datetime) -> datetime:
        """RT-03（S03）：<= dt_utc 的最近一次合法触发（含 dt_utc 所在分钟）。

        RUN_ONCE 补跑用它取 [旧 next_fire_at, now] 内最近合法计划槽位——
        不从最旧槽位逐次 next_after 遍历，也不能拿 now 冒充计划时间。
        与 next_after 同一 matches()（Vixie 日/周 OR）与同一 366 天搜索上限；
        超限抛 NextFireOutOfRange（"有效但范围内无触发"≠非法表达式）。"""
        if dt_utc.tzinfo is None:
            raise ValueError("previous_at_or_before 需要 aware datetime（UTC）")
        cur = dt_utc.astimezone(CST).replace(second=0, microsecond=0)
        limit = cur - timedelta(days=_SEARCH_LIMIT_DAYS)
        while cur >= limit:
            if self.matches(cur):
                return cur.astimezone(UTC)
            cur -= timedelta(minutes=1)
        raise NextFireOutOfRange(
            f"表达式有效但向前 {_SEARCH_LIMIT_DAYS} 天内无触发：{self.expr!r}")

    def preview(self, dt_utc: datetime, count: int = 5) -> list[datetime]:
        out, cur = [], dt_utc
        for _ in range(count):
            cur = self.next_after(cur)
            out.append(cur)
        return out


def in_blackout(dt_utc: datetime, blackout: list[dict] | None) -> bool:
    """维护窗口判断（CST 时钟；支持跨午夜窗口如 23:30—00:30）。"""
    if not blackout:
        return False
    t = dt_utc.astimezone(CST)
    minutes = t.hour * 60 + t.minute
    for win in blackout:
        try:
            sh, sm = str(win.get("start", "")).split(":")
            eh, em = str(win.get("end", "")).split(":")
            s, e = int(sh) * 60 + int(sm), int(eh) * 60 + int(em)
        except (ValueError, AttributeError):
            continue                      # 畸形窗口忽略（配置校验在 API 层）
        if s == e:
            continue
        if s < e:
            if s <= minutes < e:
                return True
        else:                             # 跨午夜
            if minutes >= s or minutes < e:
                return True
    return False


def render_payload(template: dict, fire_utc: datetime) -> dict:
    """渲染 payload 占位符：{fire_date}/{fire_date_minus_1}/{fire_time}（CST）。

    fire_date_minus_1 用 CST 触发日前一天——00:05 触发时业务日期=昨天
    （10-31 容器 UTC 日界 off-by-one 事故的反例语义）。
    """
    if not template:
        return {}
    t = fire_utc.astimezone(CST)
    d = t.date()
    subs = {
        "{fire_date}": d.isoformat(),
        "{fire_date_minus_1}": (d - timedelta(days=1)).isoformat(),
        "{fire_time}": t.isoformat(),
    }
    out: dict = {}
    for k, v in template.items():
        if isinstance(v, str):
            for ph, rep in subs.items():
                v = v.replace(ph, rep)
        out[k] = v
    return out
