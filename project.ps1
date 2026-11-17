<# Stable root entry; delegates to existing application paths without moving data. #>
[CmdletBinding()]
param(
    [ValidateSet('Help','Status','Dev','Demo','Metrics','Agent','Migrate','Test','Probes','Build','Docs','Context')]
    [string]$Action = 'Help',
    [string]$PythonExe = 'python',
    [string]$TestPath = 'tests',
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$implementationRoot = Join-Path $projectRoot 'implementation'

if ($Action -eq 'Help') {
    Write-Output 'BLYB: .\project.ps1 -Action <Status|Dev|Demo|Metrics|Agent|Migrate|Test|Probes|Build|Docs|Context> [-DryRun]'
    Write-Output 'Dev/Demo: local synthetic stack, ports 8000/8001/8002/8090; Ctrl+C stops the foreground process.'
    Write-Output 'No dependency installation, source cleanup, port-owner termination, or production deployment is performed.'
    Write-Output 'Read docs/startup/README.md and docs/ai/START_HERE.md for usage and current handoff.'
    exit 0
}

$taskDirectory = $implementationRoot
$taskExecutable = $PythonExe
$taskArguments = @()
switch ($Action) {
    'Status' { $taskDirectory = $projectRoot; $taskArguments = @('scripts/ai_session.py','status') }
    'Dev' { $taskArguments = @('run_dev.py') }
    'Demo' { $taskArguments = @('run_dev.py','--demo') }
    'Metrics' { $taskArguments = @('apps/run_metrics_only.py') }
    'Agent' { $taskArguments = @('-m','apps.edge_agent.run','--once') }
    'Migrate' { $taskArguments = @('-m','apps.migrate') }
    'Test' {
        $resolvedTestPath = [IO.Path]::GetFullPath((Join-Path $implementationRoot $TestPath))
        $expectedTestRoot = [IO.Path]::GetFullPath((Join-Path $implementationRoot 'tests'))
        if (($resolvedTestPath -ne $expectedTestRoot) -and
            (-not $resolvedTestPath.StartsWith($expectedTestRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase))) {
            throw 'TestPath must stay under implementation/tests/.'
        }
        $taskArguments = @('-m','pytest',$resolvedTestPath,'-q','-ra')
    }
    'Probes' { $taskArguments = @('-m','pytest','docs/audit/2026-10-01/test_review_probes.py','-q','-ra') }
    'Build' { $taskDirectory = Join-Path $implementationRoot 'apps/admin_web'; $taskExecutable = 'npm'; $taskArguments = @('run','build') }
    'Docs' { $taskDirectory = $projectRoot; $taskArguments = @('scripts/project_docs.py','check') }
    'Context' { $taskDirectory = $projectRoot; $taskArguments = @('scripts/context_packet.py') }
}
if ($DryRun) {
    [pscustomobject]@{ WorkingDirectory=$taskDirectory; Executable=$taskExecutable; Arguments=$taskArguments } | ConvertTo-Json
    exit 0
}

if (($Action -eq 'Dev') -or ($Action -eq 'Demo')) {
    if ($env:BLYB_DATABASE_URL -and (-not $env:BLYB_DATABASE_URL.StartsWith('sqlite:'))) {
        throw 'Dev/Demo seeds synthetic data. Use an isolated SQLite URL, not an inherited production database URL.'
    }
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
if (($Action -eq 'Build') -and (-not (Test-Path -LiteralPath (Join-Path $taskDirectory 'node_modules')))) {
    throw 'Existing node_modules is required for offline build; this entry never installs dependencies.'
}
Push-Location -LiteralPath $taskDirectory
try {
    & $taskExecutable @taskArguments
    $taskExitCode = $LASTEXITCODE
} finally { Pop-Location }
exit $taskExitCode
