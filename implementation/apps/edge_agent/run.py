"""Agent 命令行入口（生产由 Windows 服务包装，见 deploy/windows）。

开发：python -m apps.edge_agent.run [--once]
打包后（PyInstaller onefile）：exe 同目录放 agent-config.json 与本地队列，
首次运行用注册令牌自动注册并写回 agent_id/token。
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def base_dir() -> str:
    """冻结(exe)模式：数据放 exe 同目录；开发模式：仓库 var/ 目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def default_config_path() -> str:
    b = base_dir()
    if getattr(sys, "frozen", False):
        return os.path.join(b, "agent-config.json")
    return os.path.join(b, "var", "agent-config.json")


def main():
    try:
        import _bootstrap  # noqa: F401  （冻结包内为无害的空操作）
    except ImportError:
        pass
    from .service import AgentService
    b = base_dir()
    default_cfg = default_config_path()
    ap = argparse.ArgumentParser(description="blyb 采集端 Agent")
    ap.add_argument("--config", default=default_cfg)
    ap.add_argument("--once", action="store_true", help="单轮执行（测试/演示）")
    ap.add_argument("--device-code", default=None,
                    help="首次注册用设备编号（多工作站共用一份程序时区分身份）")
    args = ap.parse_args()
    if not os.path.exists(args.config):
        os.makedirs(os.path.dirname(args.config) or ".", exist_ok=True)
        frozen = bool(getattr(sys, "frozen", False))
        with open(args.config, "w", encoding="utf-8") as f:
            json.dump({
                "watch_root": os.path.join(b, "scanner-output"),
                "core_url": "http://127.0.0.1:8000",
                "storage_url": "http://127.0.0.1:8001",
                "token": "dev-agent-token",
                "agent_id": None,
                "chunk_size": 4 * 1024 * 1024,
                "local_db_path": os.path.join(
                    b, "agent-queue.sqlite3" if frozen else
                    os.path.join("var", "agent-queue.sqlite3")),
            }, f, ensure_ascii=False, indent=1)
        print(f"default config written: {args.config}")
    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if args.device_code:
        cfg["device_code"] = args.device_code
    agent_id = cfg.get("agent_id")
    if not agent_id:
        import httpx
        resp = httpx.post(f"{cfg['core_url']}/api/v1/agents/enroll", json={
            "enroll_token": os.environ.get("BLYB_ENROLL_TOKEN", "dev-enroll-token"),
            "device_code": cfg.get("device_code", "SCAN-WS-01"),
            "source_system_code": "scanner-simulator"}, timeout=15)
        resp.raise_for_status()
        data = resp.json()["data"]
        agent_id = data["agent_id"]
        cfg["agent_id"] = agent_id
        cfg["token"] = data["token"]
        with open(args.config, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        print(f"enrolled agent {agent_id}")
    svc = AgentService(
        watch_root=cfg["watch_root"], core_url=cfg["core_url"],
        storage_url=cfg["storage_url"], token=cfg["token"], agent_id=agent_id,
        local_db_path=cfg.get("local_db_path") or os.path.join(
            base_dir(), "agent-queue.sqlite3"),
        chunk_size=cfg.get("chunk_size", 4 * 1024 * 1024))
    if args.once:
        print(json.dumps(svc.run_once(), ensure_ascii=False, default=str))
        return
    print("agent running; ctrl+c to stop")
    svc.run_forever()


if __name__ == "__main__":
    main()
