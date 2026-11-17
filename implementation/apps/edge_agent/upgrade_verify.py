"""升级包签名验证框架（W13）。

形态：HMAC-SHA256（密钥由部署方以文件形式分发，升级密钥与运行密钥分离）。
- 签名对象：规范化的 manifest JSON（sort_keys、ensure_ascii=False）。
- manifest: {"files": [{"path","sha256","size_bytes"}...], "version", "created_at"}。
- verify 升级包时逐文件校验 sha256/size（不给路径穿越：path 必须相对）。

这是"签名验证框架"：验签通过≠安装已实现（安装仍显式 CAPABILITY_UNSUPPORTED，
由操作员按 runbook 执行）；框架本身可独立测试与离线演练。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os


def canonical_manifest_bytes(manifest: dict) -> bytes:
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sign_manifest(manifest: dict, key_bytes: bytes) -> str:
    return hmac.new(key_bytes, canonical_manifest_bytes(manifest),
                    hashlib.sha256).hexdigest()


def verify_upgrade_bundle(manifest: dict, signature: str, public_key_path: str,
                          *, base_dir: str | None = None) -> tuple[bool, str]:
    """验签 + （可选 base_dir 时）逐文件校验。返回 (verified, detail)。"""
    try:
        with open(public_key_path, "rb") as f:
            key = f.read().strip()
        if not key:
            return False, "empty verification key"
    except OSError as exc:
        return False, f"verification key unreadable: {exc}"
    expected = hmac.new(key, canonical_manifest_bytes(manifest),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(signature or "")):
        return False, "signature mismatch"
    for entry in manifest.get("files", []):
        path = entry.get("path", "")
        if not path or os.path.isabs(path) or ".." in path.replace("\\", "/").split("/"):
            return False, f"invalid file path in manifest: {path!r}"
        if base_dir is None:
            continue
        full = os.path.join(base_dir, path)
        if not os.path.isfile(full):
            return False, f"file missing: {path}"
        h = hashlib.sha256()
        size = 0
        with open(full, "rb") as f:
            while True:
                block = f.read(4 * 1024 * 1024)
                if not block:
                    break
                h.update(block)
                size += len(block)
        if h.hexdigest() != entry.get("sha256") or size != entry.get("size_bytes"):
            return False, f"file digest/size mismatch: {path}"
    return True, "ok"
