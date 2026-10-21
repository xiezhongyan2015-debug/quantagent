# 2026-10-15 shadow tasks (run once as Administrator in the interactive session; re-running replaces them).
# QuantShadow_1136: 11:30-bar signal, same-day shadow target (candidate for a 13:00 afternoon session).
# QuantShadow_1406: 14:00-bar signal, same-day shadow target (record only; the executor cannot start that late).
# Both write only under 实盘日更\outputs\shadow_intraday and the shared chain outputs that the 16:30 pipeline rebuilds anyway.
$ia = New-ScheduledTaskPrincipal -UserId Administrator -LogonType Interactive -RunLevel Highest
$root = 'C:\ChoiceBridge\quant\current'
foreach ($p in @(@{name='QuantShadow_1136'; at='11:36'; slot=11}, @{name='QuantShadow_1406'; at='14:06'; slot=14})) {
  $a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File $root\实盘日更\cloud\shadow_intraday.ps1 -Slot $($p.slot)" -WorkingDirectory $root
  $t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $p.at
  Register-ScheduledTask -TaskName $p.name -Action $a -Trigger $t -Principal $ia -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew) -Force | Out-Null
}
Get-ScheduledTask | Where-Object { $_.TaskName -like 'QuantShadow*' } | Select-Object TaskName, State
