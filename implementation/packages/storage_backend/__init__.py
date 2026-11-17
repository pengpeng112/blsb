"""文件系统存储后端（03 §5 首版）。

目录布局（卷根下）：
  staging/{session_id}/chunks/{index}.chunk
  assembling/{asset_id}.tmp
  objects/{prefix}/{blob_id}
  quarantine/{id}
物理路径全部由服务端生成，不接受客户端提供的物理路径。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass

CHUNK_SUFFIX = ".chunk"


def safe_join(root: str, *parts: str) -> str:
    out = os.path.abspath(os.path.join(root, *parts))
    root_abs = os.path.abspath(root)
    if not (out == root_abs or out.startswith(root_abs + os.sep)):
        raise ValueError("path escapes volume root")
    return out


class PathPolicy:
    """manifest relative_path 校验（03 §3.3）。"""

    WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}

    @classmethod
    def normalize(cls, rel: str) -> str:
        raw = rel or ""
        if raw.startswith("/") or raw.startswith("\\") or re.match(r"^[A-Za-z]:", raw):
            raise ValueError("absolute path rejected")
        rel = raw.replace("\\", "/").strip("/")
        if not rel:
            raise ValueError("empty relative path")
        if re.search(r"(^|/)\\.\\.", rel) or ".." in rel.split("/"):
            raise ValueError("path traversal rejected")
        if ":" in rel or rel.startswith("/") or len(rel) > 1024:
            raise ValueError("invalid relative path")
        segs = [s for s in rel.split("/") if s]
        for s in segs:
            if not s or s.strip() != s or any(ord(c) < 32 for c in s):
                raise ValueError(f"invalid path segment: {s!r}")
            base = s.split(".")[0].upper()
            if base in cls.WIN_RESERVED:
                raise ValueError(f"windows reserved name: {s}")
        return "/".join(segs)

    @classmethod
    def case_key(cls, normalized: str) -> str:
        return "/".join(s.lower() for s in normalized.split("/"))


@dataclass
class VolumeHandle:
    volume_id: str
    root: str
    safety_margin_bytes: int = 2 * 1024 * 1024 * 1024

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.root).free

    def sentinel_path(self) -> str:
        """挂载哨兵：卷身份文件（W07挂载丢失探测）。

        注册卷时写入；任何写路径发现根存在但哨兵缺失即视为
        "卸载后被同名空目录顶替"，拒绝写入（防脑裂）。
        """
        return safe_join(self.root, f".blyb-vol-{self.volume_id}")

    def ensure_sentinel(self) -> None:
        os.makedirs(self.root, exist_ok=True)
        p = self.sentinel_path()
        if not os.path.exists(p):
            with open(p, "w", encoding="ascii") as f:
                f.write(self.volume_id)

    def mount_ok(self) -> bool:
        return os.path.isfile(self.sentinel_path())

    def ensure_layout(self) -> None:
        for sub in ("staging", "assembling", "objects", "quarantine"):
            os.makedirs(safe_join(self.root, sub), exist_ok=True)

    def chunk_path(self, session_id: str, index: int) -> str:
        return safe_join(self.root, "staging", session_id, "chunks", f"{index:08d}{CHUNK_SUFFIX}")

    def session_dir(self, session_id: str) -> str:
        return safe_join(self.root, "staging", session_id)

    def assembling_path(self, asset_id: str) -> str:
        return safe_join(self.root, "assembling", f"{asset_id}.tmp")

    def object_path(self, blob_id: str) -> str:
        prefix = blob_id[:2]
        return safe_join(self.root, "objects", prefix, blob_id)

    def quarantine_path(self, ident: str) -> str:
        return safe_join(self.root, "quarantine", ident)


def sha256_of_file(path: str, bufsize: int = 4 * 1024 * 1024) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            block = f.read(bufsize)
            if not block:
                break
            h.update(block)
            size += len(block)
    return h.hexdigest(), size


async def atomic_write_chunk_async(vol: VolumeHandle, session_id: str, index: int,
                                   astream) -> tuple[str, int]:
    """async 版本：用于 FastAPI 请求体流式接收，避免嵌套事件循环。"""
    final = vol.chunk_path(session_id, index)
    os.makedirs(os.path.dirname(final), exist_ok=True)
    tmp = final + ".tmp"
    h = hashlib.sha256()
    size = 0
    with open(tmp, "wb") as f:
        async for block in astream:
            if not isinstance(block, (bytes, bytearray, memoryview)):
                raise TypeError("non-bytes chunk block")
            f.write(block)
            h.update(block)
            size += len(block)
        f.flush()
        os.fsync(f.fileno())
    return tmp, (h.hexdigest(), size)


def publish_chunk(tmp_path: str, final_path: str) -> None:
    os.replace(tmp_path, final_path)


def assemble_file(vol: VolumeHandle, session_id: str, chunk_paths: list[str], asset_id: str,
                  expected_sha256: str, expected_size: int) -> str:
    """按索引顺序合并分片并整文件校验；成功返回对象路径（未改名前的 assembling 文件）。"""
    target = vol.assembling_path(asset_id)
    h = hashlib.sha256()
    size = 0
    with open(target, "wb") as out:
        for p in chunk_paths:
            with open(p, "rb") as src:
                while True:
                    block = src.read(4 * 1024 * 1024)
                    if not block:
                        break
                    out.write(block)
                    h.update(block)
                    size += len(block)
        out.flush()
        os.fsync(out.fileno())
    if size != expected_size:
        raise ValueError(f"assembled size {size} != expected {expected_size}")
    digest = h.hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"assembled sha256 mismatch: {digest} != {expected_sha256}")
    return target


def commit_object(vol: VolumeHandle, assembling_path: str, blob_id: str) -> str:
    """同卷原子改名到 objects/ 并 fsync 目录（Windows rename 原子性按目标卷实测记录）。"""
    final = vol.object_path(blob_id)
    os.makedirs(os.path.dirname(final), exist_ok=True)
    os.replace(assembling_path, final)
    try:
        dfd = os.open(os.path.dirname(final), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass  # 部分文件系统不支持目录 fsync；改名已完成，提交意图表兜底
    return final


def parse_range_spec(header: str, total: int):
    """解析 Range 头（R24/R25 统一条件请求语义）。

    返回 (state, rng)：state ∈ NONE / SINGLE / MULTI / UNSATISFIABLE / INVALID
    - NONE：无头或空 → 200 全量
    - SINGLE：(start,end) 闭合区间 → 206；suffix 超长裁剪到整文件、end 超末尾裁剪
    - MULTI：合法多段 → 首版按 03 §7 设计忽略，返回 200 全量
    - UNSATISFIABLE：start 越界（416，带 Content-Range */total）
    - INVALID：语法错误（416）
    """
    if not header:
        return "NONE", None
    if not header.startswith("bytes="):
        return "INVALID", None
    spec = header[len("bytes="):].strip()
    if not spec:
        return "INVALID", None
    if "," in spec:
        return "MULTI", None  # 多段首版忽略（设计03 §7），不按416处理
    import re as _re
    m = _re.fullmatch(r"(\d*)-(\d*)", spec)
    if not m:
        return "INVALID", None
    start_s, end_s = m.group(1), m.group(2)
    if start_s == "" and end_s == "":
        return "INVALID", None
    if start_s == "":
        length = int(end_s)
        if length <= 0:
            return "INVALID", None
        if length >= total:
            return "SINGLE", (0, total - 1)  # suffix 超长：裁剪为整文件
        return "SINGLE", (total - length, total - 1)
    start = int(start_s)
    if start >= total:
        return "UNSATISFIABLE", None
    if end_s == "":
        return "SINGLE", (start, total - 1)
    end = int(end_s)
    if end < start:
        return "INVALID", None
    return "SINGLE", (start, min(end, total - 1))  # end 超末尾：裁剪


def parse_range(header: str, total: int) -> tuple[int, int] | None:
    """兼容旧签名：单段返回区间；多段/无/非法返回 None（调用方回 416 由上层处理）。

    新代码应使用 parse_range_spec 以区分 MULTI（忽略并200）与 UNSATISFIABLE（416）。
    """
    state, rng = parse_range_spec(header, total)
    return rng if state == "SINGLE" else None
