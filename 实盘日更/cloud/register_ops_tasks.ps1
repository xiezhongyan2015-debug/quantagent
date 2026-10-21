# 2026-10-13 ops hardening. Adds/replaces only: QuantMonitor (now every 2 min), QuantDailyRetry (17:50 / 19:20, -IfNotDone), QuantDeadman (SYSTEM, every 10 min).
# Execution, corporate-action, dispatch and the main 16:30 pipeline tasks are untouched.
$ErrorActionPreference = 'Stop'
$root = 'C:\ChoiceBridge\quant\current'; $choice = 'C:\ChoiceBridge\runtime\choice-venv\Scripts\python.exe'
$ia = New-ScheduledTaskPrincipal -UserId Administrator -LogonType Interactive -RunLevel Highest
$sys = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
# 1) intraday monitor 09:20-15:20 Mon-Fri, every 2 minutes (was 10)
$a = New-ScheduledTaskAction -Execute $choice -Argument "$root\实盘日更\tools\monitor.py" -WorkingDirectory $root
$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 09:20
$t.Repetition = (New-ScheduledTaskTrigger -Once -At 09:20 -RepetitionInterval (New-TimeSpan -Minutes 2) -RepetitionDuration (New-TimeSpan -Hours 6)).Repetition
Register-ScheduledTask -TaskName 'QuantMonitor' -Action $a -Trigger $t -Principal $ia -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew) -Force | Out-Null
# 2) pipeline retry: does nothing when the 16:30 run is still running or T+1 is already released and armed
$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File $root\实盘日更\cloud\daily_cloud.ps1 -IfNotDone" -WorkingDirectory $root
$t1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 17:50
$t2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 19:20
Register-ScheduledTask -TaskName 'QuantDailyRetry' -Action $a -Trigger @($t1, $t2) -Principal $ia -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew -StartWhenAvailable) -Force | Out-Null
# 3) independent heartbeat watchdog as SYSTEM (works even when Administrator is logged off)
$a = New-ScheduledTaskAction -Execute $choice -Argument "$root\实盘日更\tools\deadman.py" -WorkingDirectory $root
$t = New-ScheduledTaskTrigger -Daily -At 00:05
$t.Repetition = (New-ScheduledTaskTrigger -Once -At 00:05 -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Minutes 1435)).Repetition
Register-ScheduledTask -TaskName 'QuantDeadman' -Action $a -Trigger $t -Principal $sys -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew -StartWhenAvailable) -Force | Out-Null
Get-ScheduledTask -TaskName QuantMonitor,QuantDailyRetry,QuantDeadman | ForEach-Object { $i = Get-ScheduledTaskInfo -TaskName $_.TaskName; '{0} | {1} | next={2}' -f $_.TaskName, $_.State, $i.NextRunTime }
