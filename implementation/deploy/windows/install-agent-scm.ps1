# Windows SCM 真服务安装脚本（W13 框架，未执行验证）。
# 前置：目标机安装 pywin32（pip install pywin32）；本仓库开发机未安装，
# 因此该路径**未在本机验证**（无服务管理器/重置环境），如实标注。
# 用法： powershell -File install-agent-scm.ps1 -DeviceCode SCAN-WS-01 `
#          -PythonExe "C:\Python312\python.exe" -AppRoot "D:\blyb\implementation"
param(
  [Parameter(Mandatory=$true)][string]$DeviceCode,
  [Parameter(Mandatory=$true)][string]$PythonExe,
  [Parameter(Mandatory=$true)][string]$AppRoot,
  [string]$ServiceName = "BLYBEdgeAgent-$($DeviceCode -replace '[^A-Za-z0-9]','')"
)

$ErrorActionPreference = "Stop"

# SCM 服务主体（apps.edge_agent.win_service.BLYBAgentService）
$entry = Join-Path $AppRoot "apps\edge_agent\win_service.py"
if (-not (Test-Path $entry)) { throw "win_service.py 不存在：$entry" }

# 专用低权限本地账户（按医院策略替换为域账户/gMSA；此处创建 .\svc_blyb_agent）
$acctName = "svc_blyb_agent"
$acct = ".\$acctName"
if (-not (Get-LocalUser -Name $acctName -ErrorAction SilentlyContinue)) {
  $pw = [System.Web.Security.Membership]::GeneratePassword(32, 8)
  New-LocalUser -Name $acctName -Password ($pw | ConvertTo-SecureString -AsPlainText -Force) `
    -Description "BLYB EdgeAgent service account (least privilege)" | Out-Null
  Write-Host "已创建本地账户 $acct（请立即妥善保存/轮换密码）"
}

# 数据目录 ACL：仅服务账户+Administrators+SYSTEM
$dataDir = Join-Path $AppRoot "var"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
icacls $dataDir /inheritance:r /grant "Administrators:(OI)(CI)F" `
  /grant "SYSTEM:(OI)(CI)F" /grant "${acct}:(OI)(CI)M" | Out-Null

# 以服务宿主方式注册 SCM 服务（pywin32 服务类内完成 start/stop/recovery）
& $PythonExe -m win32serviceutil --startup auto --username $acct `
  --password "" --install-as-service "apps.edge_agent.win_service" 2>$null
if ($LASTEXITCODE -ne 0) {
  # 兜底：sc.exe 直接注册（win_service 支持 console 自注册参数）
  $binPath = "`"$PythonExe`" `"$entry`" --device-code $DeviceCode --app-root `"$AppRoot`""
  sc.exe create $ServiceName binPath= $binPath start= auto obj= $acct
  sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/5000/restart/60000
  sc.exe description $ServiceName "BLYB Edge Agent (SCM service; recovery: 3x restart)"
}
Write-Host "SCM服务已注册（本脚本未在本机验证——无服务管理器环境，阶段E现场项）"
