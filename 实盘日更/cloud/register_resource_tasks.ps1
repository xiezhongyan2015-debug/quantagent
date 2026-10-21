# 2026-10-17 resource + session hygiene. Adds/replaces only these three tasks:
# QuantResourceWatch : SYSTEM, every 2 min  - memory / commit / disk sample + private alerts (resource_watch.py)
# QuantSessionCheck  : Administrator interactive, 08:30 Mon-Fri and 21:00 daily - read-only Choice session probe (session_check.py)
# QuantHousekeeping  : SYSTEM, daily 22:40   - whitelisted cleanup with retention (housekeeping.py --apply)
$root = 'C:\ChoiceBridge\quant\current'; $gru = 'C:\ChoiceBridge\runtime\gru-venv\Scripts\python.exe'; $exe = 'C:\ChoiceBridge\runtime\choice-venv\Scripts\python.exe'
$sys = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$ia = New-ScheduledTaskPrincipal -UserId Administrator -LogonType Interactive -RunLevel Highest
$a1 = New-ScheduledTaskAction -Execute $gru -Argument "-X utf8 `"$root\实盘日更\tools\resource_watch.py`"" -WorkingDirectory 'C:\ChoiceBridge\live'
$t1 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 2) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName 'QuantResourceWatch' -Action $a1 -Trigger $t1 -Principal $sys -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew -StartWhenAvailable) -Force | Out-Null
$a2 = New-ScheduledTaskAction -Execute $exe -Argument "-X utf8 `"$root\实盘日更\tools\session_check.py`"" -WorkingDirectory 'C:\ChoiceBridge\staging'
$t2a = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 08:30
$t2b = New-ScheduledTaskTrigger -Daily -At 21:00
Register-ScheduledTask -TaskName 'QuantSessionCheck' -Action $a2 -Trigger @($t2a, $t2b) -Principal $ia -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew -StartWhenAvailable) -Force | Out-Null
$a3 = New-ScheduledTaskAction -Execute $gru -Argument "-X utf8 `"$root\实盘日更\tools\housekeeping.py`" --apply" -WorkingDirectory 'C:\ChoiceBridge\live'
$t3 = New-ScheduledTaskTrigger -Daily -At 22:40
Register-ScheduledTask -TaskName 'QuantHousekeeping' -Action $a3 -Trigger $t3 -Principal $sys -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew -StartWhenAvailable) -Force | Out-Null
Get-ScheduledTask -TaskName QuantResourceWatch,QuantSessionCheck,QuantHousekeeping | ForEach-Object { $i = Get-ScheduledTaskInfo -TaskName $_.TaskName; '{0} | {1} | next={2}' -f $_.TaskName, $_.State, $i.NextRunTime }
