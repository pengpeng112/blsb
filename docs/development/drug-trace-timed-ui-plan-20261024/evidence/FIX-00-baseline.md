# FIX-00 基线记录（DTR-TIMED-UI-FIX-20261024）

- 时间：2026-10-24（session 20261024T094555Z-17f92c5b，ZCode/GLM-5.3）。
- 登记：`python scripts/ai_session.py status` 无进行中会话；
  `start --task DTR-TIMED-UI-FIX-20261024 --tool ZCode --model GLM-5.3`（6 scope）成功。
- 局部测试：`python -m pytest tests/integration/test_drug_trace_timed_ui.py
  tests/integration/test_drug_trace_api.py -q` →
  **12 passed / 2 skipped / 0 failed（7.56s）**。
  2 skipped＝timed_ui 中两个 Docker PG 测试（当时 docker daemon 未运行；
  FIX-07 新增 PG 端到端测试前已启动 Docker Desktop 复验，见 FIX-09 记录）。
- 前端：`cd implementation/apps/admin_web && npm run build` →
  **成功（2.02s，dist js 171.94 kB / css 3.61 kB）**，与上轮 RESULT 记录一致。
- 禁改清单基线：30 个冻结文件 SHA256 前 16 位存
  `evidence/FIX-frozen-baseline.json`（F-A07 收尾比对）。
- 与 FIX_TASK §七基线对照：局部 9+5 项全部通过（含 skip 项为环境性 skip，
  非断言失败）；npm build ✓。基线成立。
