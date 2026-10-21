Stop-ScheduledTask -TaskName QuantFdCatchup_20261013 -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*fd_update_reseller*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; 'killed ' + $_.ProcessId }
Start-Sleep 3
Get-ChildItem 'C:\ChoiceBridge\c5_stage_20261012\payload\c5_core\data\A_Layer_Research\fundamental\raw' -Filter *.tmp | Remove-Item -Force
Copy-Item C:\ChoiceBridge\staging\catchup.ps1 C:\ChoiceBridge\live\fd_catchup_20261013.ps1 -Force
Copy-Item C:\ChoiceBridge\staging\fd_after\c5_daily.sh C:\ChoiceBridge\c5_stage_20261012\payload\c5_core\code\QuantaAgents\scripts\c5_daily.sh -Force
'--- restart ' + (Get-Date -Format s) | Add-Content C:\ChoiceBridge\live\logs\fd_catchup_20261013.log
Start-ScheduledTask -TaskName QuantFdCatchup_20261013; Start-Sleep 15
Get-Content C:\ChoiceBridge\live\logs\fd_catchup_20261013.log -Tail 3
Get-Process -Id 4092 | Select-Object Id | Out-String
