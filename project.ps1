<# Stable root entry; delegates to existing application paths without moving data. #>
[CmdletBinding()]
param(
    [ValidateSet('Help','Status','Health','Dev','Demo','Showcase','Metrics','Agent','Migrate','Test','TestFast','TestWeb','Probes','Build','Docs','Context')]
    [string]$Action = 'Help',
    [string]$PythonExe = 'python',
    [string]$TestPath = 'tests',
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$implementationRoot = Join-Path $projectRoot 'implementation'

if ($Action -eq 'Help') {
    Write-Output 'BLYB: .\project.ps1 -Action <Action> [-PythonExe python] [-TestPath tests] [-DryRun]'
    Write-Output '  Dev/Demo/Showcase : local synthetic stack (ports 8000/8001/8002/8090) & showcase seeding'
    Write-Output '  Health            : read-only HTTP /health check on local 8000/8001/8002/8090'
    Write-Output '  TestFast/Test     : fast smoke gate (<15s) or pytest under implementation/tests/'
    Write-Output '  TestWeb/Build     : frontend node --test or offline npm run build'
    Write-Output '  Probes/Docs       : 43 audit probes or document link + source hash check'
    Write-Output '  Status/Context    : active AI session claims or slim .ai/context-packet.md export'
    Write-Output '  Metrics/Agent/Migrate : metrics-only mode, single-shot agent, or explicit DB migration'
    Write-Output 'No dependency installation, source cleanup, port-owner termination, or production deployment is performed.'
    exit 0
}

if ($Action -eq 'Health') {
    $urls = @('http://127.0.0.1:8000/health','http://127.0.0.1:8001/health','http://127.0.0.1:8002/health','http://127.0.0.1:8090/health')
    if ($DryRun) {
        [pscustomobject]@{ WorkingDirectory=$projectRoot; Executable='Invoke-WebRequest'; Arguments=$urls } | ConvertTo-Json
        exit 0
    }
    $allOk = $true
    foreach ($u in $urls) {
        try {
            $resp = Invoke-WebRequest -Uri $u -Method Get -TimeoutSec 2 -UseBasicParsing
            Write-Output ("OK   {0}  HTTP {1}" -f $u, $resp.StatusCode)
        } catch {
            Write-Output ("DOWN {0}  ({1})" -f $u, $_.Exception.Message.Split([Environment]::NewLine)[0])
            $allOk = $false
        }
    }
    if ($allOk) { exit 0 } else { exit 1 }
}

if ($Action -eq 'TestFast') {
    $steps = @(
        @{ Dir=$projectRoot; Args=@('implementation/deploy/inf_pusher/selftest.py') },
        @{ Dir=$projectRoot; Args=@('implementation/deploy/inf_watchdog/selftest.py') },
        @{ Dir=$projectRoot; Args=@('-m','pytest','scripts/tests/test_coordination.py','-q','-ra') }
    )
    if ($DryRun) {
        [pscustomobject]@{
            WorkingDirectory = $projectRoot
            Executable = $PythonExe
            Steps = $steps | ForEach-Object { $_.Args -join ' ' }
        } | ConvertTo-Json
        exit 0
    }
    foreach ($st in $steps) {
        Push-Location -LiteralPath $st.Dir
        try {
            & $PythonExe @($st.Args)
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        } finally { Pop-Location }
    }
    exit 0
}

$taskDirectory = $implementationRoot
$taskExecutable = $PythonExe
$taskArguments = @()
switch ($Action) {
    'Status'   { $taskDirectory = $projectRoot; $taskArguments = @('scripts/ai_session.py','status') }
    'Dev'      { $taskArguments = @('run_dev.py') }
    'Demo'     { $taskArguments = @('run_dev.py','--demo') }
    'Showcase' { $taskDirectory = $projectRoot; $taskArguments = @('scripts/seed_local_showcase.py','--confirm-local-demo') }
    'Metrics'  { $taskArguments = @('apps/run_metrics_only.py') }
    'Agent'    { $taskArguments = @('-m','apps.edge_agent.run','--once') }
    'Migrate'  { $taskArguments = @('-m','apps.migrate') }
    'Test' {
        $resolvedTestPath = [IO.Path]::GetFullPath((Join-Path $implementationRoot $TestPath))
        $expectedTestRoot = [IO.Path]::GetFullPath((Join-Path $implementationRoot 'tests'))
        if (($resolvedTestPath -ne $expectedTestRoot) -and
            (-not $resolvedTestPath.StartsWith($expectedTestRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase))) {
            throw 'TestPath must stay under implementation/tests/.'
        }
        $taskArguments = @('-m','pytest',$resolvedTestPath,'-q','-ra')
    }
    'Probes'   { $taskArguments = @('-m','pytest','docs/audit/2026-10-01/test_review_probes.py','-q','-ra') }
    'Build'    { $taskDirectory = Join-Path $implementationRoot 'apps/admin_web'; $taskExecutable = 'npm'; $taskArguments = @('run','build') }
    'TestWeb'  { $taskDirectory = Join-Path $implementationRoot 'apps/admin_web'; $taskExecutable = 'npm'; $taskArguments = @('test') }
    'Docs'     { $taskDirectory = $projectRoot; $taskArguments = @('scripts/project_docs.py','check') }
    'Context'  { $taskDirectory = $projectRoot; $taskArguments = @('scripts/context_packet.py') }
}
if ($DryRun) {
    [pscustomobject]@{ WorkingDirectory=$taskDirectory; Executable=$taskExecutable; Arguments=$taskArguments } | ConvertTo-Json
    exit 0
}

if (($Action -eq 'Dev') -or ($Action -eq 'Demo') -or ($Action -eq 'Showcase')) {
    if ($env:BLYB_DATABASE_URL -and (-not $env:BLYB_DATABASE_URL.StartsWith('sqlite:'))) {
        throw 'Dev/Demo/Showcase seeds synthetic data. Use an isolated SQLite URL, not an inherited production database URL.'
    }
}
if (($Action -eq 'Dev') -or ($Action -eq 'Demo')) {
    foreach ($port in @(8000,8001,8002,8090)) {
        $probe = New-Object System.Net.Sockets.TcpClient
        try {
            $connect = $probe.BeginConnect('127.0.0.1',$port,$null,$null)
            if ($connect.AsyncWaitHandle.WaitOne(200) -and $probe.Connected) {
                throw "Port $port is occupied. Identify the existing owner; this entry does not stop other processes."
            }
        } finally { $probe.Close() }
    }
}
if (($Action -eq 'Migrate') -and (-not $env:BLYB_DATABASE_URL)) {
    throw 'Set BLYB_DATABASE_URL explicitly before migration. See docs/startup/README.md.'
}
if ((($Action -eq 'Build') -or ($Action -eq 'TestWeb')) -and (-not (Test-Path -LiteralPath (Join-Path $taskDirectory 'node_modules')))) {
    throw 'Existing node_modules is required for offline frontend build/test; this entry never installs dependencies.'
}
Push-Location -LiteralPath $taskDirectory
try {
    & $taskExecutable @taskArguments
    $taskExitCode = $LASTEXITCODE
} finally { Pop-Location }
exit $taskExitCode
