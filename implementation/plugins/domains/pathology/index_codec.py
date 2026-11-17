"""病理索引编解码（pathology-internal-v1 / 正式规范草案占位）。

- 8段：证件类型(3) 证件号码 就诊类型(1) 病理申请单号 病理号 申请时间(14)
        检查机构代码(12) 申请类别(1)。
- 不对院内7位病理号补位；不强制所有实例总长79（09 §2.8）。
- 正式医保编码的段宽/总长规则未经确认（B09），formal 草案 Profile 仅校验段形态并
  将总长与官方示例长度比对，不一致时保持 CANDIDATE 并产出 QF-INDEX-FORMAT。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SEGMENTS = ["id_type", "id_no", "encounter_kind", "application_no",
            "pathology_no", "application_time", "org_code", "category"]

OFFICIAL_EXAMPLE_TOTAL_LENGTHS = [79, 86]

_SEGMENT_RULES = {
    "id_type": (r"^\d{3}$", "3位GA/T 2000.156代码"),
    "id_no": (r"^[0-9A-Za-z]{1,18}$", "按证件类型；1-18位字母数字"),
    "encounter_kind": (r"^[123]$", "1/2/3"),
    "application_no": (r"^[0-9A-Za-z]{1,20}$", "机构自编；1-20位"),
    "pathology_no": (r"^[0-9A-Za-z]{1,20}$", "病理科自编原值；不补位"),
    "application_time": (r"^\d{14}$", "YYYYMMDDHHMMSS 14位"),
    "org_code": (r"^[0-9A-Za-z]{12}$", "12位医保代码"),
    "category": (r"^[1-8]$", "1—8"),
}


@dataclass
class IndexInput:
    id_type: str | None
    id_no: str | None
    encounter_kind: str | None
    application_no: str | None
    pathology_no: str | None
    application_time: str | None   # 已是 YYYYMMDDHHMMSS
    org_code: str | None
    category: str | None

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in SEGMENTS}


def format_application_time(dt) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y%m%d%H%M%S")


def validate_segment(name: str, value: str | None) -> str | None:
    if value is None or value == "":
        return f"segment {name} missing"
    pattern, desc = _SEGMENT_RULES[name]
    if not re.fullmatch(pattern, value):
        return f"segment {name}={value!r} violates {desc}"
    return None


def encode(inp: IndexInput, profile: str = "pathology-internal-v1") -> dict:
    """返回 segments/errors/serialized/length/status。

    pathology-internal-v1：'-' 连接原值，无总长约束（内部追溯用）。
    pathology-insurance-formal-draft：段校验 + 总长与官方示例长度比对。
    """
    errors = [e for k in SEGMENTS if (e := validate_segment(k, getattr(inp, k)))]
    parts = [str(getattr(inp, k) or "") for k in SEGMENTS]
    if profile == "pathology-internal-v1":
        serialized = "-".join(parts)
        return {"segments": inp.to_dict(), "errors": errors,
                "serialized": serialized, "length": len(serialized),
                "status": "VALID" if not errors else "INVALID",
                "profile": profile}
    if profile == "pathology-insurance-formal-draft":
        serialized = "".join(parts)
        length = len(serialized)
        length_ok = length in OFFICIAL_EXAMPLE_TOTAL_LENGTHS
        if errors:
            return {"segments": inp.to_dict(), "errors": errors,
                    "serialized": serialized, "length": length,
                    "status": "INVALID", "profile": profile}
        if not length_ok:
            return {"segments": inp.to_dict(), "errors": [
                f"total length {length} not in official example lengths "
                f"{OFFICIAL_EXAMPLE_TOTAL_LENGTHS}; segment widths unconfirmed (B09), "
                f"local values are NOT padded to force a length"],
                "serialized": serialized, "length": length,
                "status": "CANDIDATE_UNCONFIRMED", "profile": profile}
        return {"segments": inp.to_dict(), "errors": [], "serialized": serialized,
                "length": length, "status": "VALID", "profile": profile}
    raise ValueError(f"unknown index profile: {profile}")


# ==================================================================
# RT-06 F04：无损解析/格式化纯辅助（不修改上方 encode 门禁）。
#
# 边界（93ebd53751dc40669f4c977781904f8b.txt 第19行 8 段分隔样本）：
# - 固定段宽：证件类型 3 / 就诊类型 1 / 申请时间 14（真实日历）/ 机构 12 /
#   类别 1；证件号码/申请单号/病理号为院内原值——**无统一段宽规定**，
#   不为凑总长补零/截断/硬限示例宽度；
# - 分隔串（8 段 `-` 连接）可无损解析；紧凑串（无分隔符）仅凭总长 79
#   **不足以推断分割**——无显式可变段宽 schema 时返回
#   AMBIGUOUS_SEGMENTS / SEGMENT_WIDTHS_UNCONFIRMED；
# - 79 位示例宽度 Profile 仅 EXAMPLE_ONLY，调用方显式选择。
# ==================================================================

class AmbiguousSegments(ValueError):
    """紧凑串无显式段宽 schema，无法无损分割。"""


class SegmentWidthsUnconfirmed(ValueError):
    """紧凑格式化缺少可变段宽 schema。"""


EXAMPLE_ONLY_WIDTHS = {"id_no": 18, "application_no": 10, "pathology_no": 20}


def _check_calendar(application_time: str) -> None:
    """时间段（14位）做真实日历校验（20261305 之类拒绝）。"""
    import datetime as _dt
    t = str(application_time or "")
    if len(t) != 14 or not t.isdigit():
        raise ValueError(f"时间段须 14 位数字：{t!r}")
    try:
        _dt.datetime.strptime(t, "%Y%m%d%H%M%S")
    except ValueError:
        raise ValueError(f"时间段不是真实日历日期时间：{t!r}")


def format_nhsa_pathology_index(segments: dict, *, with_hyphen: bool = True,
                                widths: dict | None = None) -> str:
    """段 dict → 编码串。

    - with_hyphen=True：8 段原值 `-` 连接（无损；前导零/原值保留）；
    - with_hyphen=False：紧凑串——**必须**给全部可变段宽 widths，否则
      SegmentWidthsUnconfirmed；不做来源外补位。
    段校验复用既有 _SEGMENT_RULES（encode 门禁同一事实源）。
    """
    missing = [k for k in SEGMENTS if not str(segments.get(k, "") or "")]
    if missing:
        raise ValueError(f"缺段：{missing}")
    errors = [e for k in SEGMENTS
              if (e := validate_segment(k, str(segments[k])))]
    _check_calendar(str(segments.get("application_time", "")))
    if errors:
        raise ValueError("; ".join(errors))
    if with_hyphen:
        return "-".join(str(segments[k]) for k in SEGMENTS)
    if not widths or any(k not in widths for k in
                         ("id_no", "application_no", "pathology_no")):
        raise SegmentWidthsUnconfirmed(
            "紧凑串需要全部可变段宽 schema（id_no/application_no/"
            "pathology_no）；仅总长 79 不足以推断分割")
    for k, w in widths.items():
        if len(str(segments[k])) != int(w):
            raise ValueError(
                f"段 {k} 宽度与 schema 不符：{len(str(segments[k]))}!={w}")
    return "".join(str(segments[k]) for k in SEGMENTS)


def parse_nhsa_pathology_index(code: str, *,
                               widths: dict | None = None) -> dict:
    """编码串 → 段 dict（原值/前导零无损保留）。

    - 分隔串：按 8 段 `-` 拆分（段数不符/固定段违规 → ValueError）；
    - 紧凑串：必须给 widths schema，否则 AmbiguousSegments；固定段宽+schema
      推出各段并复用 _SEGMENT_RULES 校验。
    """
    raw = str(code or "").strip()
    if "-" in raw:
        parts = raw.split("-")
        if len(parts) != len(SEGMENTS):
            raise ValueError(
                f"分隔段数须 {len(SEGMENTS)}，实际 {len(parts)}")
        segments = dict(zip(SEGMENTS, parts))
        errors = [e for k in SEGMENTS
                  if (e := validate_segment(k, str(segments[k])))]
        _check_calendar(str(segments.get("application_time", "")))
        if errors:
            raise ValueError("; ".join(errors))
        return segments
    if not widths or any(k not in widths for k in
                         ("id_no", "application_no", "pathology_no")):
        raise AmbiguousSegments(
            "紧凑串无分隔符：需显式可变段宽 schema 才能无损分割"
            "（仅总长 79 不足以推断分割）")
    # 固定段宽从既有规则推导（3/1/14/12/1），可变段宽来自显式 schema
    widths_full = {"id_type": 3, "encounter_kind": 1,
                   "application_time": 14, "org_code": 12, "category": 1,
                   **{k: int(v) for k, v in widths.items()}}
    total = sum(widths_full.values())
    if len(raw) != total:
        raise ValueError(
            f"紧凑总长 {len(raw)} 与固定段+段宽 schema 不符（应 {total}）")
    out: dict = {}
    pos = 0
    for k in SEGMENTS:
        w = widths_full[k]
        out[k] = raw[pos:pos + w]
        pos += w
    errors = [e for k in SEGMENTS if (e := validate_segment(k, str(out[k])))]
    if errors:
        raise ValueError("; ".join(errors))
    return out
