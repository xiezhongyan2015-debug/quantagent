# At system startup (SYSTEM): tell the group that the machine rebooted and Administrator must log on for the pipeline/executor to run.
$ProgressPreference='SilentlyContinue'; $env:PYTHONUTF8='1'
Start-Sleep 60
& C:\ChoiceBridge\runtime\gru-venv\Scripts\python.exe C:\ChoiceBridge\quant\current\实盘日更\tools\notify.py ("⚠️ 云机已重启（" + (Get-Date -Format 'yyyy-MM-dd HH:mm') + "）。Administrator 需要登录一次，登录后会自动检查并恢复当日/次日的执行器预约。")
