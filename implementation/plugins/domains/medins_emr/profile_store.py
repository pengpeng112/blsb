"""Profile 装载（LT-07；RT-04 B2 revision 化）：从 implementation/profiles/
<target>/ 读契约。Profile 是派生物（生成工具 tools/spec_extract）；运行时只读。

RT-04 B2：
- revisions/index.json 存在时按 active revision 装载（revisions/<rev>/ 下）；
  消费方经 revision_info() 返回 revision 与 digest；
- 旧版（r1=root 平面契约）保留可恢复：删除/改 index.active 即回退；
  无 index 时行为与 LT-07 完全一致（root 目录，平面契约）。
"""
from __future__ import annotations

import hashlib
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "profiles")


def profile_dir(target_code: str) -> str:
    return os.path.join(ROOT, target_code)


def revision_index(target_code: str) -> dict | None:
    p = os.path.join(profile_dir(target_code), "revisions", "index.json")
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def active_revision(target_code: str) -> str:
    idx = revision_index(target_code)
    return str(idx.get("active") or "r1") if idx else "r1"


def _resolve(target_code: str, rel: str) -> str:
    """revision 感知路径：active=r1 → root；否则 revisions/<rev>/。"""
    rev = active_revision(target_code)
    if rev != "r1":
        idx = revision_index(target_code) or {}
        sub = (idx.get("revisions") or {}).get(rev, {}).get("path", "")
        if sub and sub != ".":
            return os.path.join(ROOT, target_code, sub, rel)
    return os.path.join(ROOT, target_code, rel)


def revision_info(target_code: str) -> dict:
    """当前装载 revision 的标识与 digest（消费方随响应返回）。"""
    idx = revision_index(target_code)
    rev = active_revision(target_code)
    if idx is None:
        return {"revision": "r1", "digest": "", "schema_version": 1,
                "source": "root"}
    meta = (idx.get("revisions") or {}).get(rev, {})
    digest = meta.get("digest") or ""
    if not digest:
        pj = _resolve(target_code, "profile.json")
        if os.path.exists(pj):
            digest = hashlib.sha256(open(pj, "rb").read()).hexdigest()
    return {"revision": rev, "digest": digest,
            "schema_version": meta.get("schema_version"),
            "transactions": meta.get("transactions"),
            "source": meta.get("path", ".")}


def load_transactions(target_code: str) -> list[dict]:
    p = _resolve(target_code, "transactions.json")
    if not os.path.exists(p):
        return []
    return json.load(open(p, encoding="utf-8"))


def load_json(rel: str) -> dict | None:
    """按 workspace profiles 相对路径装载（含 target 前缀；兼容旧调用）。"""
    parts = rel.split("/", 1)
    if len(parts) == 2 and os.path.sep not in parts[0]:
        target_code, sub = parts
        p = _resolve(target_code, sub)
    else:
        p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def load_profile_meta(target_code: str) -> dict | None:
    return load_json(os.path.join(target_code, "profile.json"))
