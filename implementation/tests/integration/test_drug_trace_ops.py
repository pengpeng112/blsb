"""药品追溯 W01：OPS-01/02/03/04 运维修订的离线验证。

- OPS-01：certs.sh 测试 CA/SAN 证书签发与校验（名称不匹配必须拒绝）。
- OPS-02/03：restore.sh 隔离演练（fake docker）——任意 cwd、独立项目/数据根/配置根、
  归档路径穿越/符号链接/非空目标/生产路径重叠全部失败关闭。
- OPS-04：backup.sh 先静止后快照的顺序、静止窗口记录、失败 trap 只恢复原运行服务。

不依赖真实 Docker/目标机；真实隔离 PG 恢复演练在 W09 的 PG 环境另行执行。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402

IMPL = Path(__file__).resolve().parents[2]
LINUX = IMPL / "deploy" / "linux"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash 不可用（部署脚本为 bash）")


def _p_if_win(a) -> str:
    """只转换盘符开头的 Windows 绝对路径；旗标与其他原样返回。"""
    import re
    return _p(a) if re.match(r"^[A-Za-z]:", str(a)) else str(a)


def run_bash(script: str, args: list[str], env_extra: dict, cwd: str | None = None):
    env = {k: str(v) for k, v in os.environ.items()}
    env.update({k: str(v) for k, v in env_extra.items()})
    return subprocess.run([BASH, str(LINUX / script), *[_p_if_win(a) for a in args]], env=env,
                          capture_output=True, text=True, cwd=cwd, timeout=120)


def _p(path) -> str:
    """Windows 路径 → MSYS 正斜杠形式（GNU tar 把 C:/ 视为远程主机语法，必须转换）。"""
    import re
    s = str(Path(path).resolve()).replace("\\", "/")
    return re.sub(r"^([A-Za-z]):", lambda m: "/" + m.group(1).lower(), s)


def bash_cmd(cmd: str):
    return subprocess.run([BASH, "-c", cmd], capture_output=True, text=True, timeout=60)


# ---------------------------------------------------------------- fake docker
FAKE_DOCKER = r'''#!/usr/bin/env bash
# 演练用 docker 替身：记录全部调用；按需模拟 inspect/ps/exec 行为
LOG="${FAKE_DOCKER_LOG:?}"
echo "docker $*" >> "$LOG"
cmd="$1"; shift || true
case "$cmd" in
  image) # docker image inspect <img>
    exit 0 ;;
  inspect)
    fmt=""; args=()
    while [ $# -gt 0 ]; do
      case "$1" in
        --format) fmt="$2"; shift 2;;
        *) args+=("$1"); shift;;
      esac
    done
    case "$fmt" in
      *State.Health*) echo healthy ;;
      *State.Running*)
        [ -f "$FAKE_DOCKER_RUNNING" ] && echo true || echo false ;;
      *) echo "?" ;;
    esac
    exit 0 ;;
  compose)
    project=""; envf=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --project-name) project="$2"; shift 2;;
        --env-file) envf="$2"; shift 2;;
        -f) shift 2;;
        *) break ;;
      esac
    done
    sub="${1:-}"; [ $# -gt 0 ] && shift
    case "$sub" in
      ps) echo "cid-$2" ;;  # ps -q <svc>
      stop|start|down|up|config) exit 0 ;;
      run)
        # 模拟 exchange 备份快照：在 env 指定的交换目录写 .backup-tmp.sqlite3
        if printf '%s\n' "$@" | grep -q backup; then
          exdir="$(grep -E '^BLYB_EXCHANGE_DIR=' "$envf" | tail -1 | cut -d= -f2-)"
          [ -n "$exdir" ] && printf 'exchange-snapshot' > "$exdir/.backup-tmp.sqlite3"
        fi
        exit 0 ;;
      exec)
        if [ "${FAKE_PG_DUMP_FAIL:-0}" = "1" ] && printf '%s\n' "$*" | grep -q pg_dump; then
          echo "simulated pg_dump failure" >&2; exit 1
        fi
        if printf '%s\n' "$*" | grep -q pg_dump; then
          printf 'PGDUMP' > "${FAKE_DOCKER_DUMP:?}"
          exit 0
        fi
        if printf '%s\n' "$*" | grep -q pg_restore; then
          cat > "${FAKE_DOCKER_RESTORED:?}"
          exit 0
        fi
        exit 0 ;;
      *) exit 0 ;;
    esac ;;
  *) exit 0 ;;
esac
'''


@pytest.fixture()
def fake_docker(tmp_path):
    """PATH 注入的 docker 替身 + 一套'生产'目录（假想当前栈）。"""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE_DOCKER, encoding="utf-8", newline="\n")
    os.chmod(bin_dir / "docker", 0o755)
    log = tmp_path / "docker.log"
    prod_root = tmp_path / "prod-root"
    for sub in ("postgres", "objects", "exchange", "backups", "etc-blyb/certs"):
        (prod_root / sub).mkdir(parents=True)
    (prod_root / "etc-blyb" / "runtime.env").write_text(
        "# prod env\nBLYB_DB_PASSWORD=x\nBLYB_ENROLL_TOKEN=y\n"
        "BLYB_BACKEND_IMAGE=blyb/backend:rel-a\nBLYB_NGINX_IMAGE=blyb/nginx:rel-a\n"
        "BLYB_PG_IMAGE=blyb/pg:rel-a\n"
        f"BLYB_PGDATA_DIR={prod_root}/postgres\nBLYB_OBJECTS_DIR={prod_root}/objects\n"
        f"BLYB_EXCHANGE_DIR={prod_root}/exchange\nBLYB_TLS_CERT_DIR={prod_root}/etc-blyb/certs\n"
        "BLYB_HTTPS_BIND=0.0.0.0\nBLYB_HTTPS_PORT=443\n",
        encoding="utf-8")
    # 生产数据非空（模拟在运栈）
    (prod_root / "postgres" / "PG_VERSION").write_text("17\n", encoding="utf-8")
    (prod_root / "exchange" / "exchange.sqlite3").write_text("live\n", encoding="utf-8")
    # 假想发行目录（提供 compose 文件）；lib.sh 以 $BLYB_ROOT/current 为准
    opt_root = tmp_path / "opt-blyb"
    release = opt_root / "current"
    (release / "implementation" / "deploy" / "compose").mkdir(parents=True)
    (release / "implementation" / "deploy" / "compose" / "docker-compose.yml").write_text(
        "services: {db: {image: x}}\n", encoding="utf-8")
    marker = prod_root / "etc-blyb" / "runtime.env"
    yield {
        "bin": str(bin_dir), "log": log, "prod_root": prod_root,
        "prod_env": prod_root / "etc-blyb" / "runtime.env",
        "release": release, "opt_root": opt_root, "marker": marker,
        "dump": tmp_path / "dump.out", "restored": tmp_path / "restored.out",
        "running_flag": tmp_path / "running",
    }
    if log.exists():
        log.unlink()


def _env(fake, **extra):
    env = {
        "PATH": fake["bin"] + os.pathsep + os.environ.get("PATH", ""),
        "FAKE_DOCKER_LOG": str(fake["log"]),
        "FAKE_DOCKER_DUMP": str(fake["dump"]),
        "FAKE_DOCKER_RESTORED": str(fake["restored"]),
        "BLYB_ROOT": _p(fake["opt_root"]),
        "BLYB_DATA_ROOT": _p(fake["prod_root"]),
        "BLYB_ETC": _p(fake["prod_root"] / "etc-blyb"),
    }
    env.update({k: _p_if_win(v) if isinstance(v, Path) else str(v)
                for k, v in extra.items()})
    return env


# ---------------------------------------------------------------- OPS-01
def test_ops01_test_ca_san_cert_roundtrip(tmp_path):
    d = tmp_path / "certs"
    r1 = run_bash("certs.sh", ["make-test-ca", str(d)], {})
    assert r1.returncode == 0, r1.stderr
    r2 = run_bash("certs.sh", ["issue-server", str(d), "--san", "IP:<prod-host>",
                               "--san", "DNS:blyb.internal.example"], {})
    assert r2.returncode == 0, r2.stderr
    if os.name == "posix":  # Windows 不反映 POSIX 权限位
        assert (d / "tls.key").stat().st_mode & 0o077 == 0  # 私钥不开放
    assert run_bash("certs.sh", ["verify", str(d), "--host", "<prod-host>"], {}).returncode == 0
    assert run_bash("certs.sh", ["verify", str(d), "--host", "blyb.internal.example"], {}).returncode == 0
    # 名称/IP 不在 SAN → 必须拒绝（不得变成 -k 通过）
    assert run_bash("certs.sh", ["verify", str(d), "--host", "10.99.99.99"], {}).returncode != 0
    # 重复签发不覆盖
    assert run_bash("certs.sh", ["issue-server", str(d), "--san", "IP:1.2.3.4"], {}).returncode != 0


def test_ops01_status_script_no_insecure_curl():
    text = (LINUX / "status.sh").read_text(encoding="utf-8")
    assert "curl -k" not in text and "curl -ksS" not in text, "status.sh 验收不得依赖 -k"
    assert "--cacert" in text, "TLS 验收必须带 CA 校验"
    assert "subjectAltName" in text, "必须检查 SAN（OPS-01）"


# ---------------------------------------------------------------- OPS-02/03
def _make_backup(tmp_path, *, with_objects=False, env_override=None) -> Path:
    bdir = tmp_path / "backup"
    bdir.mkdir()
    (bdir / "core.pg_dump").write_bytes(b"PGDUMP-BYTES")
    (bdir / "exchange.sqlite3").write_bytes(b"EXCHANGE-BYTES")
    etc = bdir / "etc-staging" / "blyb"
    etc.mkdir(parents=True)
    env_text = (
        "BLYB_DB_PASSWORD=secret\nBLYB_ENROLL_TOKEN=tok\nBLYB_BOOTSTRAP_ADMIN_TOKEN=tok2\n"
        "BLYB_BACKEND_IMAGE=blyb/backend:rel-a\nBLYB_NGINX_IMAGE=blyb/nginx:rel-a\n"
        "BLYB_PG_IMAGE=blyb/pg:rel-a\n"
        "BLYB_PGDATA_DIR=/old/host/postgres\nBLYB_OBJECTS_DIR=/old/host/objects\n"
        "BLYB_EXCHANGE_DIR=/old/host/exchange\nBLYB_TLS_CERT_DIR=/etc/blyb/certs\n"
        "BLYB_HTTPS_BIND=0.0.0.0\nBLYB_HTTPS_PORT=443\n")
    if env_override:
        env_text = env_override(env_text)
    (etc / "runtime.env").write_text(env_text, encoding="utf-8")
    (etc / "certs").mkdir()
    (etc / "certs" / "tls.crt").write_text("cert", encoding="utf-8")
    subprocess.run([BASH, "-c",
                    f"tar -C '{_p(bdir / 'etc-staging')}' -czf '{_p(bdir / 'etc-blyb.tar.gz')}' blyb"],
                   check=True)
    shutil.rmtree(bdir / "etc-staging")
    if with_objects:
        obj = bdir / "obj-staging" / "objects" / "ab"
        obj.mkdir(parents=True)
        (obj / "blob.bin").write_bytes(b"blob-bytes")
        subprocess.run([BASH, "-c",
                        f"tar -C '{_p(bdir / 'obj-staging')}' -czf '{_p(bdir / 'objects.tar.gz')}' objects"],
                       check=True)
        shutil.rmtree(bdir / "obj-staging")
    files = ["core.pg_dump", "exchange.sqlite3", "etc-blyb.tar.gz"]
    if with_objects:
        files.append("objects.tar.gz")
    subprocess.run([BASH, "-c",
                    f"cd '{_p(bdir)}' && sha256sum {' '.join(files)} > SHA256SUMS"], check=True)
    return bdir


def test_ops02_restore_drill_isolated_project_and_dirs(fake_docker, tmp_path):
    """独立项目/数据根/配置根恢复成功；生产目录字节不变；pg_restore 走统一入口。"""
    bdir = _make_backup(tmp_path, with_objects=True)
    drill_root = tmp_path / "drill"
    # 从任意 cwd 执行（OPS-02：不依赖当前目录）
    odd_cwd = tmp_path / "some" / "other" / "cwd"
    odd_cwd.mkdir(parents=True)
    r = run_bash("restore.sh", [str(bdir), "--project", "drill-x",
                                "--data-root", str(drill_root)],
                 _env(fake_docker), cwd=str(odd_cwd))
    assert r.returncode == 0, r.stderr + r.stdout
    # 数据落位
    assert (drill_root / "exchange" / "exchange.sqlite3").read_bytes() == b"EXCHANGE-BYTES"
    assert (drill_root / "objects" / "ab" / "blob.bin").read_bytes() == b"blob-bytes"
    restored_env = (drill_root / "etc-blyb" / "runtime.env").read_text(encoding="utf-8")
    assert _p(drill_root / "postgres") in restored_env
    assert "18443" in restored_env, "演练端口必须与生产 443 隔离"
    assert "127.0.0.1" in restored_env
    # pg_restore 收到备份字节
    assert fake_docker["restored"].read_bytes() == b"PGDUMP-BYTES"
    # 生产目录未被触碰
    assert fake_docker["prod_env"].read_text(encoding="utf-8").startswith("# prod env")
    assert (fake_docker["prod_root"] / "postgres" / "PG_VERSION").exists()
    # compose 调用统一走显式项目（不落生产项目 blyb）
    log = fake_docker["log"].read_text(encoding="utf-8")
    assert "--project-name drill-x" in log
    assert "--project-name blyb " not in log.replace("--project-name blyb-restore", "")
    manifest = json.loads((drill_root / "restore-manifest.json").read_text(encoding="utf-8"))
    assert manifest["project"] == "drill-x"


@pytest.mark.parametrize("relation", ["child", "parent", "equal"])
def test_restore_overlap_guard_with_existing_glob_matches(fake_docker, tmp_path, relation):
    """Existing directories must not expand a path test into multiple args."""
    bdir = _make_backup(tmp_path)
    prod = fake_docker["prod_root"]
    for name in ("a", "b", "c"):
        (prod / name).mkdir(exist_ok=True)
    target = {"child": prod / "would-be-drill", "parent": prod.parent,
              "equal": prod}[relation]
    r = run_bash("restore.sh", [str(bdir), "--data-root", str(target), "--validate-only"],
                 _env(fake_docker))
    assert r.returncode != 0
    assert "重叠" in r.stderr + r.stdout
    assert "too many arguments" not in r.stderr
    assert not (prod / "would-be-drill").exists()


def _refresh_sums(bdir: Path):
    files = [f.name for f in bdir.iterdir()
             if f.name != "SHA256SUMS" and f.is_file()]
    subprocess.run([BASH, "-c",
                    f"cd '{_p(bdir)}' && sha256sum {' '.join(sorted(files))} > SHA256SUMS"],
                   check=True)


def _evil_archive(bdir: Path, *, kind: str):
    """构造恶意 etc-blyb.tar.gz（路径穿越 / 符号链接成员），并重算清单。
    直接测归档安全层：哈希完整性不是这两例的被测对象。"""
    import io
    import tarfile as tf
    payload = (b"BLYB_PGDATA_DIR=/a\nBLYB_OBJECTS_DIR=/b\nBLYB_EXCHANGE_DIR=/c\n")
    out = io.BytesIO()
    with tf.open(fileobj=out, mode="w:gz") as tar:
        info = tf.TarInfo("blyb/runtime.env")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
        if kind == "traversal":
            info = tf.TarInfo("blyb/../../etc/passwd-escape")
            data = b"evil"
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        elif kind == "symlink":
            info = tf.TarInfo("blyb/certs")
            info.type = tf.SYMTYPE
            info.linkname = "../../etc"
            tar.addfile(info)
    (bdir / "etc-blyb.tar.gz").write_bytes(out.getvalue())
    _refresh_sums(bdir)


def test_ops03_restore_rejects_path_traversal_archive(fake_docker, tmp_path):
    bdir = _make_backup(tmp_path)
    _evil_archive(bdir, kind="traversal")
    drill = tmp_path / "drill1"
    r = run_bash("restore.sh", [str(bdir), "--data-root", str(drill)],
                 _env(fake_docker))
    assert r.returncode != 0
    assert "穿越" in (r.stderr + r.stdout)
    assert not drill.exists() or not any(drill.iterdir())


def test_ops03_restore_rejects_symlink_member(fake_docker, tmp_path):
    bdir = _make_backup(tmp_path)
    _evil_archive(bdir, kind="symlink")
    r = run_bash("restore.sh", [str(bdir), "--data-root", str(tmp_path / "drill2")],
                 _env(fake_docker))
    assert r.returncode != 0
    assert "链接" in (r.stderr + r.stdout)


def test_ops03_restore_rejects_nonempty_target(fake_docker, tmp_path):
    bdir = _make_backup(tmp_path)
    drill = tmp_path / "drill3"
    (drill / "postgres").mkdir(parents=True)
    (drill / "postgres" / "leftover").write_text("data", encoding="utf-8")
    r = run_bash("restore.sh", [str(bdir), "--data-root", str(drill)], _env(fake_docker))
    assert r.returncode != 0
    assert "非空" in (r.stderr + r.stdout)
    assert (drill / "postgres" / "leftover").read_text() == "data", "非空目标字节不变"


def test_ops03_restore_rejects_production_overlap(fake_docker, tmp_path):
    bdir = _make_backup(tmp_path)
    # 数据根指向生产根本身 → 必须拒绝
    r = run_bash("restore.sh", [str(bdir), "--data-root", str(fake_docker["prod_root"])],
                 _env(fake_docker))
    assert r.returncode != 0
    assert "生产" in (r.stderr + r.stdout) or "重叠" in (r.stderr + r.stdout)
    # 双确认缺失时 --allow-production 也不能单独放行
    r2 = run_bash("restore.sh", [str(bdir), "--data-root", str(fake_docker["prod_root"]),
                                 "--allow-production"], _env(fake_docker))
    assert r2.returncode != 0


def test_ops03_restore_rejects_missing_backup_env_vars(fake_docker, tmp_path):
    bdir = _make_backup(tmp_path, env_override=lambda t: t.replace(
        "BLYB_OBJECTS_DIR=/old/host/objects\n", ""))
    r = run_bash("restore.sh", [str(bdir), "--data-root", str(tmp_path / "drill4")],
                 _env(fake_docker))
    assert r.returncode != 0
    assert "BLYB_OBJECTS_DIR" in (r.stderr + r.stdout)


def test_ops03_restore_validate_only_writes_nothing(fake_docker, tmp_path):
    bdir = _make_backup(tmp_path)
    drill = tmp_path / "drill5"
    r = run_bash("restore.sh", [str(bdir), "--data-root", str(drill), "--validate-only"],
                 _env(fake_docker))
    assert r.returncode == 0, r.stderr
    assert not drill.exists(), "--validate-only 不得创建任何目录"


# ---------------------------------------------------------------- OPS-04
def _run_backup(fake_docker, tmp_path, *, with_objects=False, fail=False, no_stop=False):
    args = []
    if with_objects:
        args.append("--with-objects")
    if no_stop:
        args.append("--no-stop")
    env = _env(fake_docker)
    if fail:
        env["FAKE_PG_DUMP_FAIL"] = "1"
    # 服务"在运行"
    fake_docker["running_flag"].write_text("1", encoding="utf-8")
    env["FAKE_DOCKER_RUNNING"] = fake_docker["running_flag"]
    r = run_bash("backup.sh", args, env, cwd=str(tmp_path))
    backups = sorted((fake_docker["prod_root"] / "backups").iterdir())
    return r, backups[-1] if backups else None


def test_ops04_backup_still_window_before_snapshots(fake_docker, tmp_path):
    r, dest = _run_backup(fake_docker, tmp_path, with_objects=True)
    assert r.returncode == 0, r.stderr
    log = fake_docker["log"].read_text(encoding="utf-8")
    lines = log.splitlines()
    stop_idx = max(i for i, l in enumerate(lines) if " stop " in f" {l}")
    dump_idx = next(i for i, l in enumerate(lines) if "pg_dump" in l)
    assert stop_idx < dump_idx, "必须先静止（stop worker/core）再做任何快照"
    assert " start worker" in log and " start core" in log, "结束后必须恢复原运行服务"
    meta = json.loads((dest / "backup.json").read_text(encoding="utf-8"))
    # FIX2-06：全部实际写入口静止（storage 上传写 PG/对象、exchange 写交换库）
    assert meta["quiesce_mode"] == "worker+core+storage+exchange"
    assert " start storage" in log and " start exchange" in log,         "结束后必须恢复 storage/exchange（若原在运行）"
    assert meta.get("storage_was_running") == 1 and         meta.get("exchange_was_running") == 1
    assert meta["still_window"]["start"] and meta["still_window"]["end"]
    assert meta["with_objects"] == 1
    assert (dest / "objects.tar.gz").exists()
    assert (dest / "SHA256SUMS").exists()
    # exchange 快照确实来自静止窗口内的 backup API 模拟
    assert (dest / "exchange.sqlite3").read_text(encoding="utf-8") == "exchange-snapshot"


def test_ops04_backup_failure_restores_services(fake_docker, tmp_path):
    r, _dest = _run_backup(fake_docker, tmp_path, fail=True)
    assert r.returncode != 0
    log = fake_docker["log"].read_text(encoding="utf-8")
    assert " start worker" in log and " start core" in log, "失败 trap 必须恢复原运行服务"
    assert "pg_dump" in log


def test_ops04_backup_rejects_objects_with_no_stop(fake_docker, tmp_path):
    r, _ = _run_backup(fake_docker, tmp_path, with_objects=True, no_stop=True)
    assert r.returncode != 0
    assert "--no-stop" in (r.stdout + r.stderr)


def test_ops04_backup_fails_closed_on_missing_env_paths(fake_docker, tmp_path):
    env_text = fake_docker["prod_env"].read_text(encoding="utf-8")
    fake_docker["prod_env"].write_text(
        env_text.replace(next(l for l in env_text.splitlines()
                              if l.startswith("BLYB_EXCHANGE_DIR=")), ""),
        encoding="utf-8")
    r, _ = _run_backup(fake_docker, tmp_path)
    assert r.returncode != 0
    assert "BLYB_EXCHANGE_DIR" in (r.stdout + r.stderr)
