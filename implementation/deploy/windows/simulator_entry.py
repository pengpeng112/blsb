"""PyInstaller 入口：BLYB-ScanSimulator.exe（扫描输出模拟器，测试用）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from apps.edge_agent.scanner_simulator import main  # noqa: E402

if __name__ == "__main__":
    main()
