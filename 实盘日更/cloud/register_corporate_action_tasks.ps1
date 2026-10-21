# Add only corporate-action tasks; existing execution and daily tasks are preserved.
$ErrorActionPreference = 'Stop'
$root = 'C:\ChoiceBridge\quant\current'
$ia = New-ScheduledTaskPrincipal -UserId Administrator -LogonType Interactive -RunLevel Highest
function Add-CATask($name,$mode,$at,$minutes,$duration) {
 $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$root\实盘日更\cloud\corporate_actions_task.ps1`" -Mode $mode" -WorkingDirectory $root
 $start = (Get-Date).Date.Add([TimeSpan]::Parse($at))
 if ($mode -eq 'preopen' -and $start -le (Get-Date)) { $start = $start.AddDays(1) }
 $trigger = New-ScheduledTaskTrigger -Daily -At $start
 if ($minutes) {
  $repeat = New-ScheduledTaskTrigger -Once -At $at -RepetitionInterval (New-TimeSpan -Minutes $minutes) -RepetitionDuration (New-TimeSpan -Minutes $duration)
  $trigger.Repetition = $repeat.Repetition
 }
 $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 8) -MultipleInstances IgnoreNew -StartWhenAvailable
 Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $ia -Settings $settings -Force | Out-Null
}
# Wrapper uses the same exchange calendar; weekends / holidays do not capture.
Add-CATask 'QuantCorporateActionClose' 'close' '15:10' 10 30
Add-CATask 'QuantCorporateActionPreopen' 'preopen' '09:10' 5 15
# Alerts also retry outside market hours and on holidays; no account calls.
Add-CATask 'QuantAlertDispatch' 'dispatch' '00:00' 1 1440
Get-ScheduledTask -TaskName QuantCorporateActionClose,QuantCorporateActionPreopen,QuantAlertDispatch | Select-Object TaskName,State
