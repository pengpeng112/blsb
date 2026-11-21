# AI 接手入口

适用于 Codex、Claude、ZCode、Antigravity、Grok 及纯聊天模型。**唯一规则源为根目录 [AGENTS.md](../../AGENTS.md)**，本文件仅列操作入口与协作命令速查。

## 1. 最少读取顺序

1. [AGENTS.md](../../AGENTS.md)：唯一规则与红线。
2. [STATE.md](STATE.md)：当前 5 大业务基线与最近关键变更（精简版，历史卷见 `archive/`）。
3. [HANDOFF.md](HANDOFF.md)：最新接力要点。
4. [BACKLOG.md](BACKLOG.md)：当前未完成待办与外部阻断。

按需导航：[文档分类](../README.md) · [历史演进](../HISTORY.md) · [启动说明](../startup/README.md) · [数据库 sjzc](../startup/DATABASE_ACCESS.md) · [数据上报总索引](../../数据上报/README.md) · [工具适配](TOOL_ADAPTERS.md)。

## 2. 跨 AI 认领与交接命令（根目录执行）

```powershell
# 1. 开工前查活跃锁（默认仅显示 running + 最近 5 条，加 --all 看全量）
python scripts/ai_session.py status

# 2. 必须登记任务：填实际工具与模型（未知写 unknown），--scope 精确到最小路径，禁锁 docs/ai 整目录
python scripts/ai_session.py start --task TASK-ID --tool TOOL --model MODEL --scope PATH

# 3. 阶段进展登记（保持锁）
python scripts/ai_session.py checkpoint --session SESSION-ID --summary "进展摘要"

# 4. 写完交接文件后释放锁（全部完成用 completed，未完接力用 handed_off）
python scripts/ai_session.py end --session SESSION-ID --result completed --handoff docs/ai/sessions/TASK-ID.md --summary "结果摘要"

# 5. 失联锁恢复（仅在确认原工具已停后，由用户同意执行）
python scripts/ai_session.py recover --session SESSION-ID --reason "核实原工具已停" --handoff docs/ai/sessions/RECOVERY.md
```

## 3. 共享状态职责表

| 文件 | 职责（保持精简） | 禁止放入 |
|---|---|---|
| `STATE.md` | 当前 5 大业务真实基线 + 最近关键里程碑（≤120 行） | 逐条调试日志、历史堆积流水账（旧项移入 `archive/`） |
| `HANDOFF.md` | 最近 1–2 次有效交接与关键避坑提醒（≤100 行） | 聊天全文、密钥、患者 PII |
| `BACKLOG.md` | 当前未完成待办与外部阻断（≤100 行） | 已完成项目的冗长全文（已完成仅留 1 行索引） |
| `sessions/<TASK>.md` | 单任务完整改动、实测命令/退出码与回退方式（[模板](SESSION_TEMPLATE.md)） | 覆盖他人任务记录 |
| `.ai/sessions/*.json` | 机器可读认领台账与范围冲突检查 | 密钥或跨电脑自动同步假设 |

## 4. 文件整理守门与纯聊天模型接手

- **文书与目录规范守门**：每次改动或整理用户新建文书后，运行 `python scripts/project_docs.py inventory` 与 `.\project.ps1 -Action Docs`，自动检查活跃断链、根目录杂项文件（`root_hygiene_warnings`）与状态文件膨胀（`state_bloat_warnings`），按 [AGENTS.md §7](../../AGENTS.md) 归位与归档。
- **纯聊天模型接手**：根目录运行 `.\project.ps1 -Action Context`，生成精简快照 `.ai/context-packet.md`（约 15–25 KB），经人工复核后复制给模型。
