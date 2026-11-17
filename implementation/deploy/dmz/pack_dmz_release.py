#!/usr/bin/env python3
"""DMZ 转发器离线发行包打包器（DTR-FINALIZE-20261020 FIN-03/08）。

产出单一 tar.gz（含 SHA256SUMS 与 manifest.json），供 install.sh 首装/升级：

  python deploy/dmz/pack_dmz_release.py \
      [--runtime <cpython-el7-install_only.tar.gz>] \
      [--certs <dir>] [--certs-test-only] [--out <dir>]

材料清单：
  forwarder.py / blyb-dmz-forwarder.service / blyb-dmz-forwarder.socket /
  config.template.json / README.socket.md / install.sh / manifest.json /
  SHA256SUMS / [runtime/<tar.gz>] / [certs/*]

manifest.json.files 覆盖**实际装入**的每个文件的 sha256（install.sh --verify
按此复核当前 release 完整性）；runtime 段记录来源/官方 sha256/许可证说明。
certs 只应打包 TEST-ONLY 链并在 manifest 标注；正式证书走受控渠道另行交付，
绝不进入 Git/镜像/通用哈希日志。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMPL_ROOT = HERE.parents[1]

MATERIAL_FILES = (
    "forwarder.py",
    "blyb-dmz-forwarder.service",
    "blyb-dmz-forwarder.socket",
    "config.template.json",
    "README.socket.md",
    "install.sh",
)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def git_rev() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=IMPL_ROOT.parent,
            capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def to_lf(data: bytes) -> bytes:
    """发行材料一律 LF（CRLF shebang 会让 Linux 直接 127）。"""
    return data.replace(b"\r\n", b"\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runtime", default="",
                    help="el7 兼容私有运行时 tar.gz（python-build-standalone"
                         " install_only；官方 sha256 需能用 --runtime-sha 提供）")
    ap.add_argument("--runtime-sha", default="",
                    help="运行时 tar.gz 的官方来源 sha256（与 --runtime 一起校验）")
    ap.add_argument("--runtime-url", default="",
                    help="运行时来源 URL（写 manifest 供审计）")
    ap.add_argument("--certs", default="", help="证书目录（TEST-ONLY 链）")
    ap.add_argument("--certs-test-only", action="store_true", default=True)
    ap.add_argument("--out", default=str(IMPL_ROOT / "var" / "dmz-release"),
                    help="输出目录（默认 implementation/var/dmz-release）")
    args = ap.parse_args()

    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    files: dict[str, str] = {}
    staged_hashes: dict[str, bytes] = {}   # 归一 LF 后的字节（staging 直写）
    manifest: dict = {
        "name": "blyb-dmz-forwarder",
        "version": version,
        "git_rev": git_rev(),
        "systemd_compat": "219 (Anolis 7.9 verified directives only)",
        "upstream": "https://eco.taobao.com/router/rest (fixed)",
        "files": files,
    }

    # 基础材料
    for name in MATERIAL_FILES:
        src = HERE / name
        if not src.is_file():
            print(f"missing material: {src}", file=sys.stderr)
            return 1
        data = to_lf(src.read_bytes())
        h = hashlib.sha256(data).hexdigest()
        files[name] = h
        staged_hashes[name] = data

    # 运行时
    runtime_meta = {}
    if args.runtime:
        rt = Path(args.runtime)
        if not rt.is_file():
            print(f"runtime tarball not found: {rt}", file=sys.stderr)
            return 1
        local = sha256_file(rt)
        if args.runtime_sha and args.runtime_sha.lower() != local:
            print(f"runtime sha mismatch: official={args.runtime_sha} "
                  f"local={local}", file=sys.stderr)
            return 1
        runtime_meta = {
            "name": rt.name,
            "sha256": local,
            "official_sha256": args.runtime_sha or "(not provided)",
            "source_url": args.runtime_url or "(not provided)",
            "license": "Python Software Foundation License (CPython); "
                       "bundled OpenSSL (Apache-2.0); "
                       "python-build-standalone distribution",
            "support": "CPython 3.11 series is a maintained line; verify "
                       "patch level at each re-pack",
        }
        manifest["runtime"] = runtime_meta
        files[f"runtime/{rt.name}"] = local

    # 证书（仅 TEST-ONLY）
    if args.certs:
        certs = Path(args.certs)
        names = sorted(p.name for p in certs.iterdir() if p.is_file())
        if not names:
            print(f"certs dir empty: {certs}", file=sys.stderr)
            return 1
        for n in names:
            files[f"certs/{n}"] = sha256_file(certs / n)
        manifest["certs"] = {
            "test_only": bool(args.certs_test_only),
            "files": names,
            "note": "TEST-ONLY chain for isolated acceptance; production "
                    "certificates are delivered via restricted channel and "
                    "must NOT be packed here",
        }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stage = out_dir / f"material-{version}"
    if stage.exists():
        print(f"stage dir exists: {stage}", file=sys.stderr)
        return 1
    stage.mkdir()
    for name, data in staged_hashes.items():
        (stage / name).write_bytes(data)
    if args.runtime:
        rt_dir = stage / "runtime"
        rt_dir.mkdir()
        (rt_dir / Path(args.runtime).name).write_bytes(
            Path(args.runtime).read_bytes())
    if args.certs:
        c_dir = stage / "certs"
        c_dir.mkdir()
        for n in manifest.get("certs", {}).get("files", []):
            (c_dir / n).write_bytes((Path(args.certs) / n).read_bytes())
    with open(stage / "manifest.json", "w", encoding="utf-8",
              newline="\n") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    files["manifest.json"] = sha256_file(stage / "manifest.json")
    # SHA256SUMS 覆盖除自身外全部文件（install.sh 逐条校验 + 路径越界防护）；
    # 必须写 LF——CRLF 会让 Linux sha256sum 把 \r 当文件名一部分
    lines = [f"{h}  {rel}" for rel, h in sorted(files.items())]
    with open(stage / "SHA256SUMS", "w", encoding="utf-8",
              newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    tar_path = out_dir / f"blyb-dmz-forwarder-{version}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for p in sorted(stage.iterdir()):
            tf.add(p, arcname=p.name)
    sidecar = out_dir / f"{tar_path.name}.sha256"
    with open(sidecar, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{sha256_file(tar_path)}  {tar_path.name}\n")
    print(json.dumps({"version": version, "package": str(tar_path),
                      "sha256": sha256_file(tar_path),
                      "files": len(files) + 1, "runtime": bool(runtime_meta),
                      "certs": bool(args.certs)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
