# Windows Agent 开机自启安装脚本 —— 计划任务形态（S10如实标注）。
# 注意：本脚本注册的是 SYSTEM 计划任务（Register-ScheduledTask），不是 Windows SCM 服务。
# SCM 真服务形态见 install-agent-scm.ps1（需 pywin32，本机未验证：无服务管理器环境）。
# 用法： powershell -File install-agent-service.ps1 -DeviceCode SCAN-WS-01 `
#          -PythonExe "C:\Python312\python.exe" -AppRoot "D:\blyb\implementation"
param(
  [Parameter(Mandatory=$true)][string]$DeviceCode,
  [Parameter(Mandatory=$true)][string]$PythonExe,
  [Parameter(Mandatory=$true)][string]$AppRoot,
  [string]$ServiceName = "BLYB-EdgeAgent-$DeviceCode"
)

$ErrorActionPreference = "Stop"

# 专用低权限服务账户（按医院策略可换成 gMSA/域账户）
$acct = "NT SERVICE\$ServiceName"

$dataDir = Join-Path $AppRoot "var"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

$action = New-ScheduledTaskAction -Execute $PythonExe `
  -Argument "-m apps.edge_agent.run" -WorkingDirectory $AppRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $ServiceName -Action $action -Trigger $trigger `
  -Settings $settings -RunLevel Limited -User "SYSTEM" -Force | Out-Null

Write-Host "已注册开机计划任务（非SCM服务）：$ServiceName"
Write-Host "下一步："
Write-Host " 1) 首次运行生成 $dataDir\agent-config.json（填 watch_root/core_url/storage_url）"
Write-Host " 2) 首次运行用 BLYB_ENROLL_TOKEN 自动注册并保存 agent 令牌（ACL保护该目录）"
Write-Host " 3) 日志见 var\agent-queue.sqlite3 / Windows 任务计划历史"
Write-Host "卸载： Unregister-ScheduledTask -TaskName $ServiceName -Confirm:`$false"
