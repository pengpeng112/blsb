"""Catalog project documents and check local links/source hashes, offline."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SKIP = {"node_modules", "__pycache__", ".git", ".pytest_cache", ".ai", ".v2c", ".video_agent",
        ".opencode", "var", "dist", "build", "snapshots", "validation-deps", "校验依赖"}
HISTORICAL_LINK_SKIP_DIRS = {"baselines", "baseline", "revisions", "before", "review", "evidence", "archive"}
ALLOWED_ROOT_DIRS = {
    ".ai", ".git", ".opencode", ".pytest_cache", ".v2c", ".video_agent",
    "docs", "implementation", "pacs接口上传说明", "scripts", "snapshots",
    "归档", "数据上报", "设计交付v0.3", "设计交付v0.4",
}
ALLOWED_ROOT_FILES = {
    ".gitattributes", ".gitignore", "AGENTS.md", "AI_START.md", "CLAUDE.md",
    "README.md", "project.ps1", "医院数据归集与上报平台总体方案_v0.4.md",
    # 5 root-level hash-pinned originals tracked by 设计交付v0.4/资料提取/来源文件摘要.json
    "93ebd53751dc40669f4c977781904f8b.txt",
    "AI复核报告_v0.3设计与真实库核验_2026-09-30.md",
    "说明.txt",
    "“医保病理云索引”方案(征求意见稿).pdf",
    "医保病理云数据集及字典（征求意见稿）.xlsx",
}
STATE_BLOAT_MAX_LINES = 200
LINK = re.compile(r"\[[^\]]*\]\(")


def link_targets(text):
    # Balanced parentheses are valid in filenames such as (...征求意见稿...).pdf.
    for match in LINK.finditer(text):
        start = pos = match.end()
        depth = 1
        while pos < len(text):
            if text[pos] == "\\":
                pos += 2
                continue
            if text[pos] == "(":
                depth += 1
            elif text[pos] == ")":
                depth -= 1
                if depth == 0:
                    yield text[start:pos]
                    break
            pos += 1


def markdown_files(root):
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SKIP and not (Path(directory) / d).is_symlink()]
        for name in sorted(files):
            if name.endswith(".md"):
                yield Path(directory) / name


def category(path):
    if path.startswith("归档/") or path.startswith("设计交付v0.3/") or path.startswith("docs/ai/archive/"):
        return "historical"
    if path == "docs/HISTORY.md":
        return "history_overview"
    if path.startswith("设计交付v0.4/") or path == "医院数据归集与上报平台总体方案_v0.4.md":
        return "active_design"
    if path.startswith("docs/ai/"):
        return "shared_ai_state"
    if path.startswith("docs/sources/"):
        return "source_index"
    if path.startswith("docs/maintenance/"):
        return "maintenance_evidence"
    if "/docs/adr/" in path:
        return "implementation_decision"
    if "/docs/runbooks/" in path or "/deploy/" in path:
        return "operations"
    if "/docs/audit/" in path:
        return "audit_evidence"
    if path.startswith("implementation/docs/"):
        return "implementation_status"
    if path == "一次性执行任务-本地剩余与内网部署-2026-10-01.md":
        return "pending_task_spec"
    if path == "项目独立复核与详细整改计划_2026-10-01.md":
        return "review_and_execution_history"
    if path.startswith("AI复核报告_"):
        return "historical_source_evidence"
    return "navigation_or_entry"


def _is_historical_snapshot_doc(rel: str) -> bool:
    if rel.startswith("归档/") or rel.startswith("设计交付v0.3/") or rel.startswith("AI复核报告_"):
        return True
    parts = Path(rel).parts[:-1]
    return any(p in HISTORICAL_LINK_SKIP_DIRS for p in parts)


def broken_links(root):
    result = []
    for p in markdown_files(root):
        rel = p.relative_to(root).as_posix()
        # Archived originals and point-in-time baseline/revision snapshots retain original relative paths.
        if _is_historical_snapshot_doc(rel):
            continue
        text = re.sub(r"```.*?```", "", p.read_text(encoding="utf-8-sig"), flags=re.S)
        for value in link_targets(text):
            target = value.strip().strip("<>").split("#", 1)[0]
            if not target or re.match(r"(?:https?://|mailto:|app://|data:|[A-Za-z]:/)", target):
                continue
            if not (p.parent / target).exists():
                result.append({"file": rel, "target": target})
    return sorted(result, key=lambda x: (x["file"], x["target"]))


def source_errors(root):
    errors = []
    origin = root / "设计交付v0.4/资料提取/来源文件摘要.json"
    for r in json.loads(origin.read_text(encoding="utf-8")):
        p = root / r["file"].replace("\\", "/")
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != r["sha256"]:
            errors.append(r["file"])
    for rel_manifest in ("归档/2026-10-02/归档清单.json", "归档/2026-11-21/归档清单.json"):
        archive = root / rel_manifest
        if archive.exists():
            for r in json.loads(archive.read_text(encoding="utf-8-sig"))["files"]:
                p = root / r["archive_path"]
                if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != r["sha256"]:
                    errors.append(r["archive_path"])
    return errors


def inventory(root):
    rows = []
    for p in sorted(markdown_files(root)):
        rel = p.relative_to(root).as_posix()
        content = p.read_text(encoding="utf-8-sig")
        title = next((line.lstrip("# ") for line in content.splitlines() if line.startswith("# ")), p.stem)
        rows.append({"path": rel, "category": category(rel), "title": title})
    data = {"format_version": 1, "baseline": "v0.4", "documents": rows,
            "excluded_generated_or_private_directories": sorted(SKIP),
            "note": "Classification only; this catalog does not declare implementation acceptance."}
    p = root / "docs/catalog.json"
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(rows)


def _suggest_placement(path: Path) -> str:
    if path.is_dir():
        if path.name.lower() in {"var", "tmp", "temp", "build", "dist"}:
            return "清理或移至 implementation/var/（禁止根目录创建平行运行目录）"
        return "按业务归入 数据上报/<编号、业务名>/ 或 docs/development/<专题-YYYYMMDD>/"
    suf = path.suffix.lower()
    if suf in {".xlsx", ".xls", ".pdf", ".doc", ".docx", ".csv"}:
        return "归入 数据上报/<编号、业务名>/（同步更新对应 README.md）"
    if suf in {".md", ".txt"}:
        return "归入 docs/development/<专题-YYYYMMDD>/、数据上报/<编号、业务名>/ 或 归档/<日期>/"
    if suf in {".py", ".ps1", ".sh"}:
        return "归入 scripts/ 或 implementation/ 对应子目录（一次性脚本放临时目录用完即删）"
    if suf in {".out", ".log", ".tmp", ".bak", ".zip", ".tar", ".gz", ".tgz"}:
        return "临时输出/压缩包不应留在根目录，请清理或移至 implementation/var/"
    return "按 AGENTS.md §7 归类放置并更新对应索引"


def root_hygiene_warnings(root: Path):
    warnings = []
    for item in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if item.is_dir() and item.name not in ALLOWED_ROOT_DIRS:
            warnings.append({"path": item.name + "/", "suggestion": _suggest_placement(item)})
        elif item.is_file() and item.name not in ALLOWED_ROOT_FILES:
            warnings.append({"path": item.name, "suggestion": _suggest_placement(item)})
    return warnings


def state_bloat_warnings(root: Path, max_lines: int = STATE_BLOAT_MAX_LINES):
    warnings = []
    for name in ("STATE.md", "HANDOFF.md", "BACKLOG.md"):
        p = root / "docs" / "ai" / name
        if p.is_file():
            cnt = len(p.read_text(encoding="utf-8-sig", errors="ignore").splitlines())
            if cnt > max_lines:
                warnings.append({
                    "file": f"docs/ai/{name}",
                    "lines": cnt,
                    "limit": max_lines,
                    "suggestion": "请将历史条目归档至 docs/ai/archive/，保持活跃摘要精简",
                })
    return warnings


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("inventory", "check"))
    ap.add_argument("--strict", action="store_true", help="treat root hygiene warnings as check failures")
    args = ap.parse_args()
    if args.command == "inventory":
        print(f"Catalogued {inventory(ROOT)} documents in docs/catalog.json")
        return 0
    baseline = ROOT / "docs/maintenance/link_baseline.json"
    known = json.loads(baseline.read_text(encoding="utf-8"))["preexisting_broken_links"] if baseline.exists() else []
    current = broken_links(ROOT)
    new = [x for x in current if x not in known]
    legacy = [x for x in current if x in known]
    bad_sources = source_errors(ROOT)
    root_warns = root_hygiene_warnings(ROOT)
    bloat_warns = state_bloat_warnings(ROOT)
    print(json.dumps({
        "new_broken_links": new,
        "preexisting_broken_links": legacy,
        "source_or_archive_hash_mismatches": bad_sources,
        "root_hygiene_warnings": root_warns,
        "state_bloat_warnings": bloat_warns,
    }, ensure_ascii=False, indent=2))
    if new or bad_sources or (args.strict and (root_warns or bloat_warns)):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
