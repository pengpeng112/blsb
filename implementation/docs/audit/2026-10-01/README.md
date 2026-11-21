# 2026-10-01 独立复核证据

> 本目录保存各轮历史证据。最初43项失败后来已修复，最近记录为43项通过；当前状态见 [STATE](../../../../docs/ai/STATE.md)。
> 下文“未整改/失败”按原复核轮次解释，不作为当前未修复结论。

主报告：[项目独立复核与详细整改计划](../../../../归档/2026-11-21/历史阶段任务与复核/项目独立复核与详细整改计划_2026-10-01.md)。

本目录用于保存复核材料，业务实现未在本轮整改。`test_review_probes.py` 的断言表达 v0.4 设计要求，当前失败就是偏差证据。它放在 `docs/audit/`，不会混入既有 `pytest tests` 的常规测试收集。修复时应把适用检查迁入正式回归，不要通过 xfail、删除断言或改成错误行为来制造通过。

## 复跑结果

执行目录均为 `implementation/`，前端命令除外。

| 检查 | 命令 | 结果 | 证据（历史 `var/` 产物路径） |
|---|---|---|---|
| 既有回归 | `python -m pytest tests -q -ra --junitxml=var/audit-20261001-baseline.xml` | exit 0；81 passed、4 skipped、936 warnings；192.61s | `var/audit-20261001-baseline.log`、`var/audit-20261001-baseline.xml` |
| 前端构建 | 在 `apps/admin_web/` 执行 `npm run build` | exit 0；vue-tsc、Vite 成功 | `var/audit-20261001-frontend-build.log` |
| 独立要求检查 | `python -m pytest docs/audit/2026-10-01/test_review_probes.py -q -ra --tb=short --junitxml=var/audit-20261001-review-all.xml` | exit 1；43 failed、103 warnings；91.71s | `var/audit-20261001-review-all.log`、`var/audit-20261001-review-all.xml` |
| **整改第2轮后复跑（2026-10-01）** | 同命令 | **43 passed、0 failed**（已迁入正式回归 `tests/integration/test_remediation_round2*.py` 并扩大；正式回归 137 passed/4 skipped） | `var/remediation-round2-probes.log`、`var/remediation-round2-probes.xml`、`var/remediation-round2-regression.xml` |

统一复跑前还有 28/10/5 项的分段日志，保留在 `var/audit-20261001-probes*`。以 `review-all` 为最终统一证据，不能把分段与全量重复相加。

## 隔离与解释

- 临时数据库、临时卷、合成患者与租户；没有连接真实 HIS/PIS/医保，没有发送真实数据。
- R01—R43 是 43 个检查点，同一根因可能对应多个失败；不能解释为 43 个独立架构问题。
- R07 是两个不同 `PYTHONHASHSEED` 的子进程；R20 是有终止期限的正则子进程；R39 是两个独立数据库会话；R40 是 rename 前独立会话读取提交意图；R43 是捕获启动装配的线程创建，不启动真实监听服务。
- R40 证明意图没有先提交，不代表已执行所有崩溃点的 kill/重启验收。浏览器预检及资源路由测试也不替代真实浏览器端到端验收。
- 部分测试断言当前内部接口；如果整改采用等价、合规的接口，应保留对应业务不变量并重写测试。例如同源网关部署后，用真实浏览器证明跨服务访问成功，替代对原直连架构的 OPTIONS 断言。
- `var` 日志、构建产物属于验证输出。源文件哈希清单只用于标记本次复核快照，不是完整 SBOM，也不证明生产发行可用。

## 快照

`make_manifest.py` 仅读取允许的代码/配置/设计文档与测试输出，记录相对路径、大小、SHA-256、环境及 JUnit 汇总。不读取业务数据库、凭据文件、node_modules、EXE 内容或环境变量中的密钥。

```powershell
python docs/audit/2026-10-01/make_manifest.py
```

输出：`var/audit-20261001-manifest.json`。运行日期不代表原历史报告的生成日期，复核结论应同时结合主报告中的证据边界。
