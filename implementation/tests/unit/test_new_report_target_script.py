"""LT-09/RT-06 F01 脚手架测试：dry-run 零写/实跑齐全/幂等/越界拒绝/种子复用。

RT-06 F01 新契约：种子按 AST 精确 code 定位（_seed_rows 返回列表 dict 字面量）、
插入行带专属标记 `# scaffold:<code>`、Python 字面量（单引号 repr）、readme_path
使用实际 --biz-dir、插入后源码必须仍是合法 Python。
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "..", "scripts", "new_report_target.py")

ARGS = ["--code", "skel_test_jn", "--plugin-key", "skel_test",
        "--name", "脚手架测试业务", "--category", "测试",
        "--authority", "测试机构", "--mode", "HYBRID",
        "--biz-dir", "9x、脚手架测试"]

FAKE_SEED = "def _seed_rows():\n    return [\n    ]\n"


def _run(ws, *extra):
    return subprocess.run(
        [sys.executable, SCRIPT, *ARGS, "--workspace", ws, *extra],
        capture_output=True, text=True)


def test_dry_run_zero_writes_and_real_run_idempotent(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws, exist_ok=True)
    os.makedirs(os.path.join(ws, "implementation", "plugins"), exist_ok=True)
    open(os.path.join(ws, "implementation", "plugins", "report_targets.py"),
         "w", encoding="utf-8").write(FAKE_SEED)
    # dry-run：零写入
    r1 = _run(ws, "--dry-run")
    assert r1.returncode == 0, r1.stderr
    assert "[dry-run] 将创建" in r1.stdout
    assert not os.path.exists(os.path.join(
        ws, "implementation", "plugins", "domains", "skel_test"))
    # 实跑：骨架文件 + 种子追加
    r2 = _run(ws)
    assert r2.returncode == 0, r2.stderr
    assert "created" in r2.stdout
    for rel in ("implementation/plugins/domains/skel_test/manifest.json",
                "implementation/plugins/domains/skel_test/routes.py",
                "implementation/profiles/skel_test_jn/profile.json",
                "implementation/tests/unit/test_skel_test_skeleton.py",
                "数据上报/9x、脚手架测试/README.md"):
        assert os.path.exists(os.path.join(ws, rel.replace("/", os.sep))), rel
    rt = open(os.path.join(ws, "implementation", "plugins",
                           "report_targets.py"), encoding="utf-8").read()
    # F01 契约：Python 字面量（repr 单引号）+ 专属标记 + 语法完好 + readme_path
    assert "'code': 'skel_test_jn'" in rt and "scaffold:skel_test_jn" in rt
    ast.parse(rt)
    assert "数据上报/9x、脚手架测试/README.md" in rt
    assert "数据上报/skel_test_jn/README.md" not in rt      # 不再指向 code 目录
    # 幂等复跑：新增 0
    r3 = _run(ws)
    assert r3.returncode == 0 and "新增 0" in r3.stdout
    assert open(os.path.join(ws, "implementation", "plugins",
                             "report_targets.py"),
                encoding="utf-8").read() == rt              # 重放零字节变化


def test_rejects_unsafe_paths(tmp_path):
    ws = str(tmp_path / "ws2")
    os.makedirs(ws, exist_ok=True)
    bad = ["--code", "ok_code", "--plugin-key", "ok_p", "--name", "n",
           "--category", "c", "--authority", "a", "--mode", "PUSH",
           "--workspace", ws]
    for biz in ("../escape", "C:\\abs\\path", "a/b", "a\\b", ".", "..",
                " ok", "ok ", "尾点.", "CON", "a<b", "a:b"):
        r = subprocess.run([sys.executable, SCRIPT, *bad,
                            "--biz-dir", biz], capture_output=True, text=True)
        assert r.returncode == 2, (biz, r.stdout, r.stderr)
    r3 = subprocess.run([sys.executable, SCRIPT, "--code", "Bad-Code",
                         "--plugin-key", "ok_p", "--name", "n",
                         "--category", "c", "--authority", "a", "--mode",
                         "PUSH", "--biz-dir", "ok", "--workspace", ws],
                        capture_output=True, text=True)
    assert r3.returncode == 2


def test_real_seed_medins_imaging_reused_not_duplicated():
    """真实种子 medins_imaging_jn（5条之一）：复用验证、不新建第6业务。"""
    ws = os.path.abspath(os.path.join(SCRIPT, "..", ".."))
    r = subprocess.run(
        [sys.executable, SCRIPT, "--code", "medins_imaging_jn",
         "--plugin-key", "medins_imaging", "--name", "济南医保影像云索引",
         "--category", "影像", "--authority", "济南市医保影像云",
         "--mode", "HYBRID", "--biz-dir", "5、医保影像云上报",
         "--workspace", ws, "--dry-run"], capture_output=True, text=True)
    assert r.returncode == 0
    assert "已存在（复用" in r.stdout           # 种子复用而非新建
    assert "将追加种子条目" not in r.stdout     # 不重复追加
