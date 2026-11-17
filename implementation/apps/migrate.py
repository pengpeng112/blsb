"""编号迁移入口（W14）：容器/部署用。

用法：python -m apps.migrate [--url <db_url>]
执行核心+已启用插件的编号迁移（幂等可重复；PG路径含004/006的ALTER迁移）。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_HERE, os.path.join(_HERE, "packages"), os.path.join(_HERE, "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _bootstrap  # noqa: F401,E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("BLYB_DATABASE_URL", ""))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.url:
        os.environ["BLYB_DATABASE_URL"] = args.url
    from core_db import make_engine, run_migrations
    import app_runtime
    registry = app_runtime.load_registry()  # 按BLYB_PLUGINS；默认全量（含002病理）
    engine = make_engine()
    results = run_migrations(engine, extra_migrations=registry.migrations)
    for step, name, result in results:
        print(f"{step:>4} {name:<40} {result}")
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
