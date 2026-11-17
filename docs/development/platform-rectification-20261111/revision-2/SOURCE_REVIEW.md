# revision 2 编制依据与源码复核记录

日期：2026-11-12；任务 `RT-PLAN-R2-20261112`；Codex / unknown。
只读审查业务源码与材料，修改仅计划/导航/共享交接。没有启动 RT-03～RT-06 业务实施。
本文是带日期的计划复核依据；实时任务状态仍在共享 STATE/BACKLOG/HANDOFF。

## 1. 已完成成果与本轮证据

| 内容 | 证据及解释 |
|---|---|
| 接手与冲突 | 已读 AGENTS/START_HERE/STATE/HANDOFF/BACKLOG、RT总览和四份旧任务书；登记前status无进行中会话 |
| 历史RT-01 | 已有交接；共享组件/路由/时间/下载等应复用，本轮未重做 |
| 历史RT-02 | [交接](../../../ai/sessions/RT-02-20261113.md)及 `implementation/var/RT-02-20261113-regression.xml`；XML实有966 tests、0 failures、0 errors、8 skipped，即958通过；此为历史证据 |
| 本轮相关后端 | 11份现有测试文件实跑：100 passed、1 skipped、1 warning，退出0，87.14s；没有声称新增测试或全量958本轮重跑 |
| 本轮独立探针 | 在implementation运行：43 passed，退出0，71.86s |
| 本轮前端 | 枚举5个 `*.test.mjs`：87 passed/0 failed/0 skipped，退出0；build含vue-tsc，退出0 |
| 医保文件计数 | transactions精确19项；数值名fields19、samples19；12601～12604缺失，与旧EXTRACTION_REPORT宣称23不一致 |
| 影像CSV | 标准csv读取288字段、16数据集；288行有type、285行有length、214行有requirement |
| 文档检查开工 | `project_docs.py check`退出1；new_broken_links=1271、known legacy=0、来源/归档哈希错0；另留本轮前集合，不压绿 |

任务后缀RT-02-20261113是既有记录名；XML时间显示11-12，不能因文件名先后推断今天为11-14。
本次用环境日期11-12记复核，不回写历史日期或冒充未来执行完成。

## 2. RT-03定位

路径均相对工作区根，行号是本轮读取时定位，后续以符号为准。

| 源码定位 | 当前事实 | 新任务要求 |
|---|---|---|
| `implementation/apps/worker/engine.py:150` | claim后进入handler，缺调度run开始钩子 | handler之前提交RUNNING，独立Session可见 |
| `implementation/packages/workflow_core/jobs.py:157` finish_job | 返回bool是epoch条件更新成功，RETRY_WAIT也True | 区分状态更新与终态，兼顾Operation |
| `implementation/plugins/scheduling/scheduler.py:102` | next_after(fire_utc)只推进一班 | 一次跳now之后，最近槽位补一次 |
| 同文件 `_record_run` / `finalize_run` | deadline基于历史fire；started缺失补成完成时刻 | 入队与首次执行预算，历史未知不伪造 |
| `implementation/plugins/scheduling/run_guard.py:27` | QUEUED也按deadline检测；只查询QUEUED/RUNNING | 明确未开跑不按执行超时，保留异常/迟到史 |
| `implementation/plugins/alerting/digest.py:22,43,63` | naive直接astimezone；滚动24h；naive传cron | 正确aware、自然日真实窗口、错误分类 |
| `implementation/plugins/alerting/evaluator.py:94,156` | f.detail直存；统一alerting.self | 入库脱敏、规则级self及独立恢复条件 |
| `implementation/plugins/alerting/redact.py` | 现名redact_detail，仅顶层字符串 | 明确sanitize兼容调用与递归边界 |
| `implementation/plugins/alerting/channels.py` | url_env不存在时内联url不被拒；发送入口按type派发 | 配置允许字段+变量名校验+外发服务端防线 |
| `implementation/apps/core_api/ops_routes.py:169,204,468` | runs/alerts2无真实cursor；notification-logs连cursor形参都缺 | 四个接口统一真实keyset与兼容迁移 |
| `implementation/plugins/domains/medins_emr/routes.py` list_exchanges | cursor未用且返回数组 | 迁移分页对象并同步medins.ts/历史表 |
| `implementation/plugins/report_ledger.py` decode_cursor | 某些坏游标异常直接返回None | 新分页不能继承静默首页行为 |

## 3. RT-04定位

| 源码定位 | 当前事实 | 新任务要求 |
|---|---|---|
| `implementation/plugins/domains/infectious/send.py:74` | PackageRevision未传digest，幂等键取空digest前缀 | 真快照digest与稳定源版本幂等分离 |
| 同文件send_batch | 每批新DataPackage、批内revision序列，sent_state未可靠持久化 | 明确版本模型与重启保护，不能只断言revision=2 |
| `implementation/plugins/domains/infectious/plugin.py:14,36,42` | PUBLISHED配置作运行状态，内存rows注入，无SQL collect.py | 独立状态表、真实SQL主路径、失败不越水位 |
| `implementation/plugins/domains/infectious/hooks.py` | 只有summary/search，无详情；按last_attempt_at分页 | 详情归属、sent-only未知时间可分页、六类统计 |
| `implementation/plugins/external_ledger/importers/inf_pusher.py` | SHAPE_INVALID漏rows_read；records循环只遍receipts键 | 修读行计数与sent-only，分清行/记录粒度 |
| `implementation/tools/spec_extract/build_profile.py:53,208` | 日期型被日期判断截胡；只遍19清单交易；样例顶层_synthetic | 类型/交易集/干净样例/来源结构修复 |
| 同文件 `_flatten_params` 与shape处理 | 所有表拍平，代码标识列直接作dict名，shape补路径未建真实树 | 输入输出分离、嵌套归属、Y不是字典名 |
| `implementation/tools/spec_extract/sample_shape.py` | shape只取前两个数组元素 | 合并完整结构覆盖并保留空数组语义 |
| `implementation/packages/mapping_engine/contract_validator.py:68,92` | 仅spec/None节点；递归复用同层nodes；数组只前5项 | 真树schema、全数组、spec_csv规则实际生效 |
| 同文件 `_check_scalar` | bool可能通过number；FORMAT/DICT文案含原值 | 严格类型和所有错误持久化边界无敏感值 |
| `implementation/plugins/domains/medins_emr/routes.py:35` | 仅input直下一层mask；envelope契约未传；note拼人工输入 | 任意深度、头+体、错误/回填/审计完整安全副本 |
| `implementation/apps/admin_web/src/views/target/MedinsDebug.vue` | 历史行无回填；导出下载待完整性修复 | 同步RT03分页、历史回填与完整执行包 |

原生外部导入行账按来源实例区分，但业务record聚合键是否跨实例碰撞需RT-04以反例查验。
这属于待实施验证，不在本次读代码后直接宣称已证明多实例正确。

## 4. RT-05/06定位与旧计划纠正

| 源码/材料 | 当前事实与执行影响 |
|---|---|
| `implementation/apps/admin_web/src/api/reportCenter.ts` | createTarget/updateTarget已存在，补页面调用即可复用 |
| `implementation/packages/mapping_engine/__init__.py:10` | 8种实际算子含null_fallback，不含coalesce；表单必须遵循真实DSL |
| `implementation/apps/admin_web/src/views/Workflows.vue:177` | 执行context非法JSON空catch后继续POST；不能只检查节点parameters |
| `implementation/apps/core_api/app.py:1025` | rollback只设置rolled_back_at、未接reason、未实现恢复旧版本，须扩展准确写语义 |
| `implementation/apps/admin_web/tests/sfc_loader.mjs` | SSR不点击、不执行mounted；入口内容缓存可能不随依赖变化失效，需避免旧产物 |
| `scripts/new_report_target.py:34,135` | 目录校验依平台分隔符；全局标记导致第二新业务跳过；全文plugin匹配；readme路径忽略biz-dir |
| `implementation/tools/spec_extract/csv_profile.py` | 只用部分列，type硬string/max_len空/required全False；CSV实际已有相应来源列 |
| `implementation/plugins/domains/medins_imaging/plugin.py` / hooks | 仅summary占位，无domain.routes/ledger；CSV清单没有asset_info官方数据集 |
| `implementation/plugins/domains/pathology/index_codec.py` | 内部原值/正式草案门禁已有实现，不能替换为全局固定79位规则 |
| 根目录 `93ebd53751dc40669f4c977781904f8b.txt` | 原文只固定部分段宽，写“常见为79位”；可变段紧凑码需schema才能无歧义拆分 |
| `implementation/apps/admin_web/src/api.ts` 与旧views | 存在直接coreGet/corePost、storage调用；只扫api目录会漏接口且错服务 |

## 5. 本轮真实验证命令

在 `implementation/`：

```powershell
python -m pytest tests/unit/test_scheduler.py tests/unit/test_run_guard.py tests/unit/test_alerting.py tests/unit/test_contract_validator.py tests/unit/test_spec_extract.py tests/unit/test_external_ledger.py tests/unit/test_new_report_target_script.py tests/unit/test_medins_imaging_skeleton.py tests/integration/test_inf_shadow.py tests/integration/test_medins_debug.py tests/integration/test_ops_api.py -q -ra -p no:cacheprovider
python -m pytest docs/audit/2026-10-01/test_review_probes.py -q -ra -p no:cacheprovider
```

唯一局部skip：`test_ops_api.py:322`，BLYB_PG_URL未配置，隔离PG双连接并发未执行。
现有测试通过说明基线可运行，不证明本文列出的新反例已覆盖。

在 `implementation/apps/admin_web/`：`npm run build`；以Get-ChildItem递归枚举测试文件后 `node --test $testFiles`。
本轮未安装依赖，build仅生成本地dist，未发布。未运行后端全量（约20分钟的历史全量不能冒充本轮）。
文档检查与保护哈希的收尾结果见 [本轮交接](../../../ai/sessions/RT-PLAN-R2-20261112.md)。
