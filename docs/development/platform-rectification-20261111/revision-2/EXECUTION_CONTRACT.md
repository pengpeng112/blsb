# 四轮长任务统一执行与验收约定

适用 RT-03～RT-06。此文件解决范围、操作命令、证据和中断规则；业务细节读对应任务书。

## 1. 接手、基线与会话

1. 根目录 `F:\python\blyb`：读 AGENTS、START_HERE、STATE、HANDOFF、本目录 README 和当前任务书全文。
   涉及具体上报项目先读 `数据上报/README.md` 及业务 README。对照上轮真实交接，先列已完成与仍缺项。
2. 运行 `python scripts/ai_session.py status`。读取所有 running/checkpoint 任务的范围与最后记录。
   有重叠先协调，不能自行结束别人的会话；不因记录日期较旧就认定可覆盖。
3. `git status --short` 仅用于了解差异；当前已有大量历史未提交和未跟踪文件，**禁止 reset/clean/checkout
   回退整个工作区，也不能把本轮前已有改动列成本轮成果**。保留本轮将改文件的可恢复字节基线及哈希。
4. 从对应任务书范围展开实际路径，逐个 `--scope` 登记；不要把 `{a,b}` 或 `**` 当作登记工具自动展开的语法。
   新增范围先按工具现有能力扩展/重新登记，保持记录准确。真实工具填实际名称，模型未知填 unknown。

```powershell
Set-Location -LiteralPath 'F:\python\blyb'
python scripts/ai_session.py status
# 下例只说明参数，执行时用当前任务全部实际修改范围替换 PATH-1/PATH-2。
python scripts/ai_session.py start --task RT-03-20261114 --tool TOOL --model MODEL --scope PATH-1 --scope PATH-2
```

原任务标识沿用 RT-03-20261114、RT-04-20261115、RT-05-20261116、RT-06-20261117。
会话 ID 取 start 的真实返回，不手造；交接正文日期写真实执行日期。

## 2. 全程红线及其验证

- 不连接真实源库/前置机/第三方服务，不 SSH，不读取凭据内容，不发送真实数据，不执行生产部署或运行脚本。
- 不 pip/npm 安装，不下载浏览器、Docker 镜像或规范，不新增依赖；缺环境只阻断受影响验证。
- 本机测试允许临时 SQLite、`127.0.0.1`/`::1` 随机端口 Mock。禁止跟随重定向绕出回环；测试结束关闭线程/端口。
  启动前清理代理影响，在测试作用域内恢复环境；防网络护栏只能放行本次测试拥有的回环端口。
- 默认新增调度 `enabled=False`，镜像 `observe_only=True` 且默认停用，服务端执行拒绝保持。
  原 `file_default` 通道可用；smtp/webhook 启用与测试外发持续服务端拒绝。
- 租户与 owner 隔离、业务范围鉴权、0/1/N 候选、前导零、患者/就诊分离、质量等待、revision 固定、dry-run 零真实副作用不放松。
- 冻结包、已批准字典、历史 payload、已发布 ConfigRevision 不原地修改；新状态用独立表/新 revision。
  新迁移先查核心和插件迁移注册表最大编号，不把 021 当作永远可用的固定编号。

每轮开工与收尾生成 `相对路径 → SHA256` 清单，比较文件集合与字节：

```text
implementation/deploy/inf_pusher/**
implementation/deploy/inf_watchdog/**
implementation/plugins/destinations/alihealth_drugtrace/**
implementation/apps/admin_web/src/views/DrugTrace.vue
implementation/apps/admin_web/src/views/drugTrace.css
implementation/apps/admin_web/src/views/drugTraceDisplay.ts
implementation/docs/audit/2026-10-01/test_review_probes.py
docs/development/drug-trace-timed-report-20261024/**（存在时）
已上线迁移 001～020 的原文件及语义
本轮读取的原始规范、样例、来源扫描、批准和冻结材料
implementation/apps/admin_web/package.json 与现有 lockfile
```

扫描排除 `__pycache__`/临时缓存，缺路径要记“基线即不存在”，不造一个空清单当校验通过。
部署脚本均只读，包括既有 Compose；RT-02 的插件集已调整，本批不再次改它。

## 3. 每个子阶段的交付方法

按任务书完成“源码定位 → 可复现的反例 → 最小兼容修改 → 有意义的断言 → 文档与证据”。
已完成项先核验证据，只补真实缺口。发现任务书假设错误时，在交接写源码/规范依据及替代方案，
不盲照旧行号。接口改动先列 method/path/request/response/角色/scope/审计/错误码/所有调用方。

测试不能仅检查函数名、源文件包含字符串或 Mock 被调用；关键路径必须查实际 DB 行、响应、文件和实际请求命中。
不能删断言、标 xfail、批量 skip、把未确认改成成功或硬编码时间/统计数造绿。

阶段 checkpoint 至少记录：验收 ID、已修改文件、真实命令/退出码、未完成、下一条可执行命令、环境恢复事项。
上下文续做先读本轮交接/checkpoint，不从头重建。

## 4. 统一验证命令与工作目录

按局部 → 必要全量顺序。所有测试命令记录工作目录、开始结束时间、退出码、通过/失败/错误/跳过数。
日志放已有 `implementation/var/`，前端日志也可用绝对路径写那里；不把敏感报文或凭据写日志。

```powershell
# 后端全量与探针：两条命令都在 implementation 内执行。
Set-Location -LiteralPath 'F:\python\blyb\implementation'
python -m pytest tests -q -ra -p no:cacheprovider --junitxml=var/RT-03-20261114-regression.xml
$regressionExit = $LASTEXITCODE
python -m pytest docs/audit/2026-10-01/test_review_probes.py -q -ra -p no:cacheprovider
$probeExit = $LASTEXITCODE

# 前端必须跑 build，不用 build-fast 代替 vue-tsc。
Set-Location -LiteralPath 'F:\python\blyb\implementation\apps\admin_web'
npm run build
$buildExit = $LASTEXITCODE
$testFiles = @(Get-ChildItem -LiteralPath tests -Recurse -File -Filter '*.test.mjs' |
  Sort-Object FullName | Select-Object -ExpandProperty FullName)
if ($testFiles.Count -eq 0) { throw '未发现前端测试，不能验收' }
node --test $testFiles
$frontendExit = $LASTEXITCODE

# 文档与登记工具：回到工作区根。
Set-Location -LiteralPath 'F:\python\blyb'
python scripts/project_docs.py inventory
$inventoryExit = $LASTEXITCODE
python scripts/project_docs.py check
$docsExit = $LASTEXITCODE
```

每轮替换正确任务 ID，RT-06 最终全量使用 `var/RT-06-final-regression.xml`。
PowerShell 原生命令失败不一定中止后续脚本；必须逐条读取退出码，不能只报告最后一条命令为 0。
需要管道落日志时保留原生命令退出码；不要以 `Tee-Object`/`Out-File` 的状态覆盖 pytest/npm/node 状态。

验收标准：后端 0 failed/0 error；探针 43 passed 且无新增 skip；前端 build exit 0、全部测试通过且无新增掩盖性 skip。
既有环境 skip 逐条记录原因和影响；不宣称该能力已验证。核心本地场景不能以缺真实数据库为由跳过。
RT-05 即使前端为主，收尾仍做一次后端全量与探针，确认操作接口与本轮辅助字段没有破坏已有系统。
一轮无后续代码更改且证据有效，不机械反复执行长全量。

文档检查当前已有历史断链，不能要求本轮清理全仓原件。开工留 check JSON、当前 broken_links 集合、source_errors；
收尾运行原脚本并另做前后集合差异，要求**本轮新增断链 0、来源新增错误 0**。
若脚本因既有错误 exit 1，原样报告，不改白名单或历史基线压绿。

## 5. 文档与关闭会话

四类实施文档为：`implementation/docs/实施状态矩阵.md`、`系统架构与流程说明.md`、
`接口与设计差异.md`、`测试执行记录.md`；API 变化再同步 `04-API目录对照矩阵.md`。
按实际变化写对应章节，保留历史证据。业务 README 只补导航和本轮事实，状态仍统一 STATE/BACKLOG/HANDOFF。

每轮交接分别写 `docs/ai/sessions/RT-03-20261114.md` 等原约定文件，含验收 ID 对照表、命令与证据、
兼容变化、新迁移、禁改哈希、未测和外部阻断、下一轮前提。再执行：

```powershell
python scripts/ai_session.py end --session SESSION-ID --result completed --summary '实际完成摘要' --handoff docs/ai/sessions/RT-03-20261114.md
```

只有本轮本地必达项全部完成才 completed。未完使用 checkpoint 或 `--result handed_off`，
不得因额度、时长或用户期望“全部绿”而捏造结论。外部签名/真实七天观察等原定外部项可继续外部阻断，
但必须与本轮本地验收状态分栏，RT-06 不得用它们替代本地未实现项。
