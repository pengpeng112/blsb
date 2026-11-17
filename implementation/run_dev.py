"""一键本地开发环境：迁移+种子+启动全部服务。

用法（阶段A退出条件"一条文档命令启动"）：
    python run_dev.py            # 前台启动 core/storage/exchange/mock + worker
    python run_dev.py --demo     # 额外生成3片+1重扫演示数据并跑Agent一轮
默认端口：core 8000 / storage 8001 / exchange 8002 / mock平台 8090。
"""
from __future__ import annotations

import argparse
import logging
import os
import threading
import time

import _bootstrap  # noqa: F401
import uvicorn

from core_db import make_engine, make_session_factory, run_migrations
import plugin_registry
from apps.seed import seed

HERE = os.path.dirname(os.path.abspath(__file__))
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("run_dev")


def start_all(*, demo: bool = False, scanner_mb: int = 2):
    os.environ.setdefault("BLYB_EXCHANGE_DB",
                          os.path.join(HERE, "var", "exchange.sqlite3"))
    engine = make_engine()
    results = run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
    log.info("migrations: %s", [(r[1], r[2]) for r in results])
    sf = make_session_factory(engine)
    seed(engine, sf)
    log.info("seed ok")

    from apps.core_api.app import create_app as create_core
    from apps.storage_service.app import create_app as create_storage
    from apps.exchange_gateway.app import create_app as create_exchange
    from destinations.mock_http.server import create_app as create_mock

    servers = []
    # 单执行器（R43/ADR-DEV-02）：storage 关闭内嵌 worker，统一由下方 worker
    # 引擎驱动；否则同一DB会出现两个执行器竞态。
    # T14：绑定地址可经 BLYB_BIND_HOST 覆盖（默认仅本机回环；内网远端访问
    # 需显式 0.0.0.0，见 deploy/intranet/README.md）。
    bind_host = os.environ.get("BLYB_BIND_HOST", "127.0.0.1")
    for app_factory, port in (
            (lambda: create_storage(embedded_worker=False), 8001),
            (create_core, 8000), (create_exchange, 8002), (create_mock, 8090)):
        app = app_factory()
        config = uvicorn.Config(app, host=bind_host, port=port,
                                log_level="warning")
        server = uvicorn.Server(config)
        t = threading.Thread(target=server.run, daemon=True,
                             name=f"srv-{port}")
        t.start()
        servers.append((server, t, port))
    time.sleep(1.2)

    from apps.worker.engine import WorkerEngine
    we = WorkerEngine(sf)

    def worker_loop():
        while True:
            try:
                we.tick()
            except Exception:  # noqa: BLE001
                log.exception("worker tick failed")
            time.sleep(0.2)

    threading.Thread(target=worker_loop, daemon=True, name="worker").start()

    if demo:
        from apps.edge_agent.scanner_simulator import demo_scenario
        watch = os.path.join(HERE, "var", "scanner-output")
        os.makedirs(watch, exist_ok=True)
        manifests = demo_scenario(watch, size_bytes=scanner_mb * 1024 * 1024)
        log.info("simulator wrote %d scans", len(manifests))
        from apps.edge_agent.service import AgentService
        agent = AgentService(
            watch_root=watch, core_url="http://127.0.0.1:8000",
            storage_url="http://127.0.0.1:8001", token="dev-agent-token",
            agent_id=seed_agent_id(sf), local_db_path=os.path.join(
                HERE, "var", "agent-queue.sqlite3"),
            chunk_size=4 * 1024 * 1024)
        def agent_demo():
            for _ in range(60):
                agent.run_once()
                time.sleep(1)
        threading.Thread(target=agent_demo, daemon=True, name="agent").start()

    shown_host = "127.0.0.1" if bind_host in ("0.0.0.0", "::") else bind_host
    print("=" * 64)
    print("blyb environment up:")
    print(f"  core-api      http://{shown_host}:8000  (docs /docs, ui /ui/)")
    print(f"  storage       http://{shown_host}:8001  (docs /docs)")
    print(f"  exchange      http://{shown_host}:8002  (docs /docs)")
    print(f"  mock platform http://{shown_host}:8090  (MOCK，非真实医保)")
    print("  worker        in-process thread")
    if os.environ.get("BLYB_DISABLE_DEV_TOKENS") == "1":
        print("tokens: dev-* disabled; login with BLYB_BOOTSTRAP_ADMIN_TOKEN "
              "(value is not printed)")
    else:
        print("tokens: admin dev-admin-token / agent dev-agent-token /"
              " collector dev-collector-token")
        print("dev tokens are enabled; do not expose this mode on a hospital network")
    volume_root = os.environ.get("BLYB_VOLUME_ROOT", "").strip()
    if volume_root:
        print(f"volume root: {volume_root}")
    print("=" * 64)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("shutting down")


def seed_agent_id(sf):
    with sf() as db:
        from core_db.models import Agent
        a = db.query(Agent).filter_by(device_code="SCAN-WS-01").first()
        return a.id if a else None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--scanner-mb", type=int, default=2)
    args = ap.parse_args()
    start_all(demo=args.demo, scanner_mb=args.scanner_mb)
