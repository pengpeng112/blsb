"""Read-only source/evidence inventory; writes only the named audit JSON."""
from __future__ import annotations

import hashlib
import json
import platform
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


def main():
    impl = Path(__file__).resolve().parents[3]
    root = impl.parent
    paths = set()
    suffixes = {".py", ".ts", ".vue", ".json", ".yml", ".yaml", ".ps1", ".md", ".txt"}
    excluded = {"node_modules", "__pycache__", "dist", "var", ".git", ".pytest_cache"}
    for dirname in ("apps", "packages", "plugins", "deploy", "docs", "tests"):
        for p in (impl / dirname).rglob("*"):
            rel = p.relative_to(impl)
            if p.is_file() and p.suffix in suffixes and not excluded.intersection(rel.parts):
                paths.add(p)
    for p in impl.iterdir():
        if p.is_file() and p.suffix in suffixes:
            paths.add(p)
    for p in root.glob("*.md"):
        paths.add(p)
    for p in (root / "设计交付v0.4").rglob("*"):
        if p.is_file() and p.suffix in suffixes:
            paths.add(p)
    formal_index = root / "93ebd53751dc40669f4c977781904f8b.txt"
    if formal_index.exists():
        paths.add(formal_index)
    paths.update((impl / "var").glob("audit-20261001-*.log"))
    paths.update((impl / "var").glob("audit-20261001-*.xml"))
    records = []
    for p in sorted(paths, key=lambda p: p.as_posix()):
        data = p.read_bytes()
        records.append({"path": p.relative_to(root).as_posix(), "size_bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()})
    summaries = {}
    for filename in ("audit-20261001-baseline.xml", "audit-20261001-review-all.xml"):
        p = impl / "var" / filename
        if p.exists():
            suites = ET.parse(p).getroot().iter("testsuite")
            summaries[filename] = [dict(s.attrib) for s in suites]
    manifest = {
        "review_date": "2026-10-01",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version, "platform": platform.platform(),
        "scope": "selected source, design and audit outputs; no data DB or secrets",
        "junit": summaries, "files": records,
    }
    target = impl / "var" / "audit-20261001-manifest.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {target.name}: {len(records)} file hashes")


if __name__ == "__main__":
    main()
