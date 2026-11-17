# 给下一位 AI 的长任务提示词 · revision 2

每次复制对应完整代码块即可。任务书已细化实现语义、范围、反例、测试与收尾，不必把旧长提示词一起粘贴。
四轮顺序执行；默认每次只做一轮。需要跨上下文时继续同轮checkpoint，不能只做计划或临时停在“可以继续”。

## RT-03

```text
你在 Windows 工作区 F:\python\blyb。当前请实际执行 RT-03（任务ID RT-03-20261114），不要只给方案。

先读 AGENTS.md、docs/ai/START_HERE.md、STATE.md、HANDOFF.md，以及：
docs/development/platform-rectification-20261111/revision-2/README.md
docs/development/platform-rectification-20261111/revision-2/EXECUTION_CONTRACT.md
docs/development/platform-rectification-20261111/revision-2/SOURCE_REVIEW.md
docs/development/platform-rectification-20261111/revision-2/RT-03.md（全文）
原RT-03仅作历史依据，细节冲突以本revision及当前证据为准。

先核对 ai_session.py status；按RT-03第1节展开真实scope登记，工具用实际名称、模型未知填unknown。
保留本轮可恢复基线和禁改哈希，不回退已有大量未提交改动，不自动启动其他代理。
连续完成S01～S10：真实RUNNING/耗时/fencing与重试终态；RUN_ONCE/SKIP合并；时区和昨日真实窗口；
self告警独立恢复、全部入库脱敏、Webhook引用校验；四接口真实游标及所有前端调用者同步。
先可复现反例再修，新增≥30项有意义后端测试，并补前端分页/渲染断言；不要只用直接函数测试证明worker装配。
保持新调度默认停用、镜像observe_only不可执行、file可用、smtp/webhook服务端拒外发。

不连接真实库或第三方、不改生产运行脚本/DrugTrace三件套、不安装下载新依赖。
按EXECUTION_CONTRACT明确工作目录执行后端全量、43探针、前端build与全部测试、文档检查、禁改哈希；
逐条记录真实退出码，旧断链单列且本轮新增0，不篡改测试/白名单造绿。
更新四类实施文档、API矩阵、STATE/BACKLOG/HANDOFF和docs/ai/sessions/RT-03-20261114.md，
全部本地必达项完成才ai_session.py end --result completed，否则如实checkpoint/handed_off。
不要每个函数停下来问；在授权本地范围内持续做到可审查结果，结束报告已测、未测、外部阻断及下一轮契约。
```

## RT-04

```text
你在 Windows 工作区 F:\python\blyb。当前请实际执行 RT-04（任务ID RT-04-20261115），不要只给方案。
先读根AGENTS、docs/ai/START_HERE、STATE、HANDOFF和RT-03真实交接；读数据上报总索引、医保与传染病README。
全文阅读 docs/development/platform-rectification-20261111/revision-2/ 下的README.md、EXECUTION_CONTRACT.md、
SOURCE_REVIEW.md、RT-04.md。原RT-04仅作历史依据，细节冲突以本revision及当前证据为准。

先status确认无范围冲突，再按任务书第1节展开scope登记；实际工具/模型，不知道模型写unknown。
备份将改文件并建立禁改/原件哈希，不覆盖他人和历史未提交修改，不自动启动子代理。
按A0→B1隐私防线→A1～A5→B2～B6推进：先堵所有持久化字段递归隐私泄漏；完成真digest、稳定源版本幂等、
独立游标和已发状态、六SQLite视图真实SQL采集/断点/同时间戳分页/父子依赖→本机Mock完整链；
修导入行与记录计数、sent-only、外部详情、六类分类、七天合成对账CLI；
新revision生成真正23交易嵌套契约，输入输出分离，完整数组递归校验，公共报文头/回填/完整导出与零泄漏扫描。
不要用上传时间作源版本；不要推进水位丢失败记录；不要原地修改PUBLISHED配置/旧冻结Profile；
不要仅验数组前5项，不把未知规范warning删除凑零；所有落库错误文案/note/审计同样脱敏。

新增至少30项测试，按任务书建议规模≥45个后端（含23交易）及≥6前端；满足场景比数量更重要。
禁改 implementation/deploy/inf_pusher/**、inf_watchdog/**和原始规范/样例，不复制原样例敏感值，不出外部网络。
保持RT-03分页、RBAC/tenant/owner、Mock与真实证据区分，不安装下载。
按统一约定跑全量后端/43探针/build/全部前端测试/文档与哈希；真实七天/正式签名继续外部阻断。
更新业务README/必要过程档案与总索引、四类实施文档/API矩阵、STATE/BACKLOG/HANDOFF、
docs/ai/sessions/RT-04-20261115.md，再正确end；本地必达未完成只能checkpoint/handed_off。
持续自主实施，不按小步询问；报告新增迁移/Profile版本、真实命令、未测和下一轮接口前提。
```

## RT-05

```text
你在 Windows 工作区 F:\python\blyb。当前请实际执行 RT-05（任务ID RT-05-20261116），不要只给方案。
读AGENTS、docs/ai/START_HERE、STATE、HANDOFF、RT-03/04交接；全文读
docs/development/platform-rectification-20261111/revision-2/README.md、EXECUTION_CONTRACT.md、SOURCE_REVIEW.md、RT-05.md。
原RT-05仅作历史依据；当前细节以revision 2及源码/规范证据为准。

先status、备份与禁改哈希；按任务书登记实际scope/工具/模型，默认单AI，不自动启动子代理。
完成U01～U10：14个受影响页面逐页核对真实API、角色/scope/revision/reason/审计，
补业务创建编辑、调度创建编辑/真实Cron预览、规则创建/通道表单；升级Mapping/Pack/Workflows/Connectors，
再完成Cases/Quality/Review/Publications/Lineage/Metrics六页。
复用RT-01组件和API，不重建骨架。Mapping真实算子是null_fallback而不是coalesce，JSON互转不能丢配置。
特别处理Pack后端语义：现有rollback仅标记发布已撤销，不能拿它假装恢复到版本；按任务书补理由审计和真实restore闭环。
修所有workflow非法JSON静默空对象，包括执行context；CSV正确处理引号换行；SSR缓存防过期。
每项写操作要有错误保留、409不覆盖、禁重复、成功刷新、权限和真实请求断言，SSR不能代替点击行为验证。

至少新增25前端测试，现有87基线仅作参考，按实际开工数+新增数验收；14页SSR和关键交互/纯逻辑都覆盖。
禁止npm/pip安装、改package/lock、DrugTrace三件套、生产脚本、真实连接与外发。
按统一约定跑类型build、全部前端测试、后端全量/43探针、文档与禁改哈希；缺浏览器不下载，明示视觉/点击未测。
更新四类实施文档/API矩阵、STATE/BACKLOG/HANDOFF，写docs/ai/sessions/RT-05-20261116.md并end。
本地必达未完如实checkpoint/handed_off，不以“只差后端”或测试数达到旧96门槛提前completed。
在授权范围内持续完成，不按小步询问，结尾报告真实恢复语义、测试证据、剩余和RT-06前提。
```

## RT-06

```text
你在 Windows 工作区 F:\python\blyb。当前请实际执行 RT-06（任务ID RT-06-20261117），不要只给方案。
读AGENTS、docs/ai/START_HERE、STATE、HANDOFF和RT-03～05真实交接；读上报总索引/病理/影像README。
全文读 docs/development/platform-rectification-20261111/revision-2/README.md、EXECUTION_CONTRACT.md、
SOURCE_REVIEW.md、RT-06.md。原RT-06只作历史依据，不照抄固定79位的无依据假设。

先status确认冲突、保留基线/哈希，再按任务书登记实际scope/工具/模型；单AI顺序推进，不自动子代理。
完成F01～F07：临时工作区脚手架连续A/B创建+重放零修改/路径穿越/失败原子性；
影像16数据集保留CSV实际约束/未批准标记，新增revision及16合成样例，真实domain.routes与投影→本地发布或Mock→summary/ledger/详情；
病理新增纯编码辅助，8段原值/前导零保留，紧凑码需显式段宽schema或报歧义，不猜示例段宽，不改既有门禁；
全新临时库migrate+bootstrap+FastAPI+Worker分业务链冒烟，受限/跨租户/真实SQL影子/医保23/影像16/病理/调度重试/告警早报/file/ack/CSV；
用实际前端API客户端对本地HTTP测试解包与分页；全量TS/Vue直接请求按core/storage等服务交叉匹配路由method/path，缺失0、未解析0。
TestClient、回环HTTP、SSR和浏览器验证分开报告，不能静态路由存在就承诺业务0个404。

严禁真实外部库/第三方/SSH/生产部署、生产运行脚本/DrugTrace改动、下载安装。所有端口随机且只用本机合成数据，退出清理本轮资源。
最终在implementation执行pytest全量输出var/RT-06-final-regression.xml，必须0failed/0error；43探针、前端build/全部测试、
文档inventory/check、本轮新增断链0、来源/禁改哈希一致。逐条留退出码与skip，不造绿。
生成docs/development/platform-rectification-20261111/FINAL_ACCEPTANCE_REPORT.md并更新既有DEPLOY_RUNBOOK.md，
汇总验收ID与证据、外部阻断；更新业务README/总索引、四类实施文档/API矩阵、STATE/BACKLOG/HANDOFF、
docs/ai/sessions/RT-06-20261117.md，全部本地必达完成才end --result completed；否则准确handed_off。
持续自主做到可验收，不部署；报告本地完成程度和真实上线前仍需什么，不用Mock冒充真实确认。
```
