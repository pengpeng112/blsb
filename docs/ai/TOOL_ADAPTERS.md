# 工具与模型如何接入同一项目

工具、模型与角色分开登记；同一工具切换模型时填实际模型标识（无法获知写 `unknown`），不假设自动共享聊天记忆。

| 工具/场景 | 自动/推荐入口 | 使用要点 |
|---|---|---|
| Codex / Antigravity 类本地工具 | 根 [AGENTS.md](../../AGENTS.md)；[implementation/AGENTS.md](../../implementation/AGENTS.md) | 根目录已有 `.git/`，可直接用 `git status`/`git diff` 自查；登记会话从根目录执行 `ai_session.py` |
| Claude Code / 本地 Claude | 根 [CLAUDE.md](../../CLAUDE.md)；[implementation/CLAUDE.md](../../implementation/CLAUDE.md) | 薄指针转发至根 `AGENTS.md`，不在私有记忆另建规则 |
| ZCode / Grok 类工具 | [AI_START.md](../../AI_START.md) + [START_HERE.md](START_HERE.md) | 会话首条粘贴通用提示词；仅用户明确调用时才触发 `multi-review`/`multi-verify` 技能 |
| 纯聊天模型 | `.\project.ps1 -Action Context` → `.ai/context-packet.md` | 仅输出建议/补丁并标“未执行”；由本地工具落地验证 |

## 接入最小自检

让新会话用 4 句话回答：当前基线、最近实测、活跃会话锁（来自 `ai_session.py status`）、下一步任务；回答必须来自共享状态文件而非记忆。
