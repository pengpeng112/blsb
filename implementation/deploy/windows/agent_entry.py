"""PyInstaller 入口：BLYB-EdgeAgent.exe（含运行环境，免安装）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from apps.edge_agent.run import main  # noqa: E402

if __name__ == "__main__":
    main()
