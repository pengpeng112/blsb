"""RT-04 A4：影子 vs 外部台账对账（纯函数 + CLI；只读、无自动重发）。

- 两侧集合身份统一 ``(source_instance, table, source_record_key)``；
- 版本比较统一 **source_op_time + 稳定源内容摘要**（外部副本无内容摘要时按
  op_time 比较）；**禁止**拿上传 operationTime、导入时间或 snapshot digest
  直接比当作源版本；
- 输出分类：matched / only_in_external / only_in_shadow / version_mismatch /
  unknown_source_time / duplicate / conflict——缺源时间的记录单列
  （不静默剔除、不擅自补当前时间）；
- 报告只含身份键/时间/摘要前缀/计数，**不含姓名/证件/患者信息**；
- 真实七天连续运行与真实 state 副本导入仍外部阻断（本轮为合成场景实跑）。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field

TARGET_CODE = "inf_cdc_frontend"


@dataclass(frozen=True)
class RecKey:
    source_instance: str
    table: str
    record_key: str

    def as_str(self) -> str:
        return f"{self.source_instance}/{self.table}/{self.record_key}"


@dataclass
class ShadowVer:
    op_time: str                    # 规范化源时间（""=未知，单列不比较）
    version_digest: str             # 源内容摘要（前缀展示）


@dataclass
class ExternalVer:
    op_time: str                    # 源 opTime（""=未知）
    bucket: str
    evidence_level: str = ""


@dataclass
class ReconcileReport:
    matched: list[str] = field(default_factory=list)
    only_in_external: list[str] = field(default_factory=list)
    only_in_shadow: list[str] = field(default_factory=list)
    version_mismatch: list[dict] = field(default_factory=list)
    unknown_source_time: list[dict] = field(default_factory=list)
    duplicate: list[str] = field(default_factory=list)
    conflict: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"matched": len(self.matched),
                "only_in_external": len(self.only_in_external),
                "only_in_shadow": len(self.only_in_shadow),
                "version_mismatch": len(self.version_mismatch),
                "unknown_source_time": len(self.unknown_source_time),
                "duplicate": len(self.duplicate),
                "conflict": len(self.conflict),
                "total_shadow": None, "total_external": None}

    def to_dict(self) -> dict:
        out = self.summary()
        out.update({
            "matched_keys": self.matched,
            "only_in_external_keys": self.only_in_external,
            "only_in_shadow_keys": self.only_in_shadow,
            "version_mismatch_details": self.version_mismatch,
            "unknown_source_time_details": self.unknown_source_time,
            "duplicate_keys": self.duplicate,
            "conflict_keys": self.conflict})
        return out


# ---------------------------------------------------------------- 采集（只读）
def collect_shadow(db, tenant_id: str, *, source_instance: str | None = None,
                   tables: list[str] | None = None,
                   since: str = "", until: str = "") -> dict[RecKey, ShadowVer]:
    """影子侧（infectious_shadow_record）：按源时间窗过滤（缺窗=全量）。"""
    from core_db.models import InfectiousShadowRecord
    q = db.query(InfectiousShadowRecord).filter_by(
        tenant_id=tenant_id, target_code=TARGET_CODE)
    if source_instance:
        q = q.filter_by(source_instance=source_instance)
    if tables:
        q = q.filter(InfectiousShadowRecord.table_key.in_(tables))
    from sqlalchemy import or_
    if since:
        # 未知源时间（""）行不静默剔除：窗口内 OR 未知单列
        q = q.filter(or_(InfectiousShadowRecord.source_op_time >= since,
                         InfectiousShadowRecord.source_op_time == ""))
    if until:
        q = q.filter(or_(InfectiousShadowRecord.source_op_time < until,
                         InfectiousShadowRecord.source_op_time == ""))
    out: dict[RecKey, ShadowVer] = {}
    for r in q.all():
        k = RecKey(r.source_instance, r.table_key, r.source_record_key)
        out[k] = ShadowVer(r.source_op_time or "",
                           (r.source_version_digest or "")[:16])
    return out


def collect_external(db, tenant_id: str, *, source_instance: str | None = None,
                     tables: list[str] | None = None,
                     since: str = "", until: str = "") -> dict[RecKey, ExternalVer]:
    """外部侧（external_submission_record，导入副本）：op_time=源 opTime
    （不是 last_attempt_at/导入时间）；未知保持空。"""
    from sqlalchemy import or_
    from core_db.models import ExternalSubmissionRecord
    q = db.query(ExternalSubmissionRecord).filter_by(
        tenant_id=tenant_id, target_code=TARGET_CODE)
    if source_instance:
        q = q.filter_by(source_instance=source_instance)
    if tables:
        keys = [f"{t}/%" for t in tables]
        q = q.filter(or_(*[ExternalSubmissionRecord.record_key.like(k)
                           for k in keys]))
    if since:
        q = q.filter(or_(ExternalSubmissionRecord.op_time >= since,
                         ExternalSubmissionRecord.op_time.is_(None)))
    if until:
        q = q.filter(or_(ExternalSubmissionRecord.op_time < until,
                         ExternalSubmissionRecord.op_time.is_(None)))
    seen: dict[RecKey, int] = {}
    out: dict[RecKey, ExternalVer] = {}
    for r in q.all():
        table, _, rid = r.record_key.partition("/")
        k = RecKey(r.source_instance, table, rid)
        seen[k] = seen.get(k, 0) + 1
        out[k] = ExternalVer(
            r.op_time.strftime("%Y-%m-%d %H:%M:%S") if r.op_time else "",
            r.bucket or "", r.evidence_level or "")
    return out, seen


# ---------------------------------------------------------------- 比较（纯函数）
def compare(shadow: dict[RecKey, ShadowVer],
            external: dict[RecKey, ExternalVer],
            *, shadow_conflicts: set[str] | None = None,
            external_duplicates: set[RecKey] | None = None) -> ReconcileReport:
    """集合比较（纯函数；不查库、不写库、不触发任何发送）。

    - 任一侧源时间未知 → unknown_source_time（不参与 matched/mismatch）；
    - 两侧均有时间：相等→matched；不等→version_mismatch（方向标注）；
    - 源内容摘要仅在影子侧可得：冲突状态（同时间不同内容）单独列出。
    """
    rep = ReconcileReport()
    shadow_conflicts = shadow_conflicts or set()
    external_duplicates = external_duplicates or set()
    all_keys = set(shadow) | set(external)
    for k in sorted(all_keys, key=lambda x: x.as_str()):
        s = shadow.get(k)
        e = external.get(k)
        ks = k.as_str()
        if s is None:
            rep.only_in_external.append(ks)
            continue
        if e is None:
            rep.only_in_shadow.append(ks)
            continue
        if ks in shadow_conflicts:
            rep.conflict.append(ks)          # 同源时间不同内容（影子侧显式冲突）
            continue
        if k in external_duplicates:
            rep.duplicate.append(ks)         # 外部副本内同身份重复行
            continue
        if not s.op_time or not e.op_time:
            rep.unknown_source_time.append({
                "key": ks, "shadow_op_time": s.op_time or None,
                "external_op_time": e.op_time or None,
                "note": "一侧源时间未知——单列，不擅自补时间/不参与版本比较"})
            continue
        if s.op_time == e.op_time:
            rep.matched.append(ks)
        else:
            rep.version_mismatch.append({
                "key": ks, "shadow_op_time": s.op_time,
                "external_op_time": e.op_time,
                "direction": "shadow_newer" if s.op_time > e.op_time
                else "external_newer",
                "shadow_version_digest_prefix": s.version_digest or None})
    return rep


def reconcile(db, tenant_id: str, *, source_instance: str | None = None,
              tables: list[str] | None = None,
              since: str = "", until: str = "") -> dict:
    """完整对账（只读）。返回 JSON 可序列化报告（无患者信息）。"""
    shadow = collect_shadow(db, tenant_id, source_instance=source_instance,
                            tables=tables, since=since, until=until)
    external, seen = collect_external(db, tenant_id,
                                      source_instance=source_instance,
                                      tables=tables, since=since, until=until)
    dup = {k for k, n in seen.items() if n > 1}
    from core_db.models import InfectiousShadowRecord
    q = db.query(InfectiousShadowRecord).filter_by(
        tenant_id=tenant_id, target_code=TARGET_CODE, state="CONFLICT")
    if source_instance:
        q = q.filter_by(source_instance=source_instance)
    conflicts = {f"{r.source_instance}/{r.table_key}/{r.source_record_key}"
                 for r in q.all()}
    rep = compare(shadow, external, shadow_conflicts=conflicts,
                  external_duplicates=dup)
    out = rep.to_dict()
    out["total_shadow"] = len(shadow)
    out["total_external"] = len(external)
    out["params"] = {"tenant_id": tenant_id, "target_code": TARGET_CODE,
                     "source_instance": source_instance or "(all)",
                     "tables": tables or "(all)",
                     "op_time_window": [since or "(open)", until or "(open)"]}
    out["note"] = ("平台本地影子对账（只读；无自动重发）；版本=source_op_time"
                   "+源内容摘要；上传时间/导入时间/snapshot digest 不参与比较")
    return out


# ---------------------------------------------------------------- CLI（只读）
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="传染病影子 vs 外部台账对账（只读；不发任何请求）")
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--target", default=TARGET_CODE)
    ap.add_argument("--source-instance", default="",
                    help="来源实例（空=全部实例）")
    ap.add_argument("--since", default="", help="源时间起（YYYY-MM-DD HH:MM:SS）")
    ap.add_argument("--until", default="", help="源时间止（开区间）")
    ap.add_argument("--tables", default="",
                    help="逗号分隔表键（空=全部）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args(argv)
    import os
    import sys
    _here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    for p in (_here, os.path.join(_here, "packages"),
              os.path.join(_here, "plugins")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import _bootstrap  # noqa: F401
    from core_db import make_engine, make_session_factory
    engine = make_engine()
    sf = make_session_factory(engine)
    try:
        with sf() as db:
            tables = [t.strip() for t in args.tables.split(",") if t.strip()] \
                or None
            rep = reconcile(db, args.tenant,
                            source_instance=args.source_instance or None,
                            tables=tables, since=args.since, until=args.until)
    finally:
        engine.dispose()
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        s = {k: v for k, v in rep.items()
             if isinstance(v, int) or k in ("params", "note")}
        print(json.dumps(s, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
