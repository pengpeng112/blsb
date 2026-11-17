"""RT-06 F06：前端路由 AST 交叉核对（实例化应用路由 dump + node ts AST 审计）。

- 本 fixture 实例化完整默认插件集应用（domain.routes 真实装入——medins-imaging
  datasets 等域路由在册），dump (service, method, path) 到 var/rt06_routes.json；
- 运行 node AST 审计脚本（TypeScript compiler），要求 已识别调用缺失 0、未解析 0；
- 失败输出每调用点文件/行（var/rt06_route_audit.json），不静默跳过。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "apps", "admin_web")
AUDIT = os.path.join(WEB, "tests", "rt06_route_audit.mjs")


@pytest.fixture(scope="module")
def routes_dump(tmp_path_factory):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path_factory.mktemp('rt06ra')}/ra.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry()               # 默认全插件集
    from core_db import make_engine, run_migrations
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    from core_db import make_session_factory
    sf = make_session_factory(engine)
    client = TestClient(create_app(session_factory=sf, engine=engine,
                                   registry=reg))
    from apps.storage_service.app import create_app as create_storage
    storage_client = TestClient(create_storage(
        session_factory=sf, engine=engine))
    rows = []
    for app_obj, service in ((client.app, "core"),
                             (storage_client.app, "storage")):
        for r in app_obj.routes:
            methods = sorted(m for m in (getattr(r, "methods", None) or [])
                             if m in ("GET", "POST", "PUT", "DELETE"))
            path = getattr(r, "path", "")
            if not methods or not path:
                continue                  # Mount/静态等无方法路由跳过
            for m in methods:
                rows.append({"service": service, "method": m, "path": path})
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)
    out = tmp_path_factory.mktemp("ra") / "rt06_routes.json"
    out.write_text(json.dumps(rows), encoding="utf-8")
    return str(out)


def test_f06_route_audit_zero_missing_zero_unresolved(routes_dump):
    r = subprocess.run(
        [sys.executable, "-c",
         f"import subprocess,sys; sys.exit(subprocess.run(['node', {AUDIT!r},"
         f" {routes_dump!r}], capture_output=False).returncode)"],
        capture_output=True, text=True, cwd=WEB)
    # node 直接跑（不经 python 包装）
    r = subprocess.run(["node", AUDIT, routes_dump],
                       capture_output=True, text=True, cwd=WEB)
    assert r.returncode == 0, (r.stdout + r.stderr)[-4000:]
    assert "unresolved=0" in r.stdout
    # 域路由真实装入证据：medins-imaging datasets 在 dump 中
    routes = json.load(open(routes_dump, encoding="utf-8"))
    assert any("/medins-imaging/datasets" in x["path"] and x["method"] == "GET"
               for x in routes)
