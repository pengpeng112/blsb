"""RT-06 F05：启动本地 FastAPI（随机端口）→ 注入 base/凭据给前端 API 客户端测试。

真实密码会话登录链需要 console account（seed 的 dev token 走 Bearer 简登）；
本 fixture 直接用 apps.manage_console_user 的内部函数建一次性账号。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="session")
def api_client_env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rt06client")
    db = tmp / "client.sqlite3"
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{db}"
    import plugin_registry
    from core_db import make_engine, make_session_factory, run_migrations
    from apps.seed import seed
    engine = make_engine()
    run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
    sf = make_session_factory(engine)
    seed(engine, sf, with_volume=False)
    with sf() as db:
        from core_db.models import Tenant
        tid = db.query(Tenant).first().id
        # apps/seed 不含业务目录种子——API 客户端用例需要 medins_emr_sd
        from plugins.report_targets import seed_default_targets
        seed_default_targets(db, tid)
        db.commit()
        # 一次性 console 账号（真实密码会话登录链）
        from contracts.passwords import hash_password
        from core_db.models import AuthSubject, ConsoleAccount
        subj = AuthSubject(
            id="rt06-subj", tenant_id=tid, kind="user",
            subject_code="rt06admin", display_name="RT06 E2E",
            token_hash=hash_password("not-a-real-token-xx"), roles=["platform_admin"],
            target_scopes=["*"], status="ACTIVE")
        db.add(subj)
        db.flush()
        db.add(ConsoleAccount(
            id="rt06-console", tenant_id=tid, subject_id=subj.id,
            username="rt06admin",
            password_hash=hash_password("rt06-E2E-Password1")))
        db.commit()
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app as create_core
    app = create_core(session_factory=sf, engine=engine)
    port = _free_port()
    import uvicorn
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    injection = {"base": f"http://127.0.0.1:{port}",
                 "username": "rt06admin", "password": "rt06-E2E-Password1",
                 "token": ""}
    var_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "..", "var"))
    os.makedirs(var_dir, exist_ok=True)
    out = os.path.join(var_dir, "rt06_api_client.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(injection, fh)
    yield injection
    server.should_exit = True
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def test_f05_frontend_api_client_integration(api_client_env):
    """node --test 跑 rt06_api_client.test.mjs（真实 fetch 到本地 FastAPI）。"""
    web = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "..", "..", "apps", "admin_web"))
    r = subprocess.run(["node", "--test",
                        os.path.join(web, "tests", "rt06_api_client.test.mjs")],
                       capture_output=True, text=True, cwd=web,
                       timeout=240)
    assert r.returncode == 0, (r.stdout + r.stderr)[-4000:]
