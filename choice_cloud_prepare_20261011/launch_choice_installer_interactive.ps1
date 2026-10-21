$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$exe='C:\ChoiceBridge\installers\ChoiceSetup_win_x86.exe'
if((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant() -ne '5891303fa28df7437f213704c7788b2c2ab3cb551e14facd120a0e1fb420dabb'){throw 'Installer hash changed'}
$desktop=@(Get-Process explorer -ErrorAction SilentlyContinue | Select-Object Id,SessionId)
if($desktop.Count -ne 1){throw 'Expected exactly one logged-in desktop'}
$name='ChoicePrepareInstaller20261011'
if(Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue){throw 'Preparation task already exists'}
$action=New-ScheduledTaskAction -Execute $exe
$principal=New-ScheduledTaskPrincipal -UserId 'Administrator' -LogonType Interactive -RunLevel Highest
$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $name
@{kind='interactive_official_installer_launch';task=$name;desktop=$desktop;automatic_trading_started=$false}|ConvertTo-Json -Depth 4
