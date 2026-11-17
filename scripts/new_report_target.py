"""新上报业务接入脚手架（LT-09；RT-06 F01 重修：AST 种子定位/原子写/诚实模板）。

用法（工作区根）：
    python scripts/new_report_target.py --code demo_target_a \
        --plugin-key demo_target --name "演示业务A" \
        --category 演示 --authority "演示机构" --mode HYBRID \
        --biz-dir "9、演示业务A" [--dry-run] [--workspace F:\\python\\blyb]

F01 行为（审计整改终验收口）：
- 种子按 code 精确定位：AST 解析 implementation/plugins/report_targets.py 的
  `_seed_rows()` 返回列表，逐个 dict 字面量读 `code`/`plugin_key` 常量——
  不做全文子串包含匹配，不用 rfind 猜最后一个列表；
- 新业务插入带专属标记 `# scaffold:<code>`（幂等判重=精确 code 或专属标记；
  同 code 同参数重放零字节变化；同 code 不同 plugin 拒绝且零写）；
- 插入锚点=列表最后一个元素的行尾（结构定位），锚点丢失/源语法损坏 → 拒绝
  不写错位置；
- `--biz-dir` 只允许单层目录名：拒空值/首尾空白/`/`/`\\`/`.`/`..`/盘符/UNC/
  Windows 保留设备名/尾点/非法字符/控制字符；每个输出目标 realpath 后必须仍
  在 workspace 根内（父目录 symlink/junction 越界同样拒绝）；
- 先完成全部校验（参数/种子冲突/目标路径）再产写计划；--dry-run 零写
  （不建目录/文件、不改 mtime）；生成内容先在内存 ast.parse 语法自检；
  seed_entry 由 JSON 转 Python 字面量（true/false/null → True/False/None）；
- 写序=骨架文件在前、种子最后（暂存+os.replace 原子替换）：任何失败不会留下
  "已改种子却缺 manifest" 的半成品（骨架半成品可幂等续跑补齐）；
- readme_path 使用实际 `数据上报/<biz-dir>/README.md`；
- 标准骨架含 manifest、summary/ledger 占位钩子与 `reg.attach('domain.routes',
  register_routes)` 的真实可导入实现（骨架端点如实"暂无数据"，不返回伪成功）；
  不自动加入生产插件加载集（BLYB_PLUGINS 显式集）、不启用任何发送。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import string
import tempfile

CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,48}$")
PLUGIN_RE = re.compile(r"^[a-z][a-z0-9_]{2,48}$")

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "templates", "report_target")

SEED_MARK_BEGIN = "# >>> new_report_target seed (idempotent block; do not reorder) >>>"
SEED_MARK_END = "# <<< new_report_target seed <<<"

# Windows 保留设备名（不含扩展名的 stem 判定）
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL",
                     *(f"COM{i}" for i in range(1, 10)),
                     *(f"LPT{i}" for i in range(1, 10))}
_ILLEGAL_CHARS = set('<>:"|?*')


def biz_dir_problem(name: str) -> str | None:
    """--biz-dir 单层目录名问题描述（None=合法）。"""
    if not name:
        return "biz-dir 为空"
    if name != name.strip():
        return "biz-dir 有首尾空白"
    if "/" in name or "\\" in name:
        return "biz-dir 含路径分隔符（只允许单层目录名）"
    if name in (".", ".."):
        return "biz-dir 为 . 或 .."
    if re.match(r"^[A-Za-z]:", name) or name.startswith("\\\\"):
        return "biz-dir 含盘符或 UNC 前缀"
    if any(ord(c) < 32 for c in name):
        return "biz-dir 含控制字符"
    if any(c in _ILLEGAL_CHARS for c in name):
        return "biz-dir 含 Windows 非法字符 <>:\"|?*"
    if name.endswith("."):
        return "biz-dir 以点结尾（Windows 会吞掉尾点）"
    if name.split(".")[0].upper() in _WINDOWS_RESERVED:
        return f"biz-dir 使用 Windows 保留设备名 {name.split('.')[0].upper()}"
    return None


def _safe_rel(p: str) -> bool:
    """相对路径安全（拒绝绝对/..；盘符）。"""
    if os.path.isabs(p) or ".." in p.split(os.sep) or ":" in p:
        return False
    return True


def _render(name: str, ctx: dict) -> str:
    tpl = open(os.path.join(TEMPLATES_DIR, name), encoding="utf-8").read()
    return string.Template(tpl).substitute(**ctx)


def _py_literal(obj, indent: int = 8) -> str:
    """JSON 值 → 合法 Python 字面量源码（true/false/null → True/False/None）。"""
    pad = " " * indent
    if obj is None:
        return "None"
    if obj is True:
        return "True"
    if obj is False:
        return "False"
    if isinstance(obj, str):
        return repr(obj)
    if isinstance(obj, (int, float)):
        return repr(obj)
    if isinstance(obj, list):
        if not obj:
            return "[]"
        inner = ", ".join(_py_literal(v, indent + 2) for v in obj)
        return f"[{inner}]"
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        items = ", ".join(
            f"{repr(k)}: {_py_literal(v, indent + 2)}" for k, v in obj.items())
        return f"{{{items}}}"
    raise TypeError(f"unsupported literal type: {type(obj)!r}")


def _seed_entry(code: str, name: str, category: str, authority: str,
                plugin_key: str, mode: str, biz_dir: str) -> str:
    entry = {
        "code": code, "name": name, "category": category,
        "authority": authority, "plugin_key": plugin_key, "mode": mode,
        "lifecycle": "PLANNED", "runtime_kind": "PLATFORM",
        "sole_pusher": "UNCONFIRMED", "sole_pusher_note": "",
        "spec_docs": [], "readme_path": f"数据上报/{biz_dir}/README.md",
        "schedule_note": "", "extra": {},
    }
    body = _py_literal(entry)
    return f"        {body},  # scaffold:{code} (new_report_target seed)"


def _analyze_seed_module(src: str) -> dict:
    """AST 解析 _seed_rows 的返回列表 → {code: {plugin_key, lineno}} +
    插入点（列表最后一个元素之后的行号）+ 错误描述。

    - 源语法损坏 → error；
    - 缺 _seed_rows / 缺 return list / 非法元素 → error（拒绝写错位置）。
    """
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return {"error": f"种子源码语法损坏：{exc}"}
    fn = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                node.name == "_seed_rows":
            fn = node
            break
    if fn is None:
        return {"error": "未找到 _seed_rows 定义（插入锚点丢失）"}
    ret = None
    for n in ast.walk(fn):
        if isinstance(n, ast.Return) and isinstance(n.value, ast.List):
            ret = n.value
            break
    if ret is None:
        return {"error": "_seed_rows 未返回列表字面量（插入锚点丢失）"}
    known: dict[str, dict] = {}
    for el in ret.elts:
        if not isinstance(el, ast.Dict):
            continue  # 允许 *展开/注释等非字面量元素（不猜）
        code = plugin = None
        for k, v in zip(el.keys, el.values):
            if isinstance(k, ast.Constant) and k.value == "code" and \
                    isinstance(v, ast.Constant):
                code = str(v.value)
            if isinstance(k, ast.Constant) and k.value == "plugin_key" and \
                    isinstance(v, ast.Constant):
                plugin = str(v.value)
        if code:
            known[code] = {"plugin_key": plugin, "lineno": el.lineno}
    insert_after = ret.elts[-1].end_lineno if ret.elts else \
        (ret.lineno)  # 空列表：插到 [ 行之后由调用方处理
    return {"known": known, "insert_after": insert_after,
            "list_end": ret.end_lineno}


def _atomic_write(path: str, content: str) -> None:
    """暂存+同目录 os.replace 原子写（UTF-8, LF）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path),
                               prefix=".rt06-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="新上报业务骨架生成（幂等；已存在不覆盖）")
    ap.add_argument("--code", required=True)
    ap.add_argument("--plugin-key", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--category", required=True)
    ap.add_argument("--authority", required=True)
    ap.add_argument("--mode", required=True,
                    choices=["PUSH", "PULL", "HYBRID"])
    ap.add_argument("--biz-dir", required=True, help="数据上报/<biz-dir>（单层目录名）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workspace", default=os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    args = ap.parse_args(argv)

    ws = os.path.abspath(args.workspace)
    ws_real = os.path.realpath(ws)
    if not CODE_RE.fullmatch(args.code):
        print(f"拒绝：code {args.code!r} 不合法（小写字母开头，[a-z0-9_]）")
        return 2
    if not PLUGIN_RE.fullmatch(args.plugin_key):
        print(f"拒绝：plugin-key {args.plugin_key!r} 不合法")
        return 2
    problem = biz_dir_problem(args.biz_dir)
    if problem:
        print(f"拒绝：{problem}")
        return 2
    # 种子文件必须存在（在所有校验后、读文件前；缺失=非本仓库工作区）
    rt_path = os.path.join(ws, "implementation", "plugins", "report_targets.py")
    if not os.path.isfile(rt_path):
        print(f"拒绝：工作区缺 {rt_path}（--workspace 应指向仓库根）")
        return 2

    ctx = {"code": args.code, "plugin_key": args.plugin_key,
           "name": args.name, "category": args.category,
           "authority": args.authority, "mode": args.mode,
           "biz_dir": args.biz_dir}

    # ---- 阶段1：全部校验与写计划（先校验，后产计划；dry-run 零写） ----
    plan: list[tuple[str, str]] = []   # (relpath, content)

    for tpl, rel in [
        ("plugin_init.tmpl", f"implementation/plugins/domains/{args.plugin_key}/__init__.py"),
        ("manifest.json.tmpl", f"implementation/plugins/domains/{args.plugin_key}/manifest.json"),
        ("plugin.py.tmpl", f"implementation/plugins/domains/{args.plugin_key}/plugin.py"),
        ("routes.py.tmpl", f"implementation/plugins/domains/{args.plugin_key}/routes.py"),
        ("hooks.py.tmpl", f"implementation/plugins/domains/{args.plugin_key}/hooks.py"),
        ("plugin_readme.md.tmpl", f"implementation/plugins/domains/{args.plugin_key}/README.md"),
        ("profile_json.tmpl", f"implementation/profiles/{args.code}/profile.json"),
        ("skeleton_test.tmpl", f"implementation/tests/unit/test_{args.plugin_key}_skeleton.py"),
        ("biz_readme.md.tmpl", f"数据上报/{args.biz_dir}/README.md"),
    ]:
        plan.append((rel, _render(tpl, ctx)))

    # 输出目标越界校验（realpath 后必须在 workspace 根内；父路径 symlink/
    # junction 越界同样拒绝——realpath 已解析）
    for rel, _c in plan:
        target = os.path.join(ws, rel.replace("/", os.sep))
        real = os.path.realpath(target)
        if real != ws_real and not real.startswith(ws_real + os.sep):
            print(f"拒绝：输出目标越界 {rel} → {real}")
            return 2

    # 种子：AST 精确 code 定位；已存在校验一致；不存在带专属标记插入
    rt_src = open(rt_path, encoding="utf-8").read()
    info = _analyze_seed_module(rt_src)
    if "error" in info:
        print(f"拒绝：{info['error']}")
        return 2
    known = info["known"]
    seed_new_content = None
    if args.code in known:
        got = known[args.code].get("plugin_key")
        if got != args.plugin_key:
            print(f"拒绝：种子 {args.code} 已存在但 plugin_key 不一致"
                  f"（期望 {args.plugin_key}，实际 {got}；已批准字段不覆盖）")
            return 2
        print(f"种子 {args.code} 已存在（复用，验证 plugin_key 一致；不覆盖字段）")
    else:
        entry = _seed_entry(args.code, args.name, args.category,
                            args.authority, args.plugin_key, args.mode,
                            args.biz_dir)
        lines = rt_src.splitlines(keepends=True)
        after = info["insert_after"]
        seed_new_content = "".join(lines[:after]) + entry + "\n" + \
            "".join(lines[after:])
        try:
            ast.parse(seed_new_content)     # 内存语法自检（不落盘）
        except SyntaxError as exc:
            print(f"拒绝：插入后种子语法自检失败：{exc}（零写）")
            return 2

    # ---- 阶段2：执行（骨架在前、种子最后；原子替换；失败不留种子半成品） ----
    n_new = 0
    skipped = 0
    for rel, content in plan:
        target = os.path.join(ws, rel.replace("/", os.sep))
        if os.path.exists(target):
            print(f"skip（已存在）: {rel}")
            skipped += 1
            continue
        if args.dry_run:
            print(f"[dry-run] 将创建: {rel}")
        else:
            _atomic_write(target, content)
            print(f"created: {rel}")
        n_new += 1

    if seed_new_content is not None:
        if args.dry_run:
            print(f"[dry-run] 将追加种子条目：{args.code}（AST 定位第 "
                  f"{info['insert_after']} 行后，专属标记 scaffold:{args.code}）")
        else:
            _atomic_write(rt_path, seed_new_content)
            print(f"created: implementation/plugins/report_targets.py"
                  f"（种子 {args.code}，AST 锚点插入）")
        n_new += 1

    tail = "dry-run" if args.dry_run else "完成"
    print(f"\n{tail}：计划/处理 {len(plan) + (0 if args.code in known else 1)} 项，"
          f"新增 {n_new}，跳过 {skipped}。重复执行幂等（已存在不覆盖）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
