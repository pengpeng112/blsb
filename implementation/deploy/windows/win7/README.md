# Win7 构建说明（BLYB-EdgeAgent）

## 约束（为什么单独一套）

- Python **3.9 起官方不再支持 Windows 7**；本项目当前开发/测试环境为 Python 3.14，
  产物只能覆盖 **Win10/11**（已在 Win10 19045 实测）。
- Agent 代码已按 3.8 兼容编写（无 3.9+ API；类型注解走 `from __future__ import annotations`），
  依赖仅 httpx 及其传递依赖 + 标准库 sqlite3——3.8 可装。
- Win7 还需 **SP1 + TLS 1.2 补丁**（连接院内 HTTPS 服务时）。

## 构建步骤（在 Win7 SP1 或 Win7 兼容的构建机上）

1. 安装 Python 3.8.10（win32/x64 按目标机），确认 `python -V`。
2. 在本目录执行：
   ```bat
   python -m venv venv38 && venv38\Scripts\activate
   pip install --no-index --find-links ..\..\offline\wheels -r requirements-agent-win7.txt
   :: 或有网环境：pip install -r requirements-agent-win7.txt
   cd /d ..\..\..   （implementation 仓库根）
   python -m PyInstaller --noconfirm --clean --console --onefile ^
     --name BLYB-EdgeAgent-Win7 --paths . ^
     --distpath deploy\windows\dist --workpath build\pyi38 --specpath build\pyi38 ^
     deploy\windows\agent_entry.py
   ```
3. 产物 `deploy\windows\dist\BLYB-EdgeAgent-Win7.exe`，用法与 Win10 版一致
   （exe 同目录 agent-config.json / scanner-output / agent-queue.sqlite3）。

## 状态

**未执行**：本项目环境无 Python 3.8 与 Win7 机器，本说明与依赖清单为备齐材料，
实际构建与 Win7 现场运行属阶段F验证项（如实记录于 docs/实施状态矩阵.md）。
