# 给任何工具或模型的通用接手提示词

复制下方内容到任意 AI 工具（Codex / Claude / ZCode / Antigravity / Grok / 纯聊天模型），将最后一行替换为本次任务：

```text
工作区：F:\python\blyb。
1. 先遵循根目录 AGENTS.md（唯一规则源），读取 docs/ai/START_HERE.md、STATE.md、HANDOFF.md。
2. 涉及具体上报业务时，再读 数据上报/README.md 及对应业务 README；涉及查库先读 docs/startup/DATABASE_ACCESS.md 与全局 sjzc 技能。
3. 有本地读写/终端能力时：开工前跑 python scripts/ai_session.py status 检查活跃锁，非琐碎任务先 start 登记最小修改范围，改完验证并更新精简状态后 end 释放锁。
4. 遵守 AGENTS.md §7 文件放置与文书自动整理规范：新建文件按目录归位；若发现根目录或业务目录有用户新放的未整理文书，主动按规范整理归位（批量移动/删除先确认），并跑 `.\project.ps1 -Action Docs` 保持 0 断链与 0 根杂项。
5. 无本地文件能力时：仅基于 project.ps1 -Action Context 生成的 .ai/context-packet.md 给建议/补丁，所有执行与测试标“未执行”。
6. 生产写入/部署/外发/DDL/删除核心文件/引入新依赖必须先获本轮明确批准；严禁向公开 Git 仓提交真实内网 IP、密钥或患者 PII。
本次任务：<填写任务编号、目标或问题>
```

纯聊天模型接手包由 `.\project.ps1 -Action Context` 生成至 `.ai/context-packet.md`（已精简 ≤25 KB，不含密钥与运行数据）。
