"""扫描输出模拟器（07 §8 演示：3张玻片 + 1次重扫）。

在 watch root 下生成 vendor manifest 模式的扫描输出：
  {watch}/scan-{id}/manifest.json   （完成清单，原子改名发布）
  {watch}/scan-{id}/{barcode}.wsi   （合成字节，非真实图像）
重扫 = 同 barcode、新 scan id、source_revision+1。
"""
from __future__ import annotations

import json
import os
import secrets
import uuid
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_scan(watch_root: str, scan_id: str, barcode: str, *,
               revision: str = "1", size_bytes: int = 2 * 1024 * 1024,
               source_system_code: str = "scanner-simulator",
               agent_id: str = "00000000-0000-4000-8000-000000000002",
               pathology_hint: str | None = None,
               extra_files: list[dict] | None = None) -> dict:
    """生成一次扫描输出并发布完成清单。返回 manifest。"""
    scan_dir = os.path.join(watch_root, scan_id)
    os.makedirs(scan_dir, exist_ok=True)
    files = []

    def add_file(rel: str, role: str, size: int, fmt: str, media: str):
        path = os.path.join(scan_dir, rel)
        block = 1024 * 1024
        import hashlib
        h = hashlib.sha256()
        with open(path, "wb") as f:
            written = 0
            while written < size:
                n = min(block, size - written)
                data = secrets.token_bytes(n)
                f.write(data)
                h.update(data)
                written += n
        files.append({"client_asset_id": str(uuid.uuid4()),
                      "relative_path": f"{scan_id}/{rel}",
                      "role": role, "size_bytes": size,
                      "sha256": h.hexdigest(), "media_type": media,
                      "format_hint": fmt})

    add_file(f"{barcode}.wsi", "primary", size_bytes, "synthetic-opaque",
             "application/octet-stream")
    add_file(f"{barcode}.label.jpg", "label", 4096, "synthetic-label",
             "image/jpeg")
    for ef in (extra_files or []):
        add_file(ef["name"], ef.get("role", "other"), ef.get("size", 512),
                 ef.get("format_hint", "synthetic"), ef.get("media_type",
                                                           "application/octet-stream"))
    hints = {"barcode_raw": barcode}
    if pathology_hint:
        hints["pathology_no"] = pathology_hint
    manifest = {
        "schema_version": "1.0",
        "event_id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "source_system_code": source_system_code,
        "root_id": os.path.basename(watch_root),
        "captured_at": _now(),
        "source_scan_id": scan_id,
        "source_revision": revision,
        "identity_hints": hints,
        "completion_evidence": {
            "method": "vendor_manifest",
            "confirmed_at": _now(),
            "evidence_ref": f"simulator:{scan_id}:revision-{revision}",
            "source_immutable_assertion": True},
        "files": files,
    }
    # 先写临时清单，再原子改名为完成清单（03 §3.1 模式2）
    tmp = os.path.join(scan_dir, "manifest.json.tmp")
    final = os.path.join(scan_dir, "manifest.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    os.replace(tmp, final)
    return manifest


def demo_scenario(watch_root: str, size_bytes: int = 2 * 1024 * 1024) -> list[dict]:
    """3张玻片 + 其中1张重扫（07 §8 第一版完成定义）。"""
    manifests = [
        write_scan(watch_root, "scan-2026-0930-001", "900001",
                   size_bytes=size_bytes),
        write_scan(watch_root, "scan-2026-0930-002", "900002",
                   size_bytes=size_bytes),
        write_scan(watch_root, "scan-2026-0930-003", "900003",
                   size_bytes=size_bytes),
        # 重扫：同玻片条码，新扫描ID、revision=2（B03：3张玻片、4次扫描）
        write_scan(watch_root, "scan-2026-0930-004", "900001", revision="2",
                   size_bytes=size_bytes),
    ]
    return manifests


def main():
    import argparse
    try:
        import _bootstrap  # noqa: F401
    except ImportError:
        pass
    if getattr(__import__("sys"), "frozen", False):
        import sys as _s
        base = os.path.dirname(os.path.abspath(_s.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
    ap = argparse.ArgumentParser(description="blyb 扫描输出模拟器（合成数据）")
    ap.add_argument("--watch", default=os.path.join(base, "scanner-output"))
    ap.add_argument("--size-mb", type=int, default=2)
    ap.add_argument("--single", help="生成单次扫描：--single barcode")
    args = ap.parse_args()
    os.makedirs(args.watch, exist_ok=True)
    if args.single:
        m = write_scan(args.watch, f"scan-{uuid.uuid4().hex[:8]}", args.single,
                       size_bytes=args.size_mb * 1024 * 1024)
        print("scan written:", m["source_scan_id"])
    else:
        for m in demo_scenario(args.watch, size_bytes=args.size_mb * 1024 * 1024):
            print("scan written:", m["source_scan_id"],
                  "barcode:", m["identity_hints"]["barcode_raw"])


if __name__ == "__main__":
    main()
