"""RT-06 F01：脚手架连续扩展/失败原子性/诚实模板（临时工作区，不触共享库）。

验收（任务书 §2）：A/B 连续创建→各自注册/种子可读取→重放零字节变化；
同 code 不同 plugin 零写；junction/symlink 越界拒绝；dry-run 零写零 mtime；
插入锚点丢失/源语法损坏拒绝；骨架 domain.routes 真实可导入。
"""
from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "..", "scripts", "new_report_target.py")

FAKE_SEED = "def _seed_rows():\n    return [\n    ]\n"


def _make_ws(tmp_path, name="ws") -> str:
    ws = str(tmp_path / name)
    os.makedirs(os.path.join(ws, "implementation", "plugins"), exist_ok=True)
    open(os.path.join(ws, "implementation", "plugins", "report_targets.py"),
         "w", encoding="utf-8", newline="\n").write(FAKE_SEED)
    return ws


def _run(ws, code, plugin, biz, *extra):
    return subprocess.run(
        [sys.executable, SCRIPT, "--code", code, "--plugin-key", plugin,
         "--name", f"业务{code}", "--category", "测试",
         "--authority", "测试机构", "--mode", "HYBRID",
         "--biz-dir", biz, "--workspace", ws, *extra],
        capture_output=True, text=True)


def _seed_codes(ws) -> dict:
    """AST 读取种子文件 → {code: plugin_key}（不依赖字符串包含）。"""
    src = open(os.path.join(ws, "implementation", "plugins",
                            "report_targets.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_seed_rows":
            for n in ast.walk(node):
                if isinstance(n, ast.Return) and isinstance(n.value, ast.List):
                    for el in n.value.elts:
                        if isinstance(el, ast.Dict):
                            got = {}
                            for k, v in zip(el.keys, el.values):
                                if isinstance(k, ast.Constant) and \
                                        isinstance(v, ast.Constant):
                                    got[str(k.value)] = str(v.value)
                            if got.get("code"):
                                out[got["code"]] = got.get("plugin_key")
    return out


def _tree_bytes(root: str) -> dict:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            out[os.path.relpath(fp, root)] = open(fp, "rb").read()
    return out


def test_f01_continuous_ab_creation_and_byte_stable_replay(tmp_path):
    """连续创建 A/B → 各自注册/种子可读取 → 再运行 A/B 零字节变化。"""
    ws = _make_ws(tmp_path)
    r_a = _run(ws, "demo_target_a", "demo_target_a", "9、演示A")
    assert r_a.returncode == 0, r_a.stderr
    r_b = _run(ws, "demo_target_b", "demo_target_b", "9、演示B")
    assert r_b.returncode == 0, r_b.stderr
    codes = _seed_codes(ws)
    assert codes.get("demo_target_a") == "demo_target_a"
    assert codes.get("demo_target_b") == "demo_target_b"
    for rel in ("implementation/plugins/domains/demo_target_a/manifest.json",
                "implementation/plugins/domains/demo_target_b/manifest.json",
                "implementation/plugins/domains/demo_target_a/routes.py"):
        assert os.path.exists(os.path.join(ws, rel.replace("/", os.sep))), rel
    before = _tree_bytes(ws)
    # 同 code 同参数重放 → 零字节变化
    r_a2 = _run(ws, "demo_target_a", "demo_target_a", "9、演示A")
    assert r_a2.returncode == 0 and "新增 0" in r_a2.stdout
    r_b2 = _run(ws, "demo_target_b", "demo_target_b", "9、演示B")
    assert r_b2.returncode == 0 and "新增 0" in r_b2.stdout
    assert _tree_bytes(ws) == before


def test_f01_same_code_different_plugin_rejected_zero_write(tmp_path):
    ws = _make_ws(tmp_path)
    assert _run(ws, "demo_x", "demo_x", "9、演示X").returncode == 0
    before = _tree_bytes(ws)
    r = _run(ws, "demo_x", "demo_other", "9、演示X")
    assert r.returncode == 2 and "plugin_key 不一致" in r.stdout
    assert _tree_bytes(ws) == before                    # 零写


def test_f01_junction_and_symlink_escape_rejected(tmp_path):
    """父路径 symlink/junction 越界（implementation 指向 workspace 外）→ 拒绝。"""
    outside = tmp_path / "outside"
    os.makedirs(outside / "plugins")
    open(outside / "plugins" / "report_targets.py", "w",
         encoding="utf-8", newline="\n").write(FAKE_SEED)
    # ws/implementation 不预建——由 junction 指向 workspace 外的真实目录
    ws = str(tmp_path / "ws_j")
    os.makedirs(ws, exist_ok=True)
    impl = os.path.join(ws, "implementation")
    link = None
    # junction（Windows 原生；_winapi.CreateJunction(target, link_path)）
    import _winapi
    try:
        _winapi.CreateJunction(str(outside), impl)
        link = "junction"
    except OSError:
        try:
            os.symlink(str(outside), impl, target_is_directory=True)
            link = "symlink"
        except (OSError, NotImplementedError):
            pytest.skip("当前环境无法创建 junction/symlink（权限限制）——"
                        "越界解析路径已在非链接用例覆盖")
    assert link
    r = _run(ws, "demo_esc", "demo_esc", "9、演示越界")
    assert r.returncode == 2 and "越界" in r.stdout
    # 越界目标零写：outside 目录没有生成骨架
    assert not (outside / "plugins" / "domains").exists()


def test_f01_dry_run_zero_writes_zero_mtime(tmp_path):
    ws = _make_ws(tmp_path)
    seed_path = os.path.join(ws, "implementation", "plugins",
                             "report_targets.py")
    mtime0 = os.stat(seed_path).st_mtime
    r = _run(ws, "demo_dry", "demo_dry", "9、演示Dry", "--dry-run")
    assert r.returncode == 0 and "[dry-run]" in r.stdout
    assert os.stat(seed_path).st_mtime == mtime0        # mtime 未变
    # 无新文件/目录
    assert not os.path.exists(os.path.join(
        ws, "implementation", "plugins", "domains", "demo_dry"))
    assert _tree_bytes(ws) == {os.path.relpath(seed_path, ws):
                               FAKE_SEED.encode("utf-8")}


def test_f01_anchor_missing_and_broken_syntax_rejected(tmp_path):
    """锚点丢失（无 _seed_rows）/源语法损坏 → 拒绝，零写。"""
    ws = _make_ws(tmp_path, "ws_m")
    open(os.path.join(ws, "implementation", "plugins", "report_targets.py"),
         "w", encoding="utf-8").write("X = 1\n")       # 无 _seed_rows
    r1 = _run(ws, "demo_m", "demo_m", "9、演示M")
    assert r1.returncode == 2 and "锚点丢失" in r1.stdout
    assert not os.path.exists(os.path.join(
        ws, "implementation", "plugins", "domains", "demo_m"))
    ws2 = _make_ws(tmp_path, "ws_s")
    open(os.path.join(ws2, "implementation", "plugins", "report_targets.py"),
         "w", encoding="utf-8").write("def _seed_rows(:\n    return [\n")
    r2 = _run(ws2, "demo_s", "demo_s", "9、演示S")
    assert r2.returncode == 2 and "语法损坏" in r2.stdout
    assert not os.path.exists(os.path.join(
        ws2, "implementation", "plugins", "domains", "demo_s"))


def test_f01_skeleton_domain_routes_importable(tmp_path):
    """骨架 routes.py 真实可导入：register_routes 挂上 FastAPI 后可响应。"""
    ws = _make_ws(tmp_path)
    assert _run(ws, "demo_route", "demo_route", "9、演示路由").returncode == 0
    routes_path = os.path.join(
        ws, "implementation", "plugins", "domains", "demo_route", "routes.py")
    spec = importlib.util.spec_from_file_location("rt06_demo_routes",
                                                  routes_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                        # 可导入
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    class _P:
        request_id = "test-rid"

        def require_principal(self):
            return self

    class _Auth:
        def __call__(self):
            return _P()

    app = FastAPI()
    mod.register_routes(app, None, _Auth(), None)
    client = TestClient(app)
    resp = client.get("/api/v1/demo_route/datasets")
    assert resp.status_code == 200
    d = resp.json()
    assert d["data"]["items"] == [] and d["data"]["data_source"] == "none"
