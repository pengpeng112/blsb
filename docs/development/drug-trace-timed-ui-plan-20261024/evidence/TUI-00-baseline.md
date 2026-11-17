# TUI-00 基线（2026-10-24，ZCode/GLM-5.3）

- 登记：ai_session.py start --task DTR-TIMED-UI-20261024，session 20261024T052935Z-3c27a4bb；
  status 无活动会话（56 条历史全部 completed/handed_off/cancelled，无范围冲突）。
- 后端基线：`python -m pytest tests/integration/test_drug_trace_api.py -q`
  → **5 passed**（4.02s，Windows 本机 Python 3.14，350 条 DeprecationWarning 均为既有 sqlite 适配器警告）。
- 前端基线：`cd implementation/apps/admin_web && npm run build`
  → **built in 2.08s**（dist/index.html 0.40kB、css 3.41kB、js 161.36kB，exit 0）。
- Git：起点 main 分支工作区（含上一轮文档修改未提交，与本任务文件不重叠）。
