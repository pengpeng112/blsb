"""外部台账导入 CLI（LT-05 A3）。

用法：
    python -m apps.import_external_ledger --tenant <id> --target inf_cdc_frontend \
        --dir <副本目录> --source-tz CST [--source-instance host-<prod-host>] \
        [--source-label "..."] [--dry-run]

source-tz 必须显式（无偏移时间戳的解释时区）；dry-run 零写库只出报告。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _bootstrap  # noqa: E402,F401

from core_db import make_engine, make_session_factory  # noqa: E402
from core_db.models import Tenant  # noqa: E402

log = logging.getLogger("import_external_ledger")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="外部台账副本导入（只读生产副本）")
    ap.add_argument("--tenant", required=True, help="租户 code 或 id")
    ap.add_argument("--target", required=True, help="ReportTarget code")
    ap.add_argument("--dir", required=True, help="副本目录（本机路径）")
    ap.add_argument("--source-tz", required=True, choices=["UTC", "CST"],
                    help="副本内无偏移时间戳的解释时区（必填，不默认）")
    ap.add_argument("--source-instance", default="default",
                    help="来源实例标识（幂等身份组成；不同来源互不去重）")
    ap.add_argument("--source-label", default="", help="批次说明")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    engine = make_engine()
    sf = make_session_factory(engine)
    with sf() as db:
        t = (db.query(Tenant).filter_by(id=args.tenant).first()
             or db.query(Tenant).filter_by(code=args.tenant).first())
        if t is None:
            print(f"tenant {args.tenant!r} not found", file=sys.stderr)
            return 2
        from plugins.external_ledger import import_directory
        try:
            stats = import_directory(
                db, t.id, args.target, args.dir,
                source_label=args.source_label or args.dir,
                source_instance=args.source_instance,
                source_tz=args.source_tz, actor="cli",
                dry_run=args.dry_run)
            if not args.dry_run:
                db.commit()
        except ValueError as exc:
            print(f"导入拒绝: {exc}", file=sys.stderr)
            return 2
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=1))
    else:
        # RT-04 A3 两层计数口径：行级（read/valid/三类拒绝）与记录级
        # （total/created/updated/unchanged/sent-only）分开输出，不混加
        print(f"files={len(stats['files'])} rows_read={stats['rows_read']} "
              f"rows_valid={stats.get('rows_valid', 0)} "
              f"rejected={stats['rows_rejected']}"
              f"(parse={stats.get('rows_rejected_parse', 0)}"
              f"/shape={stats.get('rows_rejected_shape', 0)}"
              f"/privacy={stats.get('rows_rejected_privacy', 0)}) "
              f"records={stats.get('records_total', 0)} "
              f"created={stats.get('records_created', 0)} "
              f"updated={stats.get('records_updated', 0)} "
              f"unchanged={stats.get('records_unchanged', 0)} "
              f"sent_only={stats.get('sent_only_records', 0)} "
              f"dry_run={stats['dry_run']}")
        for r in stats.get("rejects", [])[:20]:
            print("  reject:", r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
