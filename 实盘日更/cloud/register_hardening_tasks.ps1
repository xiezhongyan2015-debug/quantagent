# 2026-10-16 hardening tasks (run once as Administrator in the interactive session; re-running replaces them).
# QuantPreflight  : 09:05 and 09:18, trading days - release/target/PCF present, armed process alive (re-arm if not), Choice running, expected dividends, disk.
# QuantAutoResume : every 10 min 09:36-13:36 - benign pre-trade stops are re-released and re-armed (max 2/day), everything else is reported only.
$ia = New-ScheduledTaskPrincipal -UserId Administrator -LogonType Interactive -RunLevel Highest
$root = 'C:\ChoiceBridge\quant\current'; $py = 'C:\ChoiceBridge\runtime\choice-venv\Scripts\python.exe'
$a1 = New-ScheduledTaskAction -Execute $py -Argument "-X utf8 $root\实盘日更\tools\preflight.py" -WorkingDirectory 'C:\ChoiceBridge\executor'
$t1a = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 09:05
$t1b = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 09:18
Register-ScheduledTask -TaskName 'QuantPreflight' -Action $a1 -Trigger @($t1a, $t1b) -Principal $ia -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew) -Force | Out-Null
$a2 = New-ScheduledTaskAction -Execute $py -Argument "-X utf8 $root\实盘日更\tools\auto_resume.py" -WorkingDirectory 'C:\ChoiceBridge\executor'
$t2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 09:36
$rep = New-ScheduledTaskTrigger -Once -At 09:36 -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Hours 4); $t2.Repetition = $rep.Repetition
Register-ScheduledTask -TaskName 'QuantAutoResume' -Action $a2 -Trigger $t2 -Principal $ia -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew) -Force | Out-Null
Get-ScheduledTask | Where-Object { $_.TaskName -in 'QuantPreflight','QuantAutoResume' } | Select-Object TaskName, State
