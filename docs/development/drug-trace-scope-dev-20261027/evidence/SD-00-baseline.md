# SD-00 基线（DTR-SCOPE-DEV-20261027）

- 登记：`python scripts/ai_session.py start --task DTR-SCOPE-DEV-20261027 --tool ZCode
  --model GLM-5.3 --scope ...`，session `20261027T134424Z-45575e89`；
  登记前 `ai_session.py status` 全量解析：86 会话（45 completed / 40 handed_off / 1 cancelled），
  **无 active/checkpoint 冲突**。
- 全量回归基线（改动前实测，2026-10-27 21:44–21:55 本机）：
  `cd implementation && python -m pytest tests -q -ra --junitxml=var/sd00-baseline.xml`
  → **595 passed / 7 skipped / 0 failed，实际退出码 0**（651.59s）。
  7 skipped 与既有记录一致（live 栈 / Windows fd / B01·B05 真实 DSN×2 / pyodbc / 50GB 窗口）。
  JUnit XML：`implementation/var/sd00-baseline.xml`。
- 前端基线：`cd implementation/apps/admin_web && npm run build` → **exit 0**
  （vite build，输出与既有构建一致）。
- 与执行书 §七基线一致：595/7/0。
