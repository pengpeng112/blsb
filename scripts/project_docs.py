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
        "var", "dist", "build", "snapshots", "validation-deps", "校验依赖"}
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
    if path.startswith("归档/") or path.startswith("设计交付v0.3/"):
        return "historical"
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


def broken_links(root):
    result = []
    for p in markdown_files(root):
        rel = p.relative_to(root).as_posix()
        # Archived originals retain their original bytes and relative paths.
        if rel.startswith("归档/") or rel.startswith("设计交付v0.3/"):
            continue
        text = re.sub(r"```.*?```", "", p.read_text(encoding="utf-8-sig"), flags=re.S)
        for value in link_targets(text):
            target = value.strip().strip("<>").split("#", 1)[0]
            if not target or re.match(r"(?:https?://|mailto:|app://|data:)", target):
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
    archive = root / "归档/2026-10-02/归档清单.json"
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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("inventory", "check"))
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
    print(json.dumps({"new_broken_links": new, "preexisting_broken_links": legacy,
                      "source_or_archive_hash_mismatches": bad_sources}, ensure_ascii=False, indent=2))
    return 1 if new or bad_sources else 0


if __name__ == "__main__":
    raise SystemExit(main())
