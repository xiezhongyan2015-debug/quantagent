# One-time, run as Administrator in the interactive session. Re-running replaces the tasks.
$ia = New-ScheduledTaskPrincipal -UserId Administrator -LogonType Interactive -RunLevel Highest
$sys = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest
$root = 'C:\ChoiceBridge\quant\current'
# 1) daily pipeline 16:30 Mon-Fri
$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File $root\实盘日更\cloud\daily_cloud.ps1" -WorkingDirectory $root
$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 16:30
Register-ScheduledTask -TaskName 'QuantDaily_1605' -Action $a -Trigger $t -Principal $ia -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 4) -StartWhenAvailable) -Force | Out-Null
# 2) intraday monitor every 10 min 09:20-15:20 Mon-Fri
$a2 = New-ScheduledTaskAction -Execute 'C:\ChoiceBridge\runtime\choice-venv\Scripts\python.exe' -Argument "$root\实盘日更\tools\monitor.py" -WorkingDirectory $root
$t2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 09:20
$rep = New-ScheduledTaskTrigger -Once -At 09:20 -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Hours 6); $t2.Repetition = $rep.Repetition
Register-ScheduledTask -TaskName 'QuantMonitor' -Action $a2 -Trigger $t2 -Principal $ia -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew) -Force | Out-Null
# 3) boot alert (SYSTEM, no logon needed)
$a3 = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File $root\实盘日更\cloud\on_boot_alert.ps1"
Register-ScheduledTask -TaskName 'QuantBootAlert' -Action $a3 -Trigger (New-ScheduledTaskTrigger -AtStartup) -Principal $sys -Force | Out-Null
# 4) re-arm at Administrator logon
$a4 = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File $root\实盘日更\cloud\on_logon_rearm.ps1" -WorkingDirectory 'C:\ChoiceBridge\executor'
Register-ScheduledTask -TaskName 'QuantLogonRearm' -Action $a4 -Trigger (New-ScheduledTaskTrigger -AtLogOn -User Administrator) -Principal $ia -Force | Out-Null
Get-ScheduledTask | Where-Object { $_.TaskName -like 'Quant*' } | Select-Object TaskName, State
