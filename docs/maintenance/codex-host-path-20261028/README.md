# Codex --no-daemon 辅助进程路径修复（2026-10-28）

任务 CODEX-HOST-PATH-20261028；Codex / model unknown。

## 结论与用户操作

已将 Codex CLI 0.165.0 完整安装到固定短目录 **`C:/Tools/CodexCLI`**，并将该目录加入系统 PATH 第一项。其他 PATH 项及 Node 安装位置保持原样，未修改 npm 全局 prefix、认证、会话历史或权限配置。

请退出发生错误的旧 Codex 进程，并完整关闭、重新打开终端一次，然后直接运行：

```powershell
codex --no-daemon
```

继续已有会话可用 `codex --no-daemon resume`，无需每次输入完整安装路径。

已打开的旧终端会继承旧 PATH。若不便重开，可在退出旧 Codex 后，在该 PowerShell 窗口仅刷新本窗口环境：

```powershell
$env:Path = 'C:\Tools\CodexCLI;' + $env:Path
codex --no-daemon
```

正在运行的进程不会因为更新 PATH 自动更换二进制；本次没有强制终止其他会话。

## 根因实测

默认命令原先命中 Windows WinGet 的 Node 包目录，其嵌套 npm 包中的 `codex-code-mode-host.exe` 完整路径长 **262 字符**。文件确实存在，大小 78139696 字节，SHA256 与正常短目录副本完全相同。

- 对 262 字符路径实际启动 `--help`，Python 子进程返回 **WinError 206（文件名或扩展名太长）/ errno 2**，PowerShell 显示进程启动失败。
- 同一版本同一哈希的文件经 185 字符路径启动成功；最终固定目录中的路径为 **140 字符**，启动成功。
- 系统 `LongPathsEnabled=1` 已开启；仅开启该设置不足以解决这条进程启动链路。
- 本机运行日志只读筛查发现 2026-10-28 18:26:22、18:26:39（北京时间）的工具路由错误，均指向该 WinGet 长路径。只提取错误级别、时间与程序路径，未输出聊天正文、密钥或患者数据。

上次安装任务验证了版本与 CLI help，未覆盖辅助进程真正启动；不能把上次验证视为本问题已经解决。本次明确补测。

## 已执行与证据

安装命令（官方 npm 源，退出 0，12 秒完成）：

```powershell
npm install --global --prefix C:/Tools/CodexCLI @openai/codex@0.165.0 --registry=https://registry.npmjs.org --fetch-retries=1 --fetch-timeout=90000 --no-audit --no-fund
```

安装后仅在机器 PATH 前添加 `C:\Tools\CodexCLI`，并发送系统 Environment 变更通知。原环境记录在 [environment-before.json](environment-before.json)，修改当刻的完整原值与新值在 [path-change.json](path-change.json)。回退时从当前机器 PATH 移除本次新增的 `C:\Tools\CodexCLI` 项即可，勿用旧快照覆盖其他人后续环境变更。

实测通过：

1. 从新系统/用户 PATH 构造的 PowerShell、Windows PowerShell 5.1、CMD 均命中短目录；`codex --no-daemon --version` 均为 0.165.0、退出 0。
2. `codex-code-mode-host.exe --help` 退出 0。
3. `codex-code-mode-host.exe --listen stdio://` 实际启动后持续运行；仅终止本次自建的探测进程。
4. 独立前台 app-server 完成 initialize 握手，并经 `command/exec` 执行本机 `print("CODEX_LOCAL_EXEC_OK")`：退出 0、stdout 标记匹配、stderr 为空。未创建模型回合，未访问业务数据库或上传业务数据。

可重复的本地验证脚本：[verify_local_runtime.py](verify_local_runtime.py)；实际结果：[runtime-verification.json](runtime-verification.json)。

CLI SHA256：`d4b2e6a054ea1dfe53217f9d8942c5f8a9f4b76171a1e0bb76ced5759915300a`。
辅助进程 SHA256：`c15b0ec7c44be4bef2516c251cc8b9f3f4ab9f8727cf2d567d1f183fd415ef2d`。

交互式 TUI 的自动伪终端检查受当前执行工具自身 CreateProcess 权限限制，未完成；未另行进行模型请求或声称旧会话已自动切换。已有原配置与历史保持不变，用户重开后可继续使用。

## 后续升级与交接

后续更新保持短安装目录。若手工通过 npm 更新，应先退出使用这套程序的 Codex，再从普通终端运行 `npm install --global --prefix C:\Tools\CodexCLI @openai/codex@latest`，避免原地覆盖正在执行的文件。无需恢复 WinGet 深层包为默认入口。

官方说明确认 `--no-daemon` 为支持的交互式参数：[CLI reference](https://learn.chatgpt.com/docs/cli/reference)。本地前台执行验证按 [App Server command/exec](https://learn.chatgpt.com/docs/app-server) 执行。

本次仅修改本机客户端与本维护目录。共享 `docs/ai` 正被 DTR-SCOPE-DEPLOY-20261028 占用，未覆盖其状态/交接；登记结束关联本说明，不触碰生产上报部署。
