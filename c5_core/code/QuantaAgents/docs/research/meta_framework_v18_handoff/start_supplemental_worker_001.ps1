$ErrorActionPreference = 'Stop'
$taskProject = (Get-Location).Path
$taskCampaign = Join-Path $taskProject 'experiment_traces/meta_casebank_references/campaigns/reference_v15_original4_001'
$taskOut = Join-Path $taskCampaign 'v18_worker_001'
if (Test-Path -LiteralPath $taskOut) { throw 'Worker intent already exists; no duplicate launch' }
$taskScope = Join-Path $taskProject 'experiment_traces/meta_casebank_supplemental_preparations/reference_v15_original4_001_v18s1_scope_001/scope.json'
$taskAdmission = Join-Path $taskProject 'docs/research/meta_framework_v18_handoff/supplemental_dispatch_admission_001.json'
$taskRegistration = Join-Path $taskCampaign 'v18_supplemental_registration_001/receipt.json'
$taskExecutable = 'C:/Users/xiezh/AppData/Local/OpenAI/Codex/bin/27d6a192e9c98618/codex.exe'
$taskPins = @{
 $taskScope='8d60a4fdef3d94c4f952edb721f9d620c62a17f51c760e2b67a4e9ed9beef66b';
 $taskAdmission='e57a5ed310ea7d8abd3fa43177fe0e07e1797d7665f00387c52cb8b5f230a163';
 $taskRegistration='74208e577ba4871486dd53a4f71b9ee7071836b23dbfdef7c4c06e878f6c7a04';
 $taskExecutable='a1cf6360ca71918d5466bc3a32d9f18b7044c9128756d1949e715d277b88c9b6';
 (Join-Path $taskProject '.venv/Scripts/python.exe')='b2c836c52cdf063180b9ee76f67ac42946101b79ac457f3494035a67c090d961';
 (Join-Path $taskProject 'experiment_traces/meta_ashare_revision18/scripts/run_casebank_supplemental.py')='429e0d7d3b191028f32545aaeabc6007e6972583d3a3049b13fcf949d5c44da9'
}
foreach ($taskPath in $taskPins.Keys) {
 if ((Get-FileHash -LiteralPath $taskPath).Hash.ToLowerInvariant() -ne $taskPins[$taskPath]) { throw 'Launch source/evidence/binary changed' }
}
$taskScopeObject=Get-Content -LiteralPath $taskScope -Raw|ConvertFrom-Json
$taskEpoch=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0
if ($taskEpoch -lt $taskScopeObject.start_epoch -or $taskEpoch -ge $taskScopeObject.end_epoch) { throw 'The one fixed window is closed' }
$taskOtherWorkers=@(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and (($_.CommandLine -match 'run_casebank_(reference|transport_correction|continuation|supplemental)\.py' -and $_.CommandLine -match '\srun\s') -or ($_.Name -eq 'codex.exe' -and $_.CommandLine.Contains($taskCampaign))) })
if ($taskOtherWorkers.Count -ne 0) { throw 'An existing research worker must be inspected before any launch' }
[System.IO.Directory]::CreateDirectory($taskOut)|Out-Null
$taskArgs=@('-B','experiment_traces/meta_ashare_revision18/scripts/run_casebank_supplemental.py','run','--campaign-root',$taskCampaign,'--scope',$taskScope,'--scope-sha256',$taskPins[$taskScope],'--admission',$taskAdmission,'--admission-sha256',$taskPins[$taskAdmission],'--executable',$taskExecutable)
$taskIntent=@{at=(Get-Date).ToUniversalTime().ToString('o');controller_thread_id='01a0774d-4327-7701-948f-2796de6fccd3';phase_id='v18s1';scope_sha256=$taskPins[$taskScope];admission_sha256=$taskPins[$taskAdmission];registration_receipt_sha256=$taskPins[$taskRegistration];model='gpt-6-astra';reasoning_effort='xhigh';start_epoch=$taskScopeObject.start_epoch;end_epoch=$taskScopeObject.end_epoch;arguments=$taskArgs;binary_and_evidence_pins=$taskPins;prior_matching_workers=0;duplicate_launch_authorized=$false;unknown_retry_authorized=$false;original_stage_status='expired_incomplete';independent_task_increment=0;formal_target_success=$false}
[System.IO.File]::WriteAllText((Join-Path $taskOut 'intent.json'),($taskIntent|ConvertTo-Json -Depth 10),[System.Text.UTF8Encoding]::new($false))
$taskWorker=Start-Process -FilePath (Join-Path $taskProject '.venv/Scripts/python.exe') -ArgumentList $taskArgs -WorkingDirectory $taskProject -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskOut 'stdout.log') -RedirectStandardError (Join-Path $taskOut 'stderr.log') -PassThru
$taskStarted=@{started_at=(Get-Date).ToUniversalTime().ToString('o');parent_pid=$taskWorker.Id;phase_id='v18s1';scope_sha256=$taskPins[$taskScope];admission_sha256=$taskPins[$taskAdmission];intent_sha256=(Get-FileHash -LiteralPath (Join-Path $taskOut 'intent.json')).Hash.ToLowerInvariant();executable=$taskExecutable;fixed_end_epoch=$taskScopeObject.end_epoch;launch_count=1;completion_verified=$false}
[System.IO.File]::WriteAllText((Join-Path $taskOut 'start.json'),($taskStarted|ConvertTo-Json -Depth 8),[System.Text.UTF8Encoding]::new($false))
$taskStarted|ConvertTo-Json -Depth 8
