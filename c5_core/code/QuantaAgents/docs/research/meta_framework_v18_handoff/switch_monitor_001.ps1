$ErrorActionPreference = 'Stop'
$taskProject = (Get-Location).Path
$taskOut = Join-Path $taskProject 'experiment_traces/meta_diagnostic_monitor_v18'
if (Test-Path -LiteralPath $taskOut) { throw 'Monitor transition directory already exists; inspect saved state before any retry' }
$taskSource = Join-Path $taskProject 'experiment_traces/meta_ashare_revision18/src/quanta_agents/meta/diagnostic_monitor.py'
if ((Get-FileHash -LiteralPath $taskSource).Hash.ToLowerInvariant() -ne '038f660f7530d583673d389531a7999f0d3e1cf9351efe7694131ccf4e41860c') { throw 'Monitor source changed' }
$taskAccepted = Join-Path $taskProject 'experiment_traces/meta_ashare_revision18/validation_artifacts/bounded_engineering_acceptance_001/receipt.json'
if ((Get-FileHash -LiteralPath $taskAccepted).Hash.ToLowerInvariant() -ne '58ed0c0c061de7de8781004af6a32c9062ec0c0f9e889d67e23189a429195b75') { throw 'Engineering evidence changed' }
$taskParent = Get-CimInstance Win32_Process -Filter 'ProcessId=95476'
$taskChild = Get-CimInstance Win32_Process -Filter 'ProcessId=59068'
foreach ($taskProcess in @($taskParent,$taskChild)) {
    if ($null -eq $taskProcess -or $taskProcess.CreationDate.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss') -ne '2026-09-07T03:02:02' -or -not $taskProcess.CommandLine.Contains('experiment_traces/meta_ashare_revision17/scripts/serve_diagnostic_monitor.py --port 8776')) { throw 'Prior task monitor identity differs' }
}
if ($taskChild.ParentProcessId -ne $taskParent.ProcessId) { throw 'Prior parent-child binding differs' }
$taskListeners = @(Get-NetTCPConnection -LocalPort 8776 -State Listen)
if ($taskListeners.Count -ne 1 -or $taskListeners[0].OwningProcess -ne 59068) { throw 'Prior listener ownership differs' }
[System.IO.Directory]::CreateDirectory($taskOut) | Out-Null
$taskIntent = @{ at=(Get-Date).ToUniversalTime().ToString('o'); operation='Replace only this task verified v17 monitor on same port with accepted v18 monitor'; prior_processes=@($taskParent,$taskChild) | Select-Object ProcessId,ParentProcessId,CreationDate,CommandLine; port=8776; next_source_sha256='038f660f7530d583673d389531a7999f0d3e1cf9351efe7694131ccf4e41860c'; paid_dispatch_authorized=$false; untouched_denied_legacy_ports=@(8769,8773) }
[System.IO.File]::WriteAllText((Join-Path $taskOut 'transition_intent.json'),($taskIntent | ConvertTo-Json -Depth 10),[System.Text.UTF8Encoding]::new($false))
Stop-Process -Id 95476,59068
$taskProcess = Start-Process -FilePath (Join-Path $taskProject '.venv/Scripts/python.exe') -ArgumentList @('experiment_traces/meta_ashare_revision18/scripts/serve_diagnostic_monitor.py','--port','8776') -WorkingDirectory $taskProject -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskOut 'stdout.log') -RedirectStandardError (Join-Path $taskOut 'stderr.log') -PassThru
$taskStarted = @{ started_at=(Get-Date).ToUniversalTime().ToString('o'); parent_pid=$taskProcess.Id; port=8776; source='experiment_traces/meta_ashare_revision18/src/quanta_agents/meta/diagnostic_monitor.py'; source_sha256='038f660f7530d583673d389531a7999f0d3e1cf9351efe7694131ccf4e41860c'; source_inventory_hash='a44654292035a10cc380b90acaf24d56fb6da689de4fbab2fa16d3a00968885f'; independent_review_sha256='c98ae4e25b90e873b0627afe30f68f019a2a9d1c5540fc557a7333f25ef5535f'; paid_dispatch_authorized=$false }
[System.IO.File]::WriteAllText((Join-Path $taskOut 'start.json'),($taskStarted | ConvertTo-Json -Depth 8),[System.Text.UTF8Encoding]::new($false))
$taskStarted | ConvertTo-Json -Depth 8
