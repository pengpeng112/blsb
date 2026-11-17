"""FB-00 只读基线快照（DTR-FIRSTBILL-EXEC-20261021）。

复用 drug-trace-push-review-20261021/probe_readiness.py 的连接与采集实现，
写入**本任务** evidence/（排他创建，不覆盖任何历史文件）。
不打印密钥/患者数据；远端命令只读（READ ONLY 事务/查看类命令）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = ROOT / "docs/development/drug-trace-push-review-20261021/probe_readiness.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def capture(target: str) -> dict:
    mod = load(f"fb_probe_{target}", SOURCE)
    receipt = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "remote_config_changes": 0,
        "his_queries": 0,
        "upload_requests": 0,
    }
    try:
        receipt.update({
            "target": mod.run_target,
            "dmz": mod.run_dmz,
            "followup": mod.run_followup,
        }[target]())
        receipt["status"] = "CAPTURED" if receipt["ssh_exit_code"] == 0 else "REMOTE_ERROR"
    except Exception as exc:  # noqa: BLE001
        receipt.update(status="CHECK_FAILED",
                       error_type=type(exc).__name__,
                       error=str(exc)[:300])
    return receipt


def main() -> int:
    names = sys.argv[1:] or ["target", "dmz", "followup"]
    out_dir = HERE / "evidence"
    out_dir.mkdir(exist_ok=True)
    rc = 0
    for name in names:
        path = out_dir / f"baseline-{name}.json"
        if path.exists():
            print(f"[skip] {path.name} 已存在（排他创建，不覆盖）", file=sys.stderr)
            rc = 1
            continue
        receipt = capture(name)
        path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"[ok] {path.name} status={receipt['status']}")
        if receipt["status"] != "CAPTURED":
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
