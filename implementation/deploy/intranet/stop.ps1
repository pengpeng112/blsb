# T14 内网离线部署：停止脚本（结束进程并核对退出）
# 用法：powershell -ExecutionPolicy Bypass -File deploy\intranet\stop.ps1
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$PidFile = Join-Path $Root "var\intranet\core.pid"
if (-not (Test-Path $PidFile)) {
    Write-Host "[intranet] no pid file; nothing to stop"
    return
}
$TargetPid = [int](Get-Content $PidFile | Select-Object -First 1)
try {
    $proc = Get-Process -Id $TargetPid -ErrorAction Stop
} catch {
    Write-Host "[intranet] pid $TargetPid already exited"
    Remove-Item $PidFile -Force
    return
}
Write-Host "[intranet] stopping pid=$TargetPid ..."
Stop-Process -Id $TargetPid -Force
Wait-Process -Id $TargetPid -Timeout 15 -ErrorAction SilentlyContinue
try {
    Get-Process -Id $TargetPid -ErrorAction Stop | Out-Null
    throw "process $TargetPid still alive after stop"
} catch [Microsoft.PowerShell.Commands.ProcessCommandException] {
    # 期望路径：进程已不存在
}
Write-Host "[intranet] pid $TargetPid stopped and verified"
Remove-Item $PidFile -Force
