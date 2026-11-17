"""离线发行材料构建（W14）：锁定版本、wheelhouse、哈希清单、SBOM、许可证。

用法（在 implementation/ 下执行）：
  python deploy/offline/build_offline.py [--out deploy/offline/dist]

产出：
  requirements.lock.txt   当前环境实测解析的精确版本（pip freeze，含传递依赖）
  wheelhouse/             直接依赖的wheel缓存（隔离网安装源）
  sha256-manifest.json    wheelhouse + lock 的 SHA-256 清单
  sbom.json               依赖清单（name/version/license/homepage，importlib.metadata）
  licenses.txt            许可证文本汇总（仅随包携带 METADATA 声明者）

边界（如实）：本脚本在开发机生成；清洁隔离网安装演练未执行（无清洁环境）。
wheelhouse 默认只含直接依赖（requirements.txt）；传递依赖由 lock 文件锁定，
离线安装时若需完整传递 wheelhouse，用 --all 重新生成。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from importlib import metadata

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DIRECT_DEPS = ["fastapi", "uvicorn", "sqlalchemy", "pydantic", "httpx"]
EXTRAS_NOTE = ("pyodbc/oracledb 为可选真库驱动（按医院环境单独提供），"
               "缺失时适配器显式 DRIVER_UNAVAILABLE")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(HERE, "deploy", "offline", "dist"))
    parser.add_argument("--all", action="store_true",
                        help="wheelhouse 包含全部传递依赖（体积大）")
    args = parser.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    wheelhouse = os.path.join(out, "wheelhouse")
    os.makedirs(wheelhouse, exist_ok=True)

    # 1) 锁文件：当前解释器实测解析的精确版本
    freeze = subprocess.run([sys.executable, "-m", "pip", "freeze"],
                            capture_output=True, text=True, check=True).stdout
    lock_path = os.path.join(out, "requirements.lock.txt")
    with open(lock_path, "w", encoding="utf-8") as f:
        f.write("# blyb 离线锁（生成于构建机实测环境；含传递依赖）\n")
        f.write(f"# python={sys.version.split()[0]} platform={sys.platform}\n")
        f.write(f"# 可选真库驱动：{EXTRAS_NOTE}\n")
        f.write(freeze)
    print(f"lock → {lock_path} ({len([l for l in freeze.splitlines() if l])} packages)")

    # 2) wheelhouse（直接依赖；--all 含传递）
    req = os.path.join(HERE, "requirements.txt")
    dl = [sys.executable, "-m", "pip", "download", "-r", req,
          "-d", wheelhouse, "--only-binary", ":all:"]
    if args.all:
        dl.append("--all-deps")
    r = subprocess.run(dl, capture_output=True, text=True)
    if r.returncode != 0:
        print("wheelhouse download failed（可能无网络或源 wheel 不可用）：")
        print(r.stderr[-1500:])
        return 2
    wheels = sorted(os.listdir(wheelhouse))
    print(f"wheelhouse → {len(wheels)} files")

    # 3) 哈希清单
    manifest = {
        "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(),
        "python": sys.version.split()[0],
        "lock_sha256": sha256_file(lock_path),
        "files": [{"path": f"wheelhouse/{w}", "sha256": sha256_file(
            os.path.join(wheelhouse, w))} for w in wheels],
    }
    mpath = os.path.join(out, "sha256-manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"manifest → {mpath}")

    # 4) SBOM + 许可证（以锁文件包名为准）
    sbom, lic_lines = [], []
    for line in freeze.splitlines():
        name = line.split("==")[0].strip()
        if not name or name.startswith(("#", "-")):
            continue
        try:
            md = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        entry = {
            "name": md.metadata["Name"],
            "version": md.version,
            "license": md.metadata.get("License", "") or "",
            "home_page": md.metadata.get("Home-page", "") or "",
            "requires_python": md.metadata.get("Requires-Python", "") or "",
            "kind": "direct" if name.lower().replace("-", "_") in
                    {d.lower().replace("-", "_") for d in DIRECT_DEPS} else "transitive",
        }
        sbom.append(entry)
        classifiers = [c for c in (md.metadata.get_all("Classifier") or [])
                       if c.startswith("License ::")]
        lic_lines.append(f"{entry['name']}=={entry['version']}\t"
                         f"{entry['license'] or (classifiers[0] if classifiers else 'UNKNOWN')}")
    with open(os.path.join(out, "sbom.json"), "w", encoding="utf-8") as f:
        json.dump({"bom_format": "blyb-simple-sbom",
                   "note": "生成于开发机；清洁隔离网安装演练未执行",
                   "components": sbom}, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "licenses.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(set(lic_lines))) + "\n")
    print(f"sbom → {len(sbom)} components; licenses → licenses.txt")
    print("注意：隔离网安装演练与PG容器形态未在本机执行（如实标注）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
