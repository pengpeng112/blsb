• 我先读取待审方案和两份待审文档。

• 我已读完方案与两份原文。现在做核验：先确认嵌入文本是否逐字、行数声明是否准确，再对照仓库验证文档中的事实性声明。

• 最后几项快速核验：`.dockerignore` 是否存在、仓库 Git 状态、wheelhouse 命名。

• 核查完成。以下为我的独立结论（署名：Kimi Code CLI / kimi，只读核查，未修改任何文件）。

  ---

  # 独立核查结论：review/round-1/方案.md

  ## 一、总评

  方案作为 multi-review round-1 的**审查分包**（非审查结论本身），对用户需求「对两份文档进行核查」的**框架覆盖是忠实的**：需求原文逐字内嵌、两份待审文档逐字内嵌（已用 diff 实证）、对象来源与核查基准交代清楚、无实质性加戏。但它**本身不含任何核查结论**——实际结论落在同目录的 `审查_grok.raw`、`审查_kimi.raw`、`审查_zcode.md`、`检查_codex.md` 等文件。此外发现方案两处行数声明错误，以及两份待审文档若干真实问题（详见下）。

  ## 二、① 覆盖度与忠实度（对方案）

  **1. 需求原文内嵌：忠实。** 方案 §0 引述的 User arguments 与你给我的原话逐字一致；§0 附注（只读、缺席如实披露、高危人工终审、不复用历史授权）与根 AGENTS.md 精神一致，属 skill 规则重述，非私自加码。

  **2. 待审文档内嵌：内容逐字，行数声明有误（轻微事实错误）。**
  - 我用进程替换做了全文 diff：对象B（view-contract.md）**完全一致**；对象A（execution-plan.md）仅多出一个尾随空行（diff 结果 `264d263`，即嵌入体第264行为空行）。
  - 但方案 §2 标称「execution-plan.md（**264行**，逐字）」，实际原文 **263行**；§3 标称「view-contract.md（**135行**，逐字）」，实际 **134行**。两处均差 1，原因是把 `</details>` 前的空行计入了。风险很低，但这是一份以"逐字嵌入"为核心卖点的文档，计数错误会削弱其可审计性——核对者若按行数校验会误判嵌入了额外内容。

  **3. 对象来源声明：属实。** 方案 §1 称两文档由 Codex 会话（任务 REPORTING-DEPLOY-20261015）产出——`python scripts/ai_session.py status` 实测存在该任务（tool: Codex, status: completed），且同目录确有 README/connection-report/NEXT_AI_PROMPT/SESSION 等文件，与 §1 描述一致。

  **4. 结构性说明（非缺陷，但须明示）：** 方案 §4 自述"核查提示（不构成限制）"，全文无任何一条核查发现。若把方案当作对「进行核查」需求的**交付物**，则实质覆盖为零；若按 multi-review 分工理解为**分发给各审查者的输入包**，则覆盖完整。同目录已存在多份审查产出文件，支持后一种解读。结论：方案忠实于"round-1 分包"角色，但任何人只读方案.md 得不到核查结论。

  **5. 未发现私自加戏。** §1 的核查基准（仓库现状+v0.4设计+自洽性+用户意图）是「核查」的合理操作化，均在需求语义内。

  ## 三、② 自发发现的问题（对两份待审文档，均经仓库实证）

  ### 高/中风险

  **F1. execution-plan §5 缺口清单遗漏"端口直发"缺口——与 §4.1 自相矛盾的现行 compose 未被列为整改项。**
  现行 `implementation/deploy/compose/docker-compose.yml:31,44,54` 将 core `8000:8000`、storage `8001:8001`、exchange `8002:8002` **直接映射到宿主机全部网卡**；而计划 §4.1 目标拓扑明确"core/storage 容器内部端口、不映射宿主机、经 nginx 同源进入"。§5 的"执行前必须修订的现有部署缺口"表共 11 行，**没有一行**要求移除这三个 `ports:` 映射，也没有"新增 nginx 服务"这一工作项（nginx 只出现在 §4.1 的目标形态里）。风险：下一 AI 严格按 §5 清单整改后，发行的 compose 仍然绕过 nginx 把 core/storage/exchange 以 HTTP 明文暴露在医院局域网——恰恰是 §4.1 和 P3"只对管理网开放确认过的入口端口"要防的事。这是计划内部一致性缺陷。

  **F2. execution-plan §5 对 Dockerfile 风险表述偏轻：不是"风险"，是现存事实。**
  §5 称"Dockerfile COPY packages 会带入运行数据库的**风险**"。实测 `implementation/packages/var/` 下**现在就存在** `blyb.sqlite3`、`blyb.sqlite3-shm`、`blyb.sqlite3-wal` 三个运行时数据库文件，而 Dockerfile 第20行 `COPY packages/ /app/packages/` 会把它们烤进镜像；且 `implementation/` 下**没有 `.dockerignore`**（`ls -a` 实证）。即今天执行构建就会把一份真实运行库打进发行镜像。计划建议改为"现存数据泄露，须在构建前排除"，而非"风险"。

  **F3. Dockerfile 中 `BLYB_FACTORY=1` 是死配置，计划未点破。**
  `implementation/Dockerfile:8` 设置 `ENV BLYB_FACTORY=1`，但全仓 grep 该变量**仅出现在 Dockerfile 自身**，无任何代码读取。计划 §5 正确指出了"get_app 是工厂，Compose 未显式 --factory"（实证：`apps/core_api/app.py:2193 app = None` + `:2196 def get_app()`，compose 第26/38/47行均未加 `--factory`，uvicorn 会把 `get_app` 当 ASGI 可调用对象直接以 `(scope, receive, send)` 调用而 TypeError），但未指出这个 env 会给读者造成"工厂模式已处理"的假安全感。整改时应删除或实装该变量。

  ### 低/中风险

  **F4. execution-plan §7 P3 示例命令存在就绪竞态。**
  示例先 `up -d db` 再 `run --rm migrate`。`docker compose run` **不等待** depends_on 的 `service_healthy` 条件，db 容器刚启动未过 `pg_isready` 健康检查时 migrate 即可能失败。计划自身 §5 又要求"失败迁移禁止拉起"。示例应加显式等待（如 `up db --wait` 或循环探测），或注明"仅示意"。单独看是小事，但这是将被下一 AI 照抄的段落。

  **F5. 验收矩阵缺"对账演练"独立项。**
  view-contract §6 用整节定义对账口径（区间→数量→单据号→码集合，"数量一致不能证明单据和码正确"），execution-plan §8 的 A01–C01 矩阵中却没有对应的验收编号（B01–B05 只覆盖增量、单据完整、码校验、状态、隔离）。worker 职责（§4.1）和 P6 提到对账，但无通过标准。两文档间的一处覆盖断点。

  **F6. view-contract 对外部 API 事实的陈述置信度不一致。**
  §5 以确定语气陈述"result_type=3 表示处理成功；sub_process_flag=51为全部成功，52为部分成功"，§4 以确定语气给出"列表长度上限为3500"。这些在仓库内**无法验证**（本地 `数据上报/1、码上放心平台/` 仅有语雀链接和需求确认单 docx，无 API 字段规范原文；离线环境不能核官方文档）。同一份文档对 321/323、yljg/lsyd 混用明确标了"待确认"，对这几项却未标。虽有 §7"实施时重核版本"兜底，仍建议对这些数值同样加"以官方文档当期版本为准"的存疑标记——否则实现者会把 51/52/3500 当作已确认常量写死。

  **F7. 其他已核实为真的声明（无问题，仅登记核查轨迹）：**
  - §2"TEST-20261012 running 占用 implementation/ 与 docs/ai/"——`ai_session.py status` 实测属实（grok/grok-4.7, running, 2026-10-12）。
  - §5"requirements 未列 psycopg"——`implementation/requirements.txt` 确实无 psycopg（注意 `deploy/offline/dist/requirements.lock.txt` 里同时混有 psycopg/psycopg-binary/psycopg2/psycopg2-binary 四个变体，佐证 §4.3"该锁混有构建机其他库、不能直接用"的判断）。
  - §5"前端构建版默认 127.0.0.1:8000/8001"——`apps/admin_web/src/api.ts:4,7,15,16` 实证。
  - §3 网络事实与 `connection-report.md` 逐条吻合（前置机 openEuler 22.03、转发被拒、No route to host、DNS 失败均一致）。
  - P1"尚无 Git 时…"——仓库根实测非 git 仓库，前提成立。
  - "本机历史实测 CPython 3.14"——requirements.txt 注释佐证。

  ## 四、无法核实项（如实披露）

  - 「multi-review Skill v5」本体不在仓库内（glob 无匹配），方案 §0 对该 skill 规则的转述是否完整，无法对照原文验证。
  - 码上放心 TOP 接口的全部字段级事实（3500 上限、result_type/sub_process_flag 取值、321/323 差异本身）离线不可核，只能确认文档对相关不确定性的标注态度（见 F6）。
  - `packages/var/blyb.sqlite3` 内是否含敏感数据未读取（只读约束下打开 SQLite 会产生 WAL 副作用，且超出必要范围）；仅确认文件存在即可支撑 F2。

  **汇总**：方案忠实、可加一处行数修正；两份待审文档质量总体较高（事实声明抽查全部属实），但有 1 处内部矛盾性遗漏（F1 端口直发）、1 处风险低估（F2 运行库已在 packages/var）、1 处死配置（F3）建议在进入执行前修订。

