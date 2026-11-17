# 工具与模型如何接入同一项目

工具、模型和角色分开记录。例如工具使用Claude但模型由网关切换时，以实际标识记入session；名称相同也不证明共享聊天记忆。

| 工具/场景 | 项目入口 | 使用方法/验证 |
|---|---|---|
| Codex类本地工具 | 根AGENTS.md；implementation/AGENTS.md薄入口 | 从根目录打开；若从子目录启动，明确读取根约定。当前无Git根，所以同时提供子目录入口 |
| Claude Code/本地Claude工具 | 根CLAUDE.md；子目录CLAUDE.md薄入口 | 内容明确指向AGENTS和共享状态；使用客户端显示的指令列表/让模型复述入口确认读取 |
| zcod/ZCode | AI_START.md＋docs/ai/START_HERE.md | 具体产品规则发现机制待确认；目前在项目规则或首条任务中粘贴通用提示词，不创建猜测的隐藏规则文件 |
| Grok类开发工具 | 同上 | 确认是否有读文件/终端能力；工具规则区引用共同入口；没有能力则按聊天模型处理 |
| GPT/Kimi/GLM/Grok/Claude纯聊天模型 | .ai/context-packet.md快照 | Context命令生成后检查并复制；模型输出建议/补丁，本地执行工具验证，不能把建议直接计为完成 |
| 同一工具切换模型 | 相同入口、相同任务、独立session记录 | 先checkpoint/handed_off；下一模型读取当前文件并重新认领，不沿用不可见聊天前提 |

## 接入成功的最小检查

让新会话回答四项：当前基线、最后业务验证、下一任务编号、仍未执行的条件。
正确回答应来自STATE/HANDOFF/BACKLOG，而不是模型记忆。再查看 `ai_session.py status` 中工具/模型和范围是否对应本次会话。
薄入口加载内容取决于客户端版本/配置，不宣称所有品牌都会自动读取AGENTS.md。

## 官方入口依据

Codex官方说明包含AGENTS文件发现顺序及无项目根时只检查当前目录的行为，所以此项目有根/implementation两个入口。[OpenAI 官方说明](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

Claude官方说明支持CLAUDE.md及引用其他文件。本项目提供简短显式读取指引，保持一份公共规则，不引入外部自动导入。[Claude 官方说明](https://code.claude.com/docs/en/memory)

本轮只检查了这些官方约定和本地文件，没有登录、安装、重配任何AI工具/模型账号，也未分别启动各客户端测试自动加载。
