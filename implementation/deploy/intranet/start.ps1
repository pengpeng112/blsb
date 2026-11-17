# T14 内网离线部署：启动脚本（PowerShell 5.1+，医院内网，不依赖外网）
# 用法：powershell -ExecutionPolicy Bypass -File deploy\intranet\start.ps1
#   实验室可加 -AllowDevTokens，允许 dev-* 演示令牌。生产不要加。
#   生产环境变量（启动前设置，脚本不打印令牌值）：
#     BLYB_DISABLE_DEV_TOKENS=1
#     BLYB_BOOTSTRAP_ADMIN_TOKEN=<至少24字符的随机令牌>
#     BLYB_BIND_HOST=0.0.0.0          可选，允许内网其他机器访问
#     BLYB_ENROLL_TOKEN=<随机令牌>    可选，扫描工作站注册；不设则仍是 dev-enroll-token
param(
    [switch]$AllowDevTokens
)
$ErrorActionPreference = "Stop"

function Test-PortBusy([string]$HostName, [int]$Port) {
    $probe = New-Object System.Net.Sockets.TcpClient
    try {
        $wait = $probe.BeginConnect($HostName, $Port, $null, $null)
        if ($wait.AsyncWaitHandle.WaitOne(300) -and $probe.Connected) { return $true }
        return $false
    } catch {
        return $false
    } finally {
        $probe.Close()
    }
}

function Stop-IntranetProcess([int]$TargetPid, [string]$PidFile) {
    $alive = Get-Process -Id $TargetPid -ErrorAction SilentlyContinue
    if ($alive) {
        Stop-Process -Id $TargetPid -Force
        Wait-Process -Id $TargetPid -Timeout 15 -ErrorAction SilentlyContinue
    }
    if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
}

if (-not $AllowDevTokens) {
    if ($env:BLYB_DISABLE_DEV_TOKENS -ne "1") {
        throw "Refusing to start. Set BLYB_DISABLE_DEV_TOKENS=1, or pass -AllowDevTokens only for a lab stack."
    }
    $bootstrap = $env:BLYB_BOOTSTRAP_ADMIN_TOKEN
    if ([string]::IsNullOrWhiteSpace($bootstrap) -or $bootstrap.Length -lt 24) {
        throw "Refusing to start. Set BLYB_BOOTSTRAP_ADMIN_TOKEN to a random token of at least 24 characters. The value is not printed."
    }
    Write-Host "[intranet] token hardening on; bootstrap token is set (value not printed)"
} else {
    Write-Host "[intranet] AllowDevTokens: dev tokens may stay active"
}

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)  # implementation\
Set-Location $Root

$VarDir = Join-Path $Root "var\intranet"
New-Item -ItemType Directory -Force -Path $VarDir | Out-Null
$VolumeRoot = Join-Path $VarDir "volume1"
New-Item -ItemType Directory -Force -Path $VolumeRoot | Out-Null

$env:BLYB_DATABASE_URL = "sqlite:///" + ($VarDir + "\core.sqlite3" -replace "\\", "/")
$env:BLYB_EXCHANGE_DB = Join-Path $VarDir "exchange.sqlite3"
$env:BLYB_VOLUME_ROOT = $VolumeRoot
$env:BLYB_STORAGE_EMBEDDED_WORKER = "0"
$env:PYTHONUNBUFFERED = "1"

$dbUrl = $env:BLYB_DATABASE_URL
if ($dbUrl -match "://[^/]*@" -or $dbUrl -match "(?i)password=") {
    Write-Host "[intranet] BLYB_DATABASE_URL is set (credentials not printed)"
} else {
    Write-Host "[intranet] BLYB_DATABASE_URL=$dbUrl"
}
Write-Host "[intranet] BLYB_VOLUME_ROOT=$env:BLYB_VOLUME_ROOT"
Write-Host "[intranet] BLYB_EXCHANGE_DB=$env:BLYB_EXCHANGE_DB"

$PidFile = Join-Path $VarDir "core.pid"
if (Test-Path $PidFile) {
    $oldText = (Get-Content $PidFile | Select-Object -First 1).Trim()
    $oldPid = 0
    if ([int]::TryParse($oldText, [ref]$oldPid)) {
        $alive = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($alive) {
            throw "Intranet stack already running (pid=$oldPid). Stop it with deploy\intranet\stop.ps1 before starting again."
        }
    }
    Remove-Item $PidFile -Force
}

$bind = $env:BLYB_BIND_HOST
if ([string]::IsNullOrWhiteSpace($bind)) { $bind = "127.0.0.1" }
$probeHosts = @("127.0.0.1")
if ($bind -ne "0.0.0.0" -and $bind -ne "::" -and $bind -ne "127.0.0.1") {
    $probeHosts += $bind
}
foreach ($port in @(8000, 8001, 8002, 8090)) {
    foreach ($probeHost in $probeHosts) {
        if (Test-PortBusy $probeHost $port) {
            throw "Port $port on $probeHost is already in use. This script does not stop foreign processes."
        }
    }
}

python -m apps.migrate
if ($LASTEXITCODE -ne 0) { throw "migrate failed (exit=$LASTEXITCODE)" }

$outLog = Join-Path $VarDir "run_dev.out.log"
$errLog = Join-Path $VarDir "run_dev.err.log"
foreach ($log in @($outLog, $errLog)) {
    if (Test-Path $log) { Remove-Item $log -Force }
}
$proc = Start-Process -FilePath "python" -ArgumentList "run_dev.py" `
    -WorkingDirectory $Root -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog
$proc.Id | Out-File -Encoding ascii $PidFile
Write-Host "[intranet] started pid=$($proc.Id); logs: var\intranet\run_dev.out.log / run_dev.err.log"

$healthHost = "127.0.0.1"
if ($bind -ne "0.0.0.0" -and $bind -ne "::") { $healthHost = $bind }
$targets = @(
    @{Name="core";     Url="http://${healthHost}:8000/health"},
    @{Name="storage";  Url="http://${healthHost}:8001/health"},
    @{Name="exchange"; Url="http://${healthHost}:8002/health"},
    @{Name="ui";       Url="http://${healthHost}:8000/ui/"}
)
$failed = @()
foreach ($t in $targets) {
    $ok = $false
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri $t.Url -TimeoutSec 2
            if ($r.StatusCode -eq 200) { $ok = $true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if ($ok) { Write-Host "[intranet] $($t.Name) healthy: $($t.Url)" }
    else { $failed += $t.Name; Write-Warning "[intranet] $($t.Name) NOT healthy: $($t.Url)" }
}
if ($failed.Count -gt 0) {
    Write-Host "[intranet] startup failed; stopping pid=$($proc.Id)"
    if (Test-Path $errLog) {
        Write-Host "[intranet] stderr tail:"
        Get-Content $errLog -Tail 40 -ErrorAction SilentlyContinue
    }
    Stop-IntranetProcess $proc.Id $PidFile
    throw ("health check failed: " + ($failed -join ", "))
}
Write-Host "[intranet] UI: http://${healthHost}:8000/ui/"
Write-Host "[intranet] stop: powershell -ExecutionPolicy Bypass -File deploy\intranet\stop.ps1"
