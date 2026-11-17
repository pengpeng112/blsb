# implementation 工作目录入口

本项目当前没有Git根；某些工具从子目录启动时只发现本文件。
请明确读取 [根目录共同约定](../AGENTS.md) 和 [共享接手入口](../docs/ai/START_HERE.md)。

这里是唯一业务源码根；不搬迁apps/packages/plugins，不改导入布局来整理文档。
运行现有测试和启动命令时工作目录为本目录；当前状态见 [STATE](../docs/ai/STATE.md)。
记录任务前切到根目录调用 `scripts/ai_session.py`，或用其绝对路径。
