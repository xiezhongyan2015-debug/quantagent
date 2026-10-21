# 2026-10-19 晚，用户指令：把已封存的 10-20 旧目标换成骨架 v5 目标，10-19 记为切换日（换手闸门 0.70）。
# 顺序：备份当前封存状态 -> 重新取估值价（含零股数行）-> daily.sh 生成新目标（含 beta 倾斜）-> 数据审核 -> 重新封存 -> 锁检查 -> 预约（升级：停掉旧的等待进程，按新目标重新预约）
# 任何一步失败都停在原地并记日志；新目标放行之前，旧的封存和预约都不动（旧预约照常在 10-20 09:30 执行旧目标）。
$ProgressPreference='SilentlyContinue'; $env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'
$Day='2026-10-19'; $Next='2026-10-20'; $T8='20261019'
$root='C:\ChoiceBridge\quant\current'; $rootU=$root -replace '\\','/'; $gru='C:\ChoiceBridge\runtime\gru-venv\Scripts\python.exe'; $exe='C:\ChoiceBridge\runtime\choice-venv\Scripts\python.exe'; $bash='C:\Program Files\Git\bin\bash.exe'
$log='C:\ChoiceBridge\live\logs\switch_v5_20261019.log'; $bk='C:\ChoiceBridge\live\patches\switch_v5_20261019_backup'
function Log($m) { Add-Content $log ((Get-Date -Format s) + ' ' + $m) -Encoding UTF8 }
New-Item -ItemType Directory -Force -Path $bk | Out-Null
Log 'START switch to skeleton v5 for execution 2026-10-20'
$base='C:\ChoiceBridge\executor\research\choice_pcf_automation'
foreach ($f in @("$base\console\settings.json", "$base\daily_public_pcf\active_rebalance_target.json", "C:\ChoiceBridge\live\release_receipt_$Next.json")) { if (Test-Path $f) { Copy-Item $f $bk -Force } }
Copy-Item "$root\实盘日更\outputs\targets\$Next" "$bk\targets_$Next" -Recurse -Force
Log ('backup done: ' + ((Get-ChildItem $bk -Recurse -File | Measure-Object).Count) + ' files')
$st = & $exe C:\ChoiceBridge\staging\arm.py --status 2>&1 | Out-String; Log ('status before: ' + $st.Trim())
Set-Location $root
$out = & $gru -I "实盘日更\tools\make_prices.py" "实盘日更\inputs\pcf\563030_$T8.xml" 2>&1 | Out-String; Log ("make_prices exit $LASTEXITCODE " + $out.Trim())
if ($LASTEXITCODE -ne 0) { Log 'STOP: prices incomplete; old release and arm untouched'; exit 2 }
$out = & $bash -lc "set -o pipefail; cd '$rootU' && export PY=C:/ChoiceBridge/runtime/gru-venv/Scripts/python.exe PYTHONUTF8=1 && bash ./实盘日更/daily.sh $Day --mode V5_tilt --skip-gru --ens all15 --refill industry --switch-day --beta-tilt C:/ChoiceBridge/live/timing/beta_shadow --beta-cache C:/ChoiceBridge/live/timing/cache 2>&1 | tail -12" 2>&1 | Out-String; Log ("daily.sh exit $LASTEXITCODE " + $out.Trim())
$tgt="$root\实盘日更\outputs\targets\$Next\target_$Next.json"
if (-not (Test-Path $tgt)) { Log 'STOP: no new target released; restoring the old target files (old release and arm untouched)'; Copy-Item "$bk\targets_$Next\*" "$root\实盘日更\outputs\targets\$Next" -Recurse -Force; exit 3 }
$pre = Get-Content "$root\实盘日更\outputs\targets\$Next\precheck_$Next.json" -Raw -Encoding UTF8 | ConvertFrom-Json
Log ("new target: names=$($pre.names) tilt_form=$($pre.tilt_form) backfill_names=$($pre.backfill_names) csi500_nav=$($pre.csi500_share_of_nav) turnover=$($pre.planned_two_way_turnover) overlay=$($pre.overlay_l1_half)")
if ($pre.tilt_form -ne 'sqrtb' -or -not $pre.backfill_names) { Log 'STOP: new target is not skeleton v5; restoring old target files'; Copy-Item "$bk\targets_$Next\*" "$root\实盘日更\outputs\targets\$Next" -Recurse -Force; exit 4 }
$out = & $gru "实盘日更\tools\data_audit.py" $Day $Next 2>&1 | Out-String; Log ("data_audit exit $LASTEXITCODE " + $out.Trim())
if ($LASTEXITCODE -ne 0) { Log 'STOP: data audit failed; old release and arm untouched'; exit 5 }
Set-Location C:\ChoiceBridge\executor
$out = & $exe C:\ChoiceBridge\staging\release_day.py --signal $Day --exec $Next --target $tgt --account "C:\ChoiceBridge\live\captain_snapshot_$Day.json" --pcf "$root\实盘日更\inputs\pcf\563030_$T8.xml" 2>&1 | Out-String; Log ("release exit $LASTEXITCODE " + $out.Trim())
if ($LASTEXITCODE -ne 0) { Log 'STOP: release failed (old arm still waiting with the old plan)'; exit 6 }
$out = & $exe -B C:\ChoiceBridge\staging\clear_stale_lock.py 2>&1 | Out-String; Log ("lock review exit $LASTEXITCODE " + $out.Trim())
if ($LASTEXITCODE -ne 0) { Log 'STOP: lock review'; exit 7 }
$out = & $exe -B C:\ChoiceBridge\staging\arm.py 2>&1 | Out-String; Log ("arm exit $LASTEXITCODE " + $out.Trim()); $armrc=$LASTEXITCODE
$st = & $exe C:\ChoiceBridge\staging\arm.py --status 2>&1 | Out-String; Log ('status after: ' + $st.Trim())
Set-Location $root
$out = & $gru "实盘日更\tools\daily_report.py" $Day --next $Next --private 2>&1 | Out-String; Log ("private report exit $LASTEXITCODE")
Log "END armrc=$armrc"
exit $armrc
