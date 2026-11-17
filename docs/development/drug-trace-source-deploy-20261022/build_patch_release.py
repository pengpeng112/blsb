"""Offline delta release from the exact verified FB image/package.

No dependency resolution or network pull. Only the reviewed five Python files
are replaced. All other release files and application image layers are retained.
"""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "implementation/var/rel-fb"
RID = "src-20261022T103000Z"
OUT = ROOT / "implementation/var" / RID
FILES = ["plugins/sources/db_view/drug_trace.py",
         "plugins/sources/db_view/source_profiles.py",
         "plugins/domains/drug_trace/service.py",
         "plugins/domains/drug_trace/routes.py",
         "plugins/domains/drug_trace/source_config.py"]
HOST_FILES = ["deploy/linux/restore.sh"]


def run(args):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise RuntimeError("command failed: " + " ".join(args[:4]) + "\n" + r.stderr[-2000:])
    return r.stdout.strip()


def main():
    manifest = json.loads((BASE / "manifest.json").read_text("utf-8"))
    for line in (BASE / "SHA256SUMS").read_text().splitlines():
        digest, rel = line.split("  ", 1)
        if hashlib.sha256((BASE / rel).read_bytes()).hexdigest() != digest:
            raise RuntimeError("base package hash mismatch: " + rel)
    for k, tag in manifest["image_tags"].items():
        if k == "pg":
            run(["docker", "tag", "postgres:16", tag])
        assert run(["docker", "inspect", "--format", "{{.Id}}", tag]) == manifest["images"][k]
    if OUT.exists():
        raise RuntimeError("immutable output already exists")
    shutil.copytree(BASE / "implementation", OUT / "implementation")
    (OUT / "images").mkdir()
    ctx = OUT / "build-context"
    ctx.mkdir()
    for rel in FILES:
        p = ROOT / "implementation" / rel
        for dest in (OUT / "implementation" / rel, ctx / rel):
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
    for rel in HOST_FILES:
        shutil.copy2(ROOT / "implementation" / rel, OUT / "implementation" / rel)
    dockerfile = "FROM " + manifest["image_tags"]["backend"] + "\nUSER root\n"
    dockerfile += "".join(f"COPY --chown=10001:10001 {p} /app/{p}\n" for p in FILES)
    dockerfile += "USER blyb\n"
    (ctx / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    tag = f"blyb/backend:{RID}"
    print(run(["docker", "build", "--network=none", "--pull=false", "-t", tag, str(ctx)]))
    old_backend = manifest["backend"].copy()
    tags = {}
    ids = {}
    for k in ("backend", "nginx", "pg"):
        tags[k] = f"blyb/{k}:{RID}"
        if k != "backend":
            run(["docker", "tag", manifest["image_tags"][k], tags[k]])
        ids[k] = run(["docker", "inspect", "--format", "{{.Id}}", tags[k]])
        with (OUT / "images" / f"blyb-{k}.tar").open("wb") as f:
            subprocess.run(["docker", "save", tags[k]], stdout=f, check=True)
    freeze = sorted(run(["docker", "run", "--rm", "--network=none", tag, "pip", "freeze"]).splitlines())
    assert freeze == old_backend["pip_freeze"], "dependency drift"
    manifest.update(release_id=RID, built_at=datetime.now(timezone.utc).isoformat(),
                    images=ids, image_tags=tags,
                    backend={**old_backend, "tag": tag, "digest": ids["backend"],
                             "base_image": old_backend["digest"]},
                    delta={"base_release": "fb-20261021T164800Z",
                           "files": FILES + HOST_FILES, "dependencies_changed": False})
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    shutil.copy2(BASE / "linux-runtime.lock.txt", OUT / "linux-runtime.lock.txt")
    sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(OUT).as_posix()}"
            for p in sorted(OUT.rglob("*")) if p.is_file()]
    (OUT / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print(json.dumps({"release": RID, "hashed_files": len(sums),
                      "backend_digest": ids["backend"], "dependencies_unchanged": True}))


if __name__ == "__main__":
    main()
