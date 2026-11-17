"""W14部署修订回归（execution-plan §5；REPORTING-LINUX-DEPLOY-20261016）。

覆盖：
- bootstrap 幂等/fail-closed（B-BOOT-01/02/03）
- enroll 默认密钥失效语义（B-ENROLL-01/02/03）
- worker 心跳文件（B-HB-01）
- compose/Dockerfile/nginx/release 静态约束（B-CFG-01..05，不依赖 Docker）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import _bootstrap  # noqa: F401,E402
from core_db import make_engine, make_session_factory, run_migrations

IMPL = Path(__file__).resolve().parents[2]


@pytest.fixture()
def fresh_env(tmp_path, monkeypatch):
    """独立 SQLite 库 + 独立对象根 + 随机高熵令牌。"""
    db = tmp_path / "core.sqlite3"
    monkeypatch.setenv("BLYB_DATABASE_URL", f"sqlite:///{db.as_posix()}")
    monkeypatch.setenv("BLYB_VOLUME_ROOT", str(tmp_path / "vol1"))
    monkeypatch.setenv("BLYB_BOOTSTRAP_ADMIN_TOKEN", "t" * 40)
    monkeypatch.setenv("BLYB_ENROLL_TOKEN", "e" * 40)
    for var in ("BLYB_DISABLE_DEV_TOKENS",):
        monkeypatch.delenv(var, raising=False)
    engine = make_engine()
    run_migrations(engine)
    yield monkeypatch
    engine.dispose()


# ---------------------------------------------------------------- bootstrap
def test_B_BOOT_01_missing_token_fails_closed(fresh_env, monkeypatch, capsys):
    monkeypatch.delenv("BLYB_BOOTSTRAP_ADMIN_TOKEN", raising=False)
    from apps import bootstrap
    assert bootstrap.main() == 2
    assert "BLYB_BOOTSTRAP_ADMIN_TOKEN" in capsys.readouterr().err


def test_B_BOOT_02_short_token_rejected(fresh_env, monkeypatch):
    monkeypatch.setenv("BLYB_BOOTSTRAP_ADMIN_TOKEN", "short")
    from apps import bootstrap
    assert bootstrap.main() == 2


def test_B_BOOT_03_idempotent_and_minimal(fresh_env, monkeypatch):
    # bootstrap.main() 会进程级写入 BLYB_DISABLE_DEV_TOKENS=1：先经 monkeypatch
    # 预设，保证测试结束时还原，不污染同进程后续依赖 dev 令牌的用例
    monkeypatch.setenv("BLYB_DISABLE_DEV_TOKENS", "1")
    from apps import bootstrap
    from core_db.models import (AuthSubject, ConfigRevision, Destination,
                                StorageVolume, Tenant, WorkflowRevision)
    assert bootstrap.main() == 0
    assert bootstrap.main() == 0  # 幂等：重复运行成功且不产生重复行
    sf = make_session_factory(make_engine())
    with sf() as db:
        assert db.query(Tenant).filter_by(code="first-hospital").count() == 1
        # 最小初始化：只有 bootstrap-admin 主体，无 dev-*、无演示工作流/目的地/Pack
        assert db.query(AuthSubject).filter_by(
            subject_code="bootstrap-admin", status="ACTIVE").count() == 1
        assert db.query(AuthSubject).filter(
            AuthSubject.subject_code.like("dev-%")).count() == 0
        assert db.query(WorkflowRevision).count() == 0
        # LT-05：bootstrap 种子 inf_frontend_mock（影子 Mock 目的地，
        # is_mock+loopback；幂等——二次 bootstrap 不新增）
        assert db.query(Destination).count() == 1
        # LT-05：随 inf_frontend_mock 种子的目的地配置（PUBLISHED×1，幂等）
        assert db.query(ConfigRevision).count() == 1
        vol = db.query(StorageVolume).filter_by(name="vol1").one()
        assert vol.root_ref == os.environ["BLYB_VOLUME_ROOT"]
        # 卷哨兵实际落盘
        assert (Path(vol.root_ref) / f".blyb-vol-{vol.id}").is_file()


# ---------------------------------------------------------------- enroll
def _enroll(client, body):
    return client.post("/api/v1/agents/enroll", json=body)


def test_B_ENROLL_01_dev_default_still_works_in_dev_mode(fresh_env, monkeypatch):
    """未禁用 dev 令牌且未显式配置注册密钥时，默认密钥行为不变（本地开发兼容）。"""
    monkeypatch.delenv("BLYB_ENROLL_TOKEN", raising=False)
    from fastapi.testclient import TestClient
    from apps.seed import seed
    seed(with_volume=False)  # dev 模式种子（scanner-simulator 源存在）
    import apps.core_api.app as m
    m.app = None
    with TestClient(m.create_app()) as c:
        r = _enroll(c, {"enroll_token": "dev-enroll-token",
                        "device_code": "W-DEV-1"})
        assert r.status_code == 200, r.text


def test_B_ENROLL_02_fail_closed_when_dev_disabled(fresh_env, monkeypatch):
    fresh_env.setenv("BLYB_DISABLE_DEV_TOKENS", "1")
    monkeypatch.delenv("BLYB_ENROLL_TOKEN", raising=False)
    from fastapi.testclient import TestClient
    from apps.seed import seed
    seed(with_volume=False)
    import apps.core_api.app as m
    m.app = None
    with TestClient(m.create_app()) as c:
        assert _enroll(c, {"enroll_token": "", "device_code": "X"}).status_code == 401
        assert _enroll(c, {"enroll_token": "dev-enroll-token",
                           "device_code": "X"}).status_code == 401


def test_B_ENROLL_03_explicit_token_still_valid(fresh_env):
    from fastapi.testclient import TestClient
    from apps.seed import seed
    seed(with_volume=False)
    import apps.core_api.app as m
    m.app = None
    token = os.environ["BLYB_ENROLL_TOKEN"]
    with TestClient(m.create_app()) as c:
        r = _enroll(c, {"enroll_token": token, "device_code": "W-PROD-1"})
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------- worker heartbeat
def test_B_HB_01_heartbeat_written(fresh_env, tmp_path, monkeypatch):
    import time
    from apps.worker import engine
    hb = tmp_path / "hb.json"
    monkeypatch.setenv("BLYB_WORKER_HEARTBEAT", str(hb))
    written = []

    class FakeEngine:
        def tick(self):
            written.append(1)
            if len(written) >= 3:
                raise _StopLoop()

    class _StopLoop(BaseException):
        """BaseException：穿透 engine.main 的 `except Exception` 通用兜底。"""

    orig = engine.WorkerEngine
    engine.WorkerEngine = lambda sf: FakeEngine()
    try:
        with pytest.raises(_StopLoop):
            engine.main(interval=0.05)
    finally:
        engine.WorkerEngine = orig
    import json
    data = json.loads(hb.read_text(encoding="utf-8"))
    assert data["pid"] == os.getpid()
    assert abs(data["ts"] - time.time()) < 30


# ---------------------------------------------------------------- 静态配置约束
COMPOSE = (IMPL / "deploy/compose/docker-compose.yml").read_text(encoding="utf-8")
DOCKERFILE = (IMPL / "Dockerfile").read_text(encoding="utf-8")


def test_B_CFG_01_no_host_port_mapping_for_backends():
    """DEP-A12：core/storage/exchange 不发布宿主机端口。"""
    import re
    ports = re.findall(r'ports:\s*\n(?:\s*#[^\n]*\n)*\s*-\s*([^\n]+)', COMPOSE)
    assert ports, "compose 应至少有 nginx ports"
    assert all("443" in p for p in ports), ports
    for svc in ("core", "storage", "exchange", "worker", "db", "migrate", "bootstrap"):
        block = COMPOSE.split(f"  {svc}:")[1].split("\n  ")[0] if f"  {svc}:" in COMPOSE else ""
        assert "ports:" not in block, f"{svc} 不得有宿主机端口"


def test_B_CFG_02_dockerfile_no_dead_factory_env():
    assert "BLYB_FACTORY=" not in DOCKERFILE  # D4：ENV 赋值不得再出现
    assert "--factory" in DOCKERFILE
    assert "PYTHONPATH=/app:/app/packages:/app/plugins" in DOCKERFILE
    assert "10001" in DOCKERFILE  # 固定 UID/GID


def test_B_CFG_03_dockerignore_covers_runtime_db():
    di = (IMPL / ".dockerignore").read_text(encoding="utf-8")
    for pat in ("**/var/", "*.sqlite3", ".env", "tests/", "scanner-output/"):
        assert pat in di, pat
    assert (IMPL / ".dockerignore").is_file()


def test_B_CFG_04_compose_gates_and_single_worker():
    for frag in ("service_completed_successfully", "BLYB_WORKER_HEARTBEAT",
                 "unless-stopped", "max-size"):
        assert frag in COMPOSE, frag


def test_B_CFG_05_nginx_same_origin_routes():
    conf = (IMPL / "deploy/compose/nginx/nginx.conf").read_text(encoding="utf-8")
    assert "location /storage/" in conf and "proxy_pass http://storage:8001/;" in conf
    assert "location /exchange/" in conf
    assert "proxy_pass http://core:8000;" in conf
    assert "ssl_protocols TLSv1.2 TLSv1.3" in conf
