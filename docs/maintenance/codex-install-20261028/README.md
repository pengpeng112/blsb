# Codex CLI 更新与修复（2026-10-28，已完成）

任务：CODEX-INSTALL-20261028；工具：Codex；模型：unknown。

用户要求检查安装一直转圈，并安装官方最新版本，随后明确是终端命令行版更新。此任务仅处理本机客户端，不操作上报服务、HIS、生产数据或业务定时任务。

## 最终结果

官方 npm registry latest 查询为 **0.165.0**，默认 `codex` 已修复至此版本。Windows PowerShell 5.1、PowerShell 7、CMD 三种新进程均实测 `codex --version` 返回 `codex-cli 0.165.0`、退出码 0；`codex --help` 通过。

未修改用户认证、会话或配置文件；未关闭正在运行的旧 CLI 与 code-mode-host。旧会话需自然退出后再启动，才能使用新进程版本。

## 已查明的失败原因

原更新日志 `C:/Users/Administrator/AppData/Local/npm-cache/_logs/2026-10-28T09_08_52_587Z-debug-0.log`：Windows 平台包下载耗时 **773315 ms**，随后 npm `SIGINT` 中断、退出 1，并有 `EPERM` 清理失败。默认 Node 全局目录中残留的版本登记虽然是 0.165.0，`codex.exe` 仅 287992062 字节，运行实测报 `spawn EFTYPE`。重新完整安装的同版可执行文件是 357356336 字节。

因此这里不是“看到版本登记就算更新成功”；慢下载、中断后的不完整文件，以及多个安装入口造成了混淆。不能仅根据 EPERM 日志确定占用者或杀毒软件责任。

## 实际修复

1. 先用官方 npm 源在独立目录安装并验证：
   `npm install --global --prefix C:/Users/Administrator/AppData/Local/Programs/CodexCLI-0.165.0 @openai/codex@0.165.0 --registry=https://registry.npmjs.org --fetch-retries=1 --fetch-timeout=90000 --no-audit --no-fund`
2. 核实默认 Node 全局包目录没有正在运行的 Codex 进程，将损坏包同目录备份为 `node_modules/@openai/codex.incomplete-20261028`，原入口文件备份在下述缓存目录 `original-node-prefix-shims/`。
3. 对默认 Node 全局 prefix 重新执行同版安装，实测 `added 2 packages in 7s`、退出 0：
   `C:/Users/Administrator/AppData/Local/Microsoft/WinGet/Packages/OpenJS.NodeJS.22_Microsoft.Winget.Source_8wekyb3d8bbwe/node-v22.23.2-win-x64`
4. 不改 PATH、不改 npm registry 持久配置，不覆盖正在使用的 Roaming/npm 0.163.0 与 Local/Programs/CodexCLI 0.164.0。当前默认命令命中步骤3的 0.165.0。
5. 比对独立安装和默认安装的 `codex.exe`，SHA256 均为 `d4b2e6a054ea1dfe53217f9d8942c5f8a9f4b76171a1e0bb76ced5759915300a`。`codex-code-mode-host.exe` 存在且为 78139696 字节。

如默认入口后续受其他工具更新影响，可直接运行已验证的备用入口：
`C:/Users/Administrator/AppData/Local/Programs/CodexCLI-0.165.0/codex.cmd --version`。
本次没有进行模型请求、登录刷新或新交互会话验证，安装成功不代表另行验证了账号或推理网络。

## 安装前实测

- 系统：Windows 10 企业版 G，10.0.19045，x64；当前终端具备管理员权限。
- 命令行默认入口：Roaming/npm，`codex --version` 为 0.163.0；另外 Local/Programs/CodexCLI 中存在 0.164.0。npm 官方 registry 的 latest 为 0.165.0。
- Windows PowerShell 5.1 查询 Appx：当前用户未安装 OpenAI.Codex；Microsoft Store 与 DesktopAppInstaller 已注册。PowerShell 7 无法直接加载 Appx 模块，后续使用系统 Windows PowerShell。
- winget list 成功，但登记的 CLI 版本 0.121.0 与实际命令行入口版本不同，不能据此判断已更新。
- 未发现正在运行的 Codex 桌面安装器，尚不能确定用户原先转圈的具体错误。当前 CLI 会话与 code-mode-host 保留运行。
- 官方桌面离线包 HEAD 返回 200，包版本 26.1023.6123.0，大小 979149564 字节。远程 ZIP 定向读取 AppxManifest.xml：Identity=OpenAI.Codex，DisplayName=ChatGPT，MinVersion=10.0.19041.0；本机版本符合。

## 官方来源与早期桌面排查

- https://learn.chatgpt.com/docs/windows/windows-app
- https://learn.chatgpt.com/docs/enterprise/windows-deployment
- CLI 官方安装说明：https://learn.chatgpt.com/docs/codex/cli
- 上述官方部署页给出的 x64 MSIX：https://persistent.oaistatic.com/codex-app-prod/ChatGPT-x64.msix
- 官方页面当前将桌面应用名称显示为 ChatGPT，包标识仍为 OpenAI.Codex。不要从第三方镜像下载，也不要将 CLI 的版本号当成桌面版本。

## 临时文件与交接

用户澄清前曾下载桌面包进行排查；澄清后立即停止自建下载进程，未安装桌面版。自建分段下载脚本及包分片已经清除。缓存目录 `C:/Users/Administrator/AppData/Local/CodexInstall/20261028/` 保留少量进度日志与入口备份；不包含用户凭据。

项目共享 docs/ai 正被 DTR-SCOPE-DEPLOY-20261028 占用，本任务仅写本目录，结束记录也保存在这里，避免覆盖正在进行的业务交接。
