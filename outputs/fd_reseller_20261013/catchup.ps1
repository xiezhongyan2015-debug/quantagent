$ErrorActionPreference = 'Continue'; $env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'; $env:PYTHONPATH = 'src'
$q = 'C:\ChoiceBridge\c5_stage_20261012\payload\c5_core\code\QuantaAgents'; Set-Location $q
$log = 'C:\ChoiceBridge\live\logs\fd_catchup_20261013.log'
& 'C:\ChoiceBridge\runtime\c5-venv\Scripts\python.exe' scripts\fd_update_reseller.py --start 20260919 --end 20261013 --skip daily adj_factor *>> $log
"EXIT $LASTEXITCODE $(Get-Date -Format s)" | Add-Content $log
