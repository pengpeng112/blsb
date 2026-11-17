"""metrics-only 独立进程入口（W11/G23）。

以 BLYB_PLUGINS=metrics 启动 core+storage+worker+Mock目的地，
不 import 病理插件、不创建病理表。启动后即可：
CSV导入(/api/v1/imports) → 映射 → 校验 → 冻结 → 双目标Mock
(push回执 + 视图发布)。

用法：python apps/run_metrics_only.py [--root <dir>]
环境：BLYB_DATABASE_URL / BLYB_EXCHANGE_DB / BLYB_METRICS_PORT(默认8010)
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "packages"))
sys.path.insert(0, os.path.join(_HERE, "plugins"))

# 必须在任何插件装配 import 之前设定
os.environ["BLYB_PLUGINS"] = "metrics"

import _bootstrap  # noqa: F401,E402

import httpx  # noqa: E402
import uvicorn  # noqa: E402

import app_runtime  # noqa: E402

assert "domains.pathology" not in sys.modules, "pathology leaked before assembly"

from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from apps.seed import seed_metrics_only  # noqa: E402

log = logging.getLogger("metrics-only")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.path.join(_HERE, "var", "metrics-only"))
    args = parser.parse_args()
    os.makedirs(args.root, exist_ok=True)
    logging.basicConfig(level=logging.INFO)

    # 强制隔离：显式 root（或 BLYB_METRICS_DB）优先于继承的 BLYB_DATABASE_URL，
    # 防止误连宿主环境（如全量演示库——那含病理表，破坏metrics-only语义）。
    os.environ["BLYB_DATABASE_URL"] = os.environ.get(
        "BLYB_METRICS_DB", f"sqlite:///{os.path.join(args.root, 'core.sqlite3')}")
    os.environ["BLYB_EXCHANGE_DB"] = os.environ.get(
        "BLYB_METRICS_EXCHANGE_DB",
        os.path.join(args.root, "exchange.sqlite3"))

    registry = app_runtime.load_registry()  # 仅 metrics
    assert registry.enabled == ["metrics"], registry.enabled
    assert not any(m in sys.modules for m in
                   ("domains.pathology", "domains.pathology.handlers",
                    "domains.pathology.plugin")), "pathology module leaked"

    engine = make_engine()
    run_migrations(engine, extra_migrations=registry.migrations)  # 核心+002病理不执行
    sf = make_session_factory(engine)
    seed_metrics_only(engine, sf, with_volume=True)

    # Mock 目的地（真实uvicorn）
    from destinations.mock_http.server import create_app as create_mock
    mock_port = free_port()
    mock_server = uvicorn.Server(uvicorn.Config(
        create_mock(), host="127.0.0.1", port=mock_port, log_level="error"))
    mock_server.config.app.state.storage_worker_disabled = True
    threading.Thread(target=mock_server.run, daemon=True).start()
    os.environ["BLYB_MOCK_PLATFORM_URL"] = f"http://127.0.0.1:{mock_port}"
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{mock_port}/health", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)

    # worker（metrics注册表；BLYB_PLUGINS=metrics → PluginRegistryAdapter）
    from apps.worker.engine import WorkerEngine
    we = WorkerEngine(sf)

    def worker_loop():
        while True:
            try:
                we.tick()
            except Exception:  # noqa: BLE001
                log.exception("tick failed")
            time.sleep(0.2)

    threading.Thread(target=worker_loop, daemon=True, name="metrics-worker").start()

    # core API（registry注入：病理路由不注册）
    from apps.core_api.app import create_app as create_core
    port = int(os.environ.get("BLYB_METRICS_PORT", "8010"))
    app = create_core(session_factory=sf, engine=engine, registry=registry)
    pathology_loaded = any(m.startswith("domains.pathology") for m in sys.modules)
    print(f"METRICS_ONLY_PORT={port}", flush=True)
    print(f"MOCK_PORT={mock_port}", flush=True)
    print(f"DB_URL={os.environ['BLYB_DATABASE_URL']}", flush=True)
    print(f"EXCHANGE_DB={os.environ['BLYB_EXCHANGE_DB']}", flush=True)
    print(f"PATHOLOGY_LOADED={int(pathology_loaded)}", flush=True)
    print(f"PLUGINS={','.join(registry.enabled)}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")


if __name__ == "__main__":
    main()
