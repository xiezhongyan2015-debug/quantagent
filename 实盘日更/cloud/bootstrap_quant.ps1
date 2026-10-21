# One-time: unpack the GRU lite package into C:\ChoiceBridge\quant and smoke-test the chain under Git Bash.
# Usage: powershell -File bootstrap_quant.ps1 -Tar C:\ChoiceBridge\staging\gru_lite_20261012.tar.gz
param([string]$Tar = 'C:\ChoiceBridge\staging\gru_lite_20261012.tar.gz')
$ErrorActionPreference = 'Stop'; $env:PYTHONUTF8 = '1'
$root = 'C:\ChoiceBridge\quant'; New-Item -ItemType Directory -Force -Path $root | Out-Null
tar -xzf $Tar -C $root
$pkg = Get-ChildItem $root -Directory | Where-Object { $_.Name -like 'gru_lite_*' } | Sort-Object Name | Select-Object -Last 1
if (Test-Path "$root\current") { Remove-Item "$root\current" -Force -Recurse }
cmd /c mklink /J "$root\current" $pkg.FullName | Out-Null
$py = 'C:\ChoiceBridge\runtime\gru-venv\Scripts\python.exe'
& $py -c "import sys; sys.path.insert(0, r'$root\current'); import gru_live_extend_20261012.hourly_extend, gru_live_extend_20261012.assemble_infer, pcf_overlay_20261012.live_targets; print('imports OK')"
Write-Output "QUANT_READY $($pkg.FullName)"
