# Unattended daily pipeline on the cloud machine (scheduled 16:05 Mon-Fri as Administrator, interactive logon).
# Steps: account snapshot -> PCF + valuation prices -> c5 states -> GRU chain -> target file (gates) -> sealed release -> arm executor for T+1.
# Any failure stops the pipeline before arming; the executor then has no plan for T+1 (nothing trades). Log: C:\ChoiceBridge\live\logs\pipeline_<T>.log
# -IfNotDone (task QuantDailyRetry, 17:50 and 19:20): skip if the main run is still running or T+1 is already released and armed; re-arm only if released but not armed; otherwise rerun the whole pipeline.
param([string]$Day = '', [string]$Mode = 'V5_tilt', [switch]$SkipC5, [switch]$SkipGru, [switch]$NoArm, [switch]$IfNotDone)
$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'
$root = 'C:\ChoiceBridge\quant\current'; $rootU = $root -replace '\\', '/'
$gru = 'C:\ChoiceBridge\runtime\gru-venv\Scripts\python.exe'; $c5py = 'C:\ChoiceBridge\runtime\c5-venv\Scripts\python.exe'; $exe = 'C:\ChoiceBridge\runtime\choice-venv\Scripts\python.exe'
$bash = 'C:\Program Files\Git\bin\bash.exe'
if (-not $Day) { $Day = (Get-Date).ToString('yyyy-MM-dd') }
New-Item -ItemType Directory -Force -Path C:\ChoiceBridge\live\logs | Out-Null
$log = "C:\ChoiceBridge\live\logs\pipeline_$Day.log"; Start-Transcript -Path $log -Append | Out-Null
function Step($name, $block) { Write-Output "===== $name $(Get-Date -Format 'HH:mm:ss')"; & $block; if ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) { throw "step failed: $name (exit $LASTEXITCODE)" } }
function Assert-NativeSuccess($name) { if ($LASTEXITCODE -ne 0) { throw "command failed: $name (exit $LASTEXITCODE)" } }
# 2026-10-17: read-only timing dashboard for the daily report. Never throws, never blocks the pipeline; output in C:\ChoiceBridge\live\timing.
function Timing-Dashboard($d) {
  $ErrorActionPreference = 'Continue'   # local scope: python warnings on stderr must not turn into terminating errors
  try { Write-Output "===== timing dashboard (read-only) $(Get-Date -Format 'HH:mm:ss')"
    & $gru "$root\timing_research_20261017\dashboard.py" $d --cache C:\ChoiceBridge\live\timing\cache --out C:\ChoiceBridge\live\timing --external C:\ChoiceBridge\live\timing\xy_external_log.csv --shadow-dir C:\ChoiceBridge\live\timing\beta_shadow --industry "$root\实盘日更\config\industry_sw_l1_20260924.json" 2>&1 | Select-Object -Last 10 | ForEach-Object { "$_" }
    Write-Output "timing dashboard exit $LASTEXITCODE" } catch { Write-Output "timing dashboard skipped: $($_.Exception.Message)" }
  $global:LASTEXITCODE = 0 }
# 2026-10-19: the beta tilt is LIVE. It runs inside daily.sh (--beta-tilt) right after the untilted target passes the gates and BEFORE
# the audit and the sealed release; any failure keeps the untilted target. The old post-arm shadow step (Beta-Shadow) is removed:
# running it on a released (tilted) target would tilt twice and overwrite the records (beta_tilt_shadow.py now refuses that anyway).
try {
  $cal = Get-Content "$root\实盘日更\config\calendar_2026.json" -Raw | ConvertFrom-Json
  if ($cal -notcontains $Day) { Write-Output "NOT A TRADING DAY: $Day"; Stop-Transcript | Out-Null; exit 0 }
  $Next = ($cal | Where-Object { $_ -gt $Day } | Select-Object -First 1); $T8 = $Day -replace '-', ''
  if ($IfNotDone) {
    $mainTask = Get-ScheduledTask -TaskName 'QuantDaily_1605' -ErrorAction SilentlyContinue
    if ($mainTask -and $mainTask.State -eq 'Running') { Write-Output "RETRY SKIPPED: main pipeline still running"; Stop-Transcript | Out-Null; exit 0 }
    if (Test-Path "C:\ChoiceBridge\live\release_receipt_$Next.json") {
      $st = & $exe C:\ChoiceBridge\staging\arm.py --status | ConvertFrom-Json
      if ($st.active -and $st.status.execution_date -eq $Next) { Write-Output "RETRY SKIPPED: $Next already released and armed"; Stop-Transcript | Out-Null; exit 0 }
      Write-Output "RETRY: $Next released but not armed -> arm only"
      Step 'stale lock review (only a dead waiting run without ledger is cleared)' { & $exe C:\ChoiceBridge\staging\clear_stale_lock.py }
      Step 'arm executor (= pressing Start, for T+1 09:30)' { & $exe C:\ChoiceBridge\staging\arm.py }
      Timing-Dashboard $Day
      Step 'daily report + push' { & $gru "实盘日更\tools\daily_report.py" $Day --next $Next }
      Write-Output "PIPELINE OK (re-armed) $Day -> $Next $(Get-Date -Format 'HH:mm:ss')"; Stop-Transcript | Out-Null; exit 0
    }
    Write-Output "RETRY: no release for $Next yet -> full pipeline"
  }
  Step 'account snapshot (read-only)' { Set-Location C:\ChoiceBridge\staging; & $exe captain_account_readonly.py --refresh --account 000000000000000000 --out "C:\ChoiceBridge\live\captain_snapshot_$Day.json" | Out-Null; Assert-NativeSuccess 'account snapshot'; Set-Location $root
    & $gru "实盘日更\tools\make_account.py" "C:\ChoiceBridge\live\captain_snapshot_$Day.json" $Day }
  Step 'PCF + valuation prices' { & $gru -I "实盘日更\tools\fetch_pcf.py" $Day; Assert-NativeSuccess 'PCF fetch'; & $gru -I "实盘日更\tools\make_prices.py" "实盘日更\inputs\pcf\563030_$T8.xml" }
  if (-not $SkipC5) { Step 'c5 states (teammate chain, our run)' { & $bash -lc "set -o pipefail; cd /c/ChoiceBridge/c5_stage_20261012/payload/c5_core/code/QuantaAgents && export PY=/c/ChoiceBridge/runtime/c5-venv/Scripts/python.exe PYTHONUTF8=1 && bash scripts/c5_daily.sh --ffill-limit 15 --sync $rootU/实盘日更/inputs/c5 2>&1 | tail -5"
      if (-not (Test-Path "$root\实盘日更\inputs\c5\c5_state_$T8.parquet")) { throw "c5 state for $Day missing" } } }
  if (-not $SkipGru) { Step 'GRU chain (public data)' { & $bash -lc "set -o pipefail; cd '$rootU' && export PY=C:/ChoiceBridge/runtime/gru-venv/Scripts/python.exe PYTHONUTF8=1 && bash gru_live_extend_20261012/run_chain.sh $Day '$rootU/实盘日更/inputs/c5/c5_state_$T8.parquet' 2>&1 | tail -3" } }
  # 2026-10-19: all15 signal + industry refill are live (user approved 10-18). Declared layer-switch days (turnover gate 0.70) are listed in
  # C:\ChoiceBridge\live\switch_days.json as signal dates, e.g. ["2026-10-19"]; every other day keeps the 0.40 gate.
  $switchArg = ''; $swf = 'C:\ChoiceBridge\live\switch_days.json'
  if ((Test-Path $swf) -and (@(Get-Content $swf -Raw | ConvertFrom-Json) -contains $Day)) { $switchArg = '--switch-day'; Write-Output "declared switch day $Day (turnover gate 0.70)" }
  Step 'target file (gates)' { & $bash -lc "set -o pipefail; cd '$rootU' && export PY=C:/ChoiceBridge/runtime/gru-venv/Scripts/python.exe PYTHONUTF8=1 && bash ./实盘日更/daily.sh $Day --mode $Mode --skip-gru --ens all15 --refill industry $switchArg --beta-tilt C:/ChoiceBridge/live/timing/beta_shadow --beta-cache C:/ChoiceBridge/live/timing/cache 2>&1 | tail -6"
    if (-not (Test-Path "$root\实盘日更\outputs\targets\$Next\target_$Next.json")) { throw "no released target for $Next" } }
  Step 'data audit (PCF / c5 / minutes / daily / GRU / target)' { & $gru "实盘日更\tools\data_audit.py" $Day $Next }
  Step 'sealed release for the executor' { & $exe C:\ChoiceBridge\staging\release_day.py --signal $Day --exec $Next --target "$root\实盘日更\outputs\targets\$Next\target_$Next.json" --account "C:\ChoiceBridge\live\captain_snapshot_$Day.json" --pcf "$root\实盘日更\inputs\pcf\563030_$T8.xml" }
  if (-not $NoArm) { Step 'stale lock review (only a dead waiting run without ledger is cleared)' { & $exe C:\ChoiceBridge\staging\clear_stale_lock.py }
    Step 'arm executor (= pressing Start, for T+1 09:30)' { & $exe C:\ChoiceBridge\staging\arm.py } }
  Timing-Dashboard $Day
  Step 'daily report + push' { & $gru "实盘日更\tools\daily_report.py" $Day --next $Next }
  # 2026-10-14: raw Sina minute folders are ~550 MB/day and already merged into minute_store; keep the 3 newest.
  try { Get-ChildItem "$root\gru_research\trading_data\future_minute_probe" -Directory -Filter 'sina_*' | Sort-Object Name -Descending | Select-Object -Skip 3 | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force; Write-Output "housekeeping: removed $($_.Name)" } } catch { Write-Output "housekeeping skipped: $($_.Exception.Message)" }
  Write-Output "PIPELINE OK $Day -> $Next $(Get-Date -Format 'HH:mm:ss')"
} catch {
  $pipelineFailure = $_.Exception.Message
  Write-Output "PIPELINE FAILED: $pipelineFailure"
  try { & $exe C:\ChoiceBridge\executor\research\choice_pcf_automation\share_units.py --pipeline-alert $Day $pipelineFailure; Assert-NativeSuccess 'pipeline alert queue' } catch { Write-Output "PIPELINE ALERT QUEUE FAILED: $($_.Exception.Message)" }
  try { & $gru "$root\实盘日更\tools\daily_report.py" $Day --next $Next --private } catch {}
  Stop-Transcript | Out-Null; exit 2
}
Stop-Transcript | Out-Null
