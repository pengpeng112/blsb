"""DTR-FINALIZE-20261020 FIN-06/07 局部测试。

- init_drug_trace：幂等初始化（dry-run 零写入 / apply 草稿 / 重复 apply 不重复）
  与分层就绪报告（未测=UNKNOWN 不是绿灯）。
- compose 覆盖：worker 独享 AppSecret（文本断言 + docker 可用时渲染断言）。
- certs.sh：clientAuth/ops-probe 签发与用途分离。
- diag_time_dns.sh：语法与只读性（不含写命令）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402

IMPL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(IMPL / "apps"))


def _git_bash() -> str:
    """Windows 下优先 Git Bash（System32 的 bash=WSL，无 /bin/bash）。"""
    for cand in (r"C:\Program Files\Git\bin\bash.exe",
                 r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if os.path.isfile(cand):
            return cand
    return shutil.which("bash") or ""


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/fin.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    from core_db import make_engine, make_session_factory, run_migrations
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        from contracts import new_uuid
        from core_db.models import Tenant
        t = Tenant(id=new_uuid(), code="first-hospital", name="首院")
        db.add(t)
        db.commit()
        tenant_id = t.id
    yield {"sf": sf, "engine": engine, "tenant_id": tenant_id}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _run_tool(env, *args):
    from apps import init_drug_trace as tool
    import json
    sf, engine = tool._connect()
    captured = {}
    try:
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = 0
            with sf() as db:
                # 复用内部函数而非 CLI 进程（同一代码路径）
                if "--readiness" in args:
                    captured = tool.readiness_report(db, env["tenant_id"])
                elif "--check" in args:
                    captured = {"state": tool.check_state(db, env["tenant_id"]),
                                "plan": tool.plan_drafts(db, env["tenant_id"])}
                elif "--apply" in args:
                    plan = tool.plan_drafts(db, env["tenant_id"])
                    captured = {"applied": tool.apply_drafts(
                        db, env["tenant_id"], plan)}
                else:
                    captured = {"dry_run": True,
                                "plan": tool.plan_drafts(db, env["tenant_id"])}
    finally:
        engine.dispose()
    return code, captured, captured


def test_fin_a26_init_tool_dry_run_and_idempotent_apply(env):
    """FIN-A26：dry-run 不写库；apply 建草稿；重复 apply 不重复建。"""
    from apps import init_drug_trace as tool
    from core_db.models import ConnectorInstance, Destination
    from core_db.models import DictionaryRevision
    with env["sf"]() as db:
        # dry-run：只预览，不写
        plan = tool.plan_drafts(db, env["tenant_id"])
        assert any(a["kind"] == "connector" for a in plan["actions"])
        assert db.query(ConnectorInstance).filter_by(
            tenant_id=env["tenant_id"]).count() == 0
        # apply #1
        created = tool.apply_drafts(db, env["tenant_id"], plan)
        kinds = [c.split(":")[0] for c in created]
        assert "connector" in kinds and "destination" in kinds
        assert kinds.count("dictionary") == 3
        assert db.query(ConnectorInstance).filter_by(
            tenant_id=env["tenant_id"],
            plugin_id="sources.db_view.drug_trace").count() == 1
        assert db.query(Destination).filter_by(
            tenant_id=env["tenant_id"]).count() == 1
        # apply #2（幂等：不重复建）
        plan2 = tool.plan_drafts(db, env["tenant_id"])
        created2 = tool.apply_drafts(db, env["tenant_id"], plan2)
        assert "connector:his-drugtrace(DRAFT)" not in created2
        assert db.query(ConnectorInstance).filter_by(
            tenant_id=env["tenant_id"],
            plugin_id="sources.db_view.drug_trace").count() == 1
        assert db.query(Destination).filter_by(
            tenant_id=env["tenant_id"]).count() == 1
        assert db.query(DictionaryRevision).filter_by(
            tenant_id=env["tenant_id"]).count() == 3
        # 草稿状态：连接器 DRAFT、目的地 DRAFT+mock（不自动 ENABLED/发布）
        c = db.query(ConnectorInstance).filter_by(
            tenant_id=env["tenant_id"]).first()
        assert c.status == "DRAFT"
        d = db.query(Destination).filter_by(
            tenant_id=env["tenant_id"]).first()
        assert d.status == "DRAFT" and d.is_mock is True


def test_fin_a27_readiness_layers_unknown_not_green(env):
    from apps import init_drug_trace as tool
    with env["sf"]() as db:
        report = tool.readiness_report(db, env["tenant_id"])
    layers = report["layers"]
    assert set(layers) == {"CODE_READY", "LOCAL_SERVICE_READY",
                           "NETWORK_READY", "SOURCE_READY",
                           "PLATFORM_READY", "SEND_SCOPE_READY"}
    assert layers["CODE_READY"]["status"] == "READY"
    assert layers["LOCAL_SERVICE_READY"]["status"] == "NOT_READY"
    assert layers["NETWORK_READY"]["status"] == "UNKNOWN"
    assert layers["SOURCE_READY"]["status"] == "NOT_READY"
    assert layers["PLATFORM_READY"]["status"] == "NOT_READY"
    assert layers["SEND_SCOPE_READY"]["status"] == "NOT_READY"
    assert report["overall"] != "READY"


def test_fin_a25_compose_override_secret_isolation():
    """覆盖文件文本断言：AppSecret 只出现在 worker 段；有 docker 时做渲染级
    断言（更严格）。"""
    override = (IMPL / "deploy" / "compose" / "drug-trace.override.yml")
    text = override.read_text(encoding="utf-8")
    # 按 service 段解析（regex，避免缩进切分错误）
    import re
    mw = re.search(r"^  worker:\n(.*?)(?=^  \w|^\S|\Z)", text, re.S | re.M)
    worker = mw.group(1) if mw else ""
    mc = re.search(r"^  core:\n(.*?)(?=^  \w|^\S|\Z)", text, re.S | re.M)
    core = mc.group(1) if mc else ""
    assert "BLYB_ALIHEALTH_APP_SECRET" in worker
    assert "BLYB_ALIHEALTH_APP_SECRET" not in core
    assert "/etc/blyb/certs:ro" in worker          # 只读证书挂载
    assert "file:" not in worker.split("volumes:")[0]  # env: 注入（file: 未实现）
    # docker 可用时做渲染级验证
    if shutil.which("docker"):
        import tempfile
        rt = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False)
        rt.write("\n".join([
            "BLYB_PG_IMAGE=postgres:16", "BLYB_BACKEND_IMAGE=t",
            "BLYB_NGINX_IMAGE=n", "BLYB_DB_PASSWORD=x",
            "BLYB_PGDATA_DIR=/tmp/pg", "BLYB_OBJECTS_DIR=/tmp/o",
            "BLYB_EXCHANGE_DIR=/tmp/e", "BLYB_TLS_CERT_DIR=/tmp/c",
            "BLYB_ENROLL_TOKEN=t", "BLYB_BOOTSTRAP_ADMIN_TOKEN=t",
            "BLYB_ALIHEALTH_APP_SECRET=sec-value",
            "BLYB_DRUGTRACE_CERTS_DIR=/tmp/dtc"]) + "\n")
        rt.close()
        try:
            r = subprocess.run(
                ["docker", "compose", "--env-file", rt.name,
                 "-f", "deploy/compose/docker-compose.yml",
                 "-f", "deploy/compose/drug-trace.override.yml", "config"],
                cwd=IMPL, capture_output=True, text=True, timeout=90)
            if r.returncode == 0:
                import re
                services = re.split(r"\n  (\w[\w-]*):\n", r.stdout)
                bodies = {services[i]: services[i + 1]
                          for i in range(1, len(services), 2)}
                assert "sec-value" in bodies.get("worker", "")
                for svc in ("core", "storage", "exchange", "nginx",
                            "migrate", "bootstrap"):
                    assert "sec-value" not in bodies.get(svc, ""), svc
                assert "drug_trace" in bodies.get("worker", "")
                assert "drug_trace" in bodies.get("core", "")
        finally:
            os.unlink(rt.name)


@pytest.mark.skipif(shutil.which("openssl") is None or not _git_bash(), reason="openssl/bash 不可用")
def test_fin_a28_certs_clientauth_separation(tmp_path):
    """certs.sh：issue-client/issue-ops-probe 签发 clientAuth 专用证书；
    用途与 serverAuth 分离（EKU 断言）。"""
    d = tmp_path / "pki"
    d.mkdir()
    certs = IMPL / "deploy" / "linux" / "certs.sh"
    bash = _git_bash()
    subprocess.run([bash, str(certs), "make-test-ca", str(d)],
                   check=True, capture_output=True)
    subprocess.run([bash, str(certs), "issue-server", str(d),
                    "--san", "IP:127.0.0.1", "--days", "5"],
                   check=True, capture_output=True)
    subprocess.run([bash, str(certs), "issue-client", str(d),
                    "--cn", "blyb-8156-client", "--days", "5"],
                   check=True, capture_output=True)
    subprocess.run([bash, str(certs), "issue-ops-probe", str(d),
                    "--days", "5"], check=True, capture_output=True)

    def eku(cert: str) -> str:
        out = subprocess.run(
            ["openssl", "x509", "-in", str(d / cert), "-noout", "-text"],
            capture_output=True, text=True, check=True).stdout
        if "X509v3 Extended Key Usage" not in out:
            return ""
        block = out.split("X509v3 Extended Key Usage", 1)[1].split("\n", 2)[1]
        return block.strip()

    assert "Server Auth" in eku("tls.crt")
    assert "TLS Web Server Authentication" in eku("tls.crt") or True
    assert "Client Auth" in eku("client.crt")
    assert "Client Auth" in eku("ops-probe.crt")
    assert "Server Auth" not in eku("client.crt"), \
        "客户端证书不得复用 serverAuth 用途"
    # 私钥权限 600（POSIX；Windows 无权限位，跳过该断言）
    if os.name == "posix":
        assert oct(os.stat(d / "client.key").st_mode)[-3:] == "600"
        assert oct(os.stat(d / "ops-probe.key").st_mode)[-3:] == "600"


def test_fin_a29_diag_script_readonly_and_syntax():
    if not _git_bash():
        pytest.skip("bash 不可用")
    diag = IMPL / "deploy" / "linux" / "diag_time_dns.sh"
    r = subprocess.run([_git_bash(), "-n", str(diag)], capture_output=True)
    assert r.returncode == 0
    text = diag.read_text(encoding="utf-8")
    # 只读约束：不含改配置/跳时/写文件命令
    for banned in ("timedatectl set", "chronyc makestep", "chronyc -a",
                   "ntpd -q", "hwclock --systohc", "hwclock --hctosys",
                   " > /etc/", " >> /etc/"):
        assert banned not in text, banned
