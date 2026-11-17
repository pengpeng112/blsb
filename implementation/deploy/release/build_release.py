"""发行包构建（execution-plan §7 P1；DEP-A03/A13）。

职责（构建机执行，需 Docker 与前端依赖；目标机离线安装）：
1. 闸门：.dockerignore 与白名单必须存在；构建上下文不得包含 var/SQLite/凭据
   （列出可疑命中即退出——不是“先构建再删”）。
2. 前端同源构建：VITE_CORE_DEFAULT=''、VITE_STORAGE_DEFAULT='/storage'。
3. 后端镜像：按 requirements-linux.txt 最小锁构建 blyb/backend:<release-id>；
   基镜像 digest 与容器内 pip freeze 记入 manifest（linux-runtime.lock）。
4. 基础镜像固化：拉取并重打 tag 的 nginx/postgres 一并入包（离线 docker load）。
5. 白名单打包 implementation 子集 + docker save 镜像 tar + SHA256SUMS + manifest.json。

不做：访问真实业务系统、写目标机、生成密钥（install.sh 在目标机生成）。

用法（工作区根）：python implementation/deploy/release/build_release.py --out <dir>
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

IMPL = Path(__file__).resolve().parents[2]
NGINX_IMAGE = "nginx:1.27-alpine"
PG_IMAGE = "postgres:16"


def run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    # Windows：npm 等是 *.cmd，须经 which 解析后传给 CreateProcess
    exe = shutil.which(str(cmd[0])) or str(cmd[0])
    argv = [exe] + [str(c) for c in cmd[1:]]
    print("+", " ".join(argv[:8]), "..." if len(argv) > 8 else "")
    return subprocess.run(argv, cwd=cwd, env=env, check=check,
                          text=True, capture_output=True)


def must(c: subprocess.CompletedProcess, what: str) -> str:
    if c.returncode != 0:
        print(f"[release] 失败：{what}\nstdout:\n{c.stdout}\nstderr:\n{c.stderr}", file=sys.stderr)
        sys.exit(1)
    return c.stdout.strip()


def load_whitelist() -> tuple[list[str], list[str]]:
    """+包含 / -排除（后匹配优先）；目录前缀或 glob。"""
    inc, exc = [], []
    for line in (IMPL / "deploy/release/whitelist.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("+"):
            inc.append(line[1:])
        elif line.startswith("-"):
            exc.append(line[1:])
        else:
            sys.exit(f"[release] 白名单行必须以 + 或 - 开头：{line}")
    return inc, exc


def path_matches(rel: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/"):
            if rel == pat.rstrip("/") or rel.startswith(pat):
                return True
        elif fnmatch.fnmatch(rel, pat):
            return True
    return False


def whitelist_files() -> list[Path]:
    """按白名单展开的发行文件清单（排除优先）。"""
    inc, exc = load_whitelist()
    files = []
    for p in sorted(IMPL.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(IMPL).as_posix()
        if path_matches(rel, exc):
            continue
        if path_matches(rel, inc):
            files.append(p)
    return files


def gate_context() -> None:
    """DEP-A13 闸门：白名单/.dockerignore 存在，且白名单展开结果不含运行数据/凭据。"""
    if not (IMPL / ".dockerignore").is_file():
        sys.exit("[release] 缺少 implementation/.dockerignore——先落实 D3 再构建")
    bad_tokens = ("var/", ".sqlite3", ".env", "agent-config.json", "node_modules",
                  "__pycache__", ".pytest_cache", "test-certs")
    hits = [(str(p.relative_to(IMPL)), tok)
            for p in whitelist_files()
            for tok in bad_tokens if tok in p.relative_to(IMPL).as_posix()]
    if hits:
        for rel, tok in hits[:20]:
            print(f"[release] 白名单命中可疑内容：{rel}（{tok}）", file=sys.stderr)
        sys.exit("[release] 白名单包含运行数据/凭据/本地产物——修正后再构建")
    print("[release] DEP-A13 闸门通过：白名单无 var/SQLite/凭据/node_modules")


def build_frontend() -> None:
    web = IMPL / "apps/admin_web"
    env = {**os.environ,
           "VITE_CORE_DEFAULT": "",          # 同源默认：/api → nginx → core
           "VITE_STORAGE_DEFAULT": "/storage"}  # 同源默认：nginx 剥前缀 → storage
    must(run(["npm", "run", "build"], cwd=web, env=env), "前端同源构建失败")
    dist = web / "dist/index.html"
    if not dist.is_file():
        sys.exit("[release] 前端构建产物缺失")
    print("[release] 前端同源构建完成（VITE_CORE_DEFAULT=''、VITE_STORAGE_DEFAULT='/storage'）")


def docker_build(release_id: str, backend_tag: str) -> dict:
    py_image = os.environ.get("BLYB_PYTHON_IMAGE", "python:3.14-slim")
    must(run(["docker", "build", "-t", backend_tag,
              "--build-arg", f"PYTHON_IMAGE={py_image}",
              "-f", "Dockerfile", "."], cwd=IMPL), "后端镜像构建失败")
    # 本地构建镜像无 RepoDigests；统一记录 image Id（内容寻址，可复现校验）
    digest = must(run(["docker", "inspect", "--format", "{{.Id}}", backend_tag]),
                  "读取镜像 Id 失败")
    freeze = must(run(["docker", "run", "--rm", backend_tag, "pip", "freeze"]),
                  "容器内 pip freeze 失败")
    return {"tag": backend_tag, "base_image": py_image, "digest": digest,
            "pip_freeze": sorted(freeze.splitlines())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="发行输出目录")
    ap.add_argument("--release-id", default=None)
    ap.add_argument("--skip-frontend", action="store_true",
                    help="前端 dist 已按同源参数构建过时跳过")
    args = ap.parse_args()

    release_id = args.release_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    gate_context()
    if not args.skip_frontend:
        build_frontend()

    backend_tag = f"blyb/backend:{release_id}"
    backend_info = docker_build(release_id, backend_tag)

    # 基础镜像固化（离线目标机无 DNS/出口）
    images: dict[str, str] = {"backend": backend_tag}
    for name, src in (("nginx", NGINX_IMAGE), ("pg", PG_IMAGE)):
        must(run(["docker", "pull", src]), f"拉取 {src} 失败")
        tag = f"blyb/{name}:{release_id}"
        must(run(["docker", "tag", src, tag]), f"重打 tag {tag} 失败")
        images[name] = tag

    # docker save（离线加载）
    img_dir = out / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    for name, tag in images.items():
        fn = img_dir / f"blyb-{name}.tar"
        with fn.open("wb") as f:
            c = subprocess.run(["docker", "save", tag], stdout=f, stderr=subprocess.PIPE)
        if c.returncode != 0:
            sys.exit(f"[release] docker save {tag} 失败：{c.stderr.decode(errors='replace')}")
        print(f"[release] 已导出 {fn.name}")

    # 白名单打包 implementation 子集
    rel_impl = out / "implementation"
    copied = 0
    for p in whitelist_files():
        rel = p.relative_to(IMPL).as_posix()
        dest = rel_impl / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(p.read_bytes())
        copied += 1
    print(f"[release] 白名单打包 {copied} 个文件 → {rel_impl}")

    # manifest（linux-runtime.lock 即 pip_freeze；模板不含密钥）
    manifest = {
        "release_id": release_id,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "images": {k: must(run(["docker", "inspect", "--format", "{{.Id}}", v]),
                           f"读取 {v} 镜像 Id 失败")
                   for k, v in images.items()},
        "image_tags": images,
        "backend": backend_info,
        "runtime_lock": "见 backend.pip_freeze（容器内 pip freeze，Linux x86_64）",
        "acceptance": "DEP-A01—A13（基础）；DEP-B01—B06（离线功能另测）",
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "linux-runtime.lock.txt").write_text(
        "\n".join(backend_info["pip_freeze"]) + "\n", encoding="utf-8")

    # SHA256SUMS（images/ 与 implementation/ 全量；manifest 后生成）
    lines = []
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name != "SHA256SUMS":
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            lines.append(f"{h}  {p.relative_to(out).as_posix()}")
    (out / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[release] 发行完成：{out}（release_id={release_id}，{len(lines)} 个文件已哈希）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
