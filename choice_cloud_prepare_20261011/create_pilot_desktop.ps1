$ErrorActionPreference='Stop'
$p='C:\ChoiceBridge\runtime\choice-venv\Scripts\pythonw.exe'
$a='C:\ChoiceBridge\executor\research\choice_pcf_automation\console_app.py'
$desktop=[Environment]::GetFolderPath('Desktop')
$ws=New-Object -ComObject WScript.Shell
$link=$ws.CreateShortcut((Join-Path $desktop '一手试单控制台.lnk'))
$link.TargetPath=$p
$link.Arguments='-X utf8 "'+$a+'"'
$link.WorkingDirectory='C:\ChoiceBridge\executor'
$link.Description='10月12日单只100股试单；打开不会自动交易'
$link.Save()
$task='ChoicePilotPreview20261011'
$action=New-ScheduledTaskAction -Execute $p -Argument ('-X utf8 "'+$a+'"') -WorkingDirectory 'C:\ChoiceBridge\executor'
$principal=New-ScheduledTaskPrincipal -UserId 'Administrator' -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 24) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $task -Action $action -Principal $principal -Settings $settings -Description 'Display prepared pilot console; no automatic start or order' -Force | Out-Null
Start-ScheduledTask -TaskName $task
[pscustomobject]@{shortcut=(Join-Path $desktop '一手试单控制台.lnk');task=$task;auto_start=$false;orders_submitted=0}|ConvertTo-Json -Compress
