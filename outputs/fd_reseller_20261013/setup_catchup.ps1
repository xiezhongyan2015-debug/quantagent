$ErrorActionPreference = 'Stop'
$raw = 'C:\ChoiceBridge\c5_stage_20261012\payload\c5_core\data\A_Layer_Research\fundamental\raw'; $bk = 'C:\ChoiceBridge\live\backups\fd_raw_20261013'
if (-not (Test-Path $bk)) { New-Item -ItemType Directory -Force -Path $bk | Out-Null; Copy-Item "$raw\*" $bk }
'backup files ' + (Get-ChildItem $bk).Count + ' size GB ' + [math]::Round(((Get-ChildItem $bk | Measure-Object Length -Sum).Sum/1GB),2)
Copy-Item C:\ChoiceBridge\staging\fd_update_reseller.py 'C:\ChoiceBridge\c5_stage_20261012\payload\c5_core\code\QuantaAgents\scripts\fd_update_reseller.py' -Force
Copy-Item C:\ChoiceBridge\staging\catchup.ps1 C:\ChoiceBridge\live\fd_catchup_20261013.ps1 -Force
$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\ChoiceBridge\live\fd_catchup_20261013.ps1'
$p = New-ScheduledTaskPrincipal -UserId Administrator -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName 'QuantFdCatchup_20261013' -Action $a -Principal $p -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 4)) -Force | Out-Null
Start-ScheduledTask -TaskName 'QuantFdCatchup_20261013'; Start-Sleep 20
Get-Content C:\ChoiceBridge\live\logs\fd_catchup_20261013.log -ErrorAction SilentlyContinue
'free GB ' + [math]::Round((Get-PSDrive C).Free/1GB,1)
