# AI 接手入口

适用于 ZCode/zcod、Codex、Claude、Grok 等工具，也适用于 GPT/Kimi/GLM/Grok 等模型。
**工具负责文件/终端能力，模型负责推理；事实来自同一工作区和证据文件。**

## 最少读取顺序

1. [共同约定](../../AGENTS.md)：首次接手必读。
2. [STATE](STATE.md)：当前基线、最近验证与限制。
3. [HANDOFF](HANDOFF.md)：上一任务实际做了什么、下一步。
4. [BACKLOG](BACKLOG.md)：选择任务；再读相关详细任务书/设计，不必全量灌入历史。

文档定位用 [分类目录](../README.md)，启动用 [startup](../startup/README.md)，工具接入用 [TOOL_ADAPTERS](TOOL_ADAPTERS.md)。

具体上报项目先读[上报目录总索引](../../数据上报/README.md)，再读对应业务目录的README，例如[码上放心](../../数据上报/1、码上放心平台/README.md)、[医保电子病历](../../数据上报/2、医保电子病历上报/README.md)、[病理](../../数据上报/3、病理上报/README.md)。
**每个上报项目必须在自己的 `数据上报/<编号、业务名>/README.md` 说明原始接口文档、代码/SQL/视图、系统链路、受限凭据位置、部署/验证证据和下一步。** 新建项目或变更这些路径/证据时同步维护；使用[统一模板](../../数据上报/上报项目目录说明模板.md)。这是业务导航，最新状态仍以共享STATE/HANDOFF为准；不复制运行项目或另起业务状态文件。

涉及数据库连接、表结构或只读取数，必读 [sjzc 统一入口](../startup/DATABASE_ACCESS.md)
及其指向的全局技能。各工具使用相同通道；服务器托管凭据禁止读取或打印。
已登记密码不再向用户索取。元数据与活库核验分开，真实发送不包含在只读授权中。

码上放心平台凭证已由用户提供并保存本机受限目录，位置、无回显调用方式和未确认项见
[平台资料登记](../development/platform-credentials-20261020/README.md)。共享文档不含Secret明文，
后续同机AI先读该记录，不重复索要已登记的AppKey/AppSecret/ref_ent_id。

追溯码链路、两视图、服务器用途及人工历史记录核查见[流程说明](../operations/drug-trace-flow-20261022/README.md)，含空白文本格式Excel模板。账号密码完整版本在该文指向的本机受限目录，禁止复制到共享文档/聊天。

最新[官方指南＋HIS视图复核](../development/drug-trace-official-view-audit-20261022/README.md)：正常门诊batch可用，持续齐套/变更及部分解析策略见A01—A05，禁止由一笔成功推定所有历史准确。

## 顺序接力：推荐日常使用方式

```powershell
# 根目录；先看别的工具有没有登记进行中的任务
python scripts/ai_session.py status

# 例：当前工具和模型由实际会话填；不要把示例品牌当真实标识
python scripts/ai_session.py start --task R4-T1 --tool TOOL --model MODEL --scope implementation/apps/core_api/app.py --scope implementation/tests/integration/test_local_gaps.py

# 在本次独立记录中写进展；请把返回的session_id填入下面命令
python scripts/ai_session.py checkpoint --session SESSION-ID --summary '已完成映射正常路径，权限与错误路径待验证'

# 结束前创建真实交接文件，再关联该文件释放本次修改范围
python scripts/ai_session.py end --session SESSION-ID --result handed_off --summary '正常路径已通过，继续做跨租户检查' --handoff docs/ai/sessions/TASK-ID.md
```

任务完整完成可用 `--result completed`；明确取消用 `cancelled`。`checkpoint`仍保留修改范围，`handed_off`释放范围，下一工具重新登记。先更新实际状态再退出，不能靠聊天末尾的一句“完成”交接。

## 共享状态的职责

| 文件 | 放什么 | 不放什么 |
|---|---|---|
| STATE.md | 最新状态摘要、最近有效证据、当前模式和限制 | 逐条调试输出、未经验证的成功标签 |
| BACKLOG.md | 任务编号、状态、依赖、完成证据链接 | 每个模型各自改名的一套任务 |
| HANDOFF.md | 当前最后一次交接、推荐下一项、精确代码/命令入口 | 完整聊天记录、密钥和患者数据 |
| sessions/每任务.md | 本次工具/模型、范围、实现、测试、剩余、发现 | 覆盖其他任务的记录 |
| .ai/sessions/*.json | 当前工作区任务认领、范围冲突、checkpoint摘要 | 跨电脑自动同步承诺、编辑器强制锁 |

按 [交接模板](SESSION_TEMPLATE.md) 留独立任务记录。执行轮次按业务任务记录；文档维护不冒充完成第4轮。

## 冲突与中断

- 同一任务或父/子目录范围重叠，登记工具拒绝再次start；不把“不见进展”当成原执行者已经停止。
- 不同文件可分别登记，但一起编辑STATE/HANDOFF前应协调一个写入者；其他人写自己的session。
- 旧任务失联时，先核实原工具已经停止并读其最后checkpoint，再用 `recover --session ID --reason 原因 --handoff 文件` 转为交接。超时不会自动释放。
- 短暂registry.lock只保护登记写入。异常遗留时先查文件里的PID，确认进程已结束再人工处理；不能自动删其他人的锁。
- 本机制是共同遵守的协议。未读取规则或跳过登记的工具仍可写文件；要从根目录打开工作区，并给每个新会话使用 [AI_START](../../AI_START.md)。
- 不同工作区/电脑/分支的登记文件不共享；以Git变更和可审查交接包合并事实。尚无Git时，顺序使用同一工作区最容易避免互相覆盖。

## 只有聊天能力的模型

从根目录运行 `project.ps1 -Action Context`，生成 `.ai/context-packet.md`。由用户检查内容后复制到模型上下文。
它只读允许的四份共享文档，不扫描私有聊天/运行数据。聊天模型给补丁建议；本地工具登记任务、核对补丁并执行验证。
换模型时无需新增“GPT状态/Kimi状态/Claude状态”文件，仍更新同一份共享状态。
