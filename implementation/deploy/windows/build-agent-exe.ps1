# 构建 BLYB-EdgeAgent.exe / BLYB-ScanSimulator.exe（PyInstaller，免安装含运行环境）
# 用法：在 implementation 仓库根执行  powershell -File deploy\windows\build-agent-exe.ps1
# 产物：deploy\windows\dist\BLYB-EdgeAgent.exe（单文件）等
#
# 备注：
#  - 默认 --onefile（分发单文件）；医院杀软误报率高时改用 --onedir（保留 dist/BLYB-EdgeAgent/ 整目录分发）。
#  - 生产分发建议对 exe 做代码签名（院内白名单/SmartScreen）。
#  - Win7 构建见同目录 win7\README.md（需独立 Python 3.8 工具链，本脚本不支持）。
param([switch]$OneDir)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
Set-Location $root
$dist = Join-Path $root "deploy\windows\dist"
$mode = if ($OneDir) { "--onedir" } else { "--onefile" }

python -m PyInstaller --noconfirm --clean --console $mode `
  --name BLYB-EdgeAgent `
  --paths "$root" `
  --distpath "$dist" --workpath "$root\build\pyinstaller" --specpath "$root\build\pyinstaller" `
  (Join-Path $root "deploy\windows\agent_entry.py")
if ($LASTEXITCODE -ne 0) { throw "agent build failed" }

python -m PyInstaller --noconfirm --clean --console $mode `
  --name BLYB-ScanSimulator `
  --paths "$root" `
  --distpath "$dist" --workpath "$root\build\pyinstaller" --specpath "$root\build\pyinstaller" `
  (Join-Path $root "deploy\windows\simulator_entry.py")
if ($LASTEXITCODE -ne 0) { throw "simulator build failed" }

Get-ChildItem $dist -Recurse -File | Where-Object { $_.Name -like "BLYB-*" } |
  Select-Object FullName, @{n="SizeMB";e={[math]::Round($_.Length/1MB,1)}}
