param(
    [Parameter(Mandatory=$true)][string]$Config,
    [Parameter(Mandatory=$true)][string]$Python,
    [switch]$EnableChoice,
    [string]$TaskPrefix = 'QuantAgent-PCF563030'
)
$ErrorActionPreference = 'Stop'
$configPath = (Resolve-Path -LiteralPath $Config).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$worker = Join-Path $PSScriptRoot 'Run-PcfJob.ps1'
foreach ($value in @($configPath,$pythonPath,$worker)) {
    if ($value.Contains('"')) { throw 'A path contains an unsupported quote.' }
}
if ((Get-TimeZone).BaseUtcOffset.TotalHours -ne 8) {
    throw 'These task times require a UTC+08 Windows time zone; the Python daemon uses China time explicitly.'
}
$cfg = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$mode = 'paper'
if ($EnableChoice) {
    if (-not ($cfg.enable_choice_orders -and $cfg.exclusive_account -and $cfg.choice_contract.validated_with_authorized_test)) {
        throw 'Choice execution requires a validated response contract and explicit dedicated-account configuration.'
    }
    $mode = 'choice'
}
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
# No restart-on-failure and no missed-morning catch-up: order uncertainty needs reconciliation.
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 110)
foreach ($job in @(@{Name='Close'; Command='close'; At='16:00'}, @{Name='Execute'; Command='execute'; At='09:35'})) {
    $taskName = $TaskPrefix + '-' + $job.Name
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        throw "Task already exists: $taskName. Inspect or remove it explicitly before reinstalling."
    }
}
foreach ($job in @(@{Name='Close'; Command='close'; At='16:00'}, @{Name='Execute'; Command='execute'; At='09:35'})) {
    $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "' + $worker + '" -Config "' + $configPath + '" -Python "' + $pythonPath + '" -Job ' + $job.Command + ' -Mode ' + $mode
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
    $trigger = New-ScheduledTaskTrigger -Daily -At $job.At
    Register-ScheduledTask -TaskName ($TaskPrefix + '-' + $job.Name) -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
}
Write-Output ('Installed after-close archive and next-session ' + $mode + ' execution tasks. No task was run by this installer.')
