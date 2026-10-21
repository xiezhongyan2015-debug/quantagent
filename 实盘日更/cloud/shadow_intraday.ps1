# Intraday SHADOW run (2026-10-15): score today's -Slot (11 = 11:30 bar, 14 = 14:00 bar) with all ensembles and write a
# same-day shadow target (V5 + tilt, blend of all15 GRU z and yesterday's c5 z).  Nothing is released or armed; the live
# 16:30 pipeline later regenerates every shared file from the full day.  Schedule: 11:36 (slot 11) / 14:06 (slot 14).
# Fetch caches are PER SLOT (sina_<Day>_s<Slot> etc., 2026-10-16): the fetchers skip files that already exist, so sharing the
# pipeline's sina_<Day> directory made the 14:06 run reuse 11:39 minutes (zero-volume fill after 13:00 -> no 14:00 scores) and
# would have fed the 16:30 live run stale minutes.  The pipeline's housekeeping (keep newest 3 sina_*) also clears these.
# Log: C:\ChoiceBridge\live\logs\shadow_<Day>_slot<Slot>.log ; outputs: <root>\实盘日更\outputs\shadow_intraday\<Day>\slot<Slot>\
param([string]$Day = '', [int]$Slot = 11, [switch]$SkipChain)   # -SkipChain: reuse today's z files, redo only blend + targets
$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'
$root = 'C:\ChoiceBridge\quant\current'; $rootU = $root -replace '\\', '/'
$gru = 'C:\ChoiceBridge\runtime\gru-venv\Scripts\python.exe'; $bash = 'C:\Program Files\Git\bin\bash.exe'
if (-not $Day) { $Day = (Get-Date).ToString('yyyy-MM-dd') }
New-Item -ItemType Directory -Force -Path C:\ChoiceBridge\live\logs | Out-Null
$log = "C:\ChoiceBridge\live\logs\shadow_${Day}_slot$Slot.log"; Start-Transcript -Path $log -Append | Out-Null
function Step($name, $block) { Write-Output "===== $name $(Get-Date -Format 'HH:mm:ss')"; & $block; if ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) { throw "step failed: $name (exit $LASTEXITCODE)" } }
try {
  $cal = Get-Content "$root\实盘日更\config\calendar_2026.json" -Raw | ConvertFrom-Json
  if ($cal -notcontains $Day) { Write-Output "NOT A TRADING DAY: $Day"; Stop-Transcript | Out-Null; exit 0 }
  $Prev = ($cal | Where-Object { $_ -lt $Day } | Select-Object -Last 1); $T8 = $Day -replace '-', ''; $P8 = $Prev -replace '-', ''
  $out = "$root\实盘日更\outputs\shadow_intraday\$Day\slot$Slot"; New-Item -ItemType Directory -Force -Path $out | Out-Null
  Set-Location $root
  Step 'PCF of today (published before the open) + valuation prices' { & $gru -I "实盘日更\tools\fetch_pcf.py" $Day; & $gru -I "实盘日更\tools\make_prices.py" "实盘日更\inputs\pcf\563030_$T8.xml" }
  $c5 = "$rootU/实盘日更/inputs/c5/c5_state_$P8.parquet"   # yesterday's c5 state is what an intraday rebalance can know
  if ($SkipChain) { Write-Output "===== chain skipped (-SkipChain) $(Get-Date -Format 'HH:mm:ss')" } else { Step "GRU chain: fetch + hourly + daily + infer (15 models), signals at slot $Slot" {
    & $bash -lc "set -o pipefail; cd '$rootU' && export PY=C:/ChoiceBridge/runtime/gru-venv/Scripts/python.exe PYTHONUTF8=1 && D=gru_research/trading_data/future_minute_probe && export EXT_SINA=`$D/sina_${Day}_s$Slot/raw EXT_TENCENT=`$D/tencent_daily_${Day}_s$Slot/raw EXT_QT=`$D/tencent_qt_${Day}_s$Slot && mkdir -p gru_live_extend_20261012/fetch && `$PY -I gru_live_extend_20261012/fetch/fetch_sina_1min.py `$EXT_SINA | tail -1 && `$PY -I gru_live_extend_20261012/fetch/fetch_tencent_daily.py `$EXT_TENCENT $Day | tail -1 && `$PY -I gru_live_extend_20261012/fetch/fetch_qt.py `$EXT_QT && `$PY gru_live_extend_20261012/hourly_extend.py | tail -1 && `$PY gru_live_extend_20261012/daily_extend.py | tail -1 && `$PY gru_live_extend_20261012/assemble_infer.py | tail -1 && for ENS in ctrl5 all15 ten; do `$PY gru_live_extend_20261012/signals_ext.py $Slot `$ENS | tail -1; done 2>&1 | tail -12"
  }
  }
  Step 'blend with yesterday c5 + same-day shadow targets (all15 and ctrl5)' {
    & $bash -lc "set -o pipefail; cd '$rootU' && export PY=C:/ChoiceBridge/runtime/gru-venv/Scripts/python.exe PYTHONUTF8=1 && for SUF in slot${Slot}_all15 slot$Slot; do `$PY gru_live_extend_20261012/blend_signal.py $Day '$c5' `$SUF $Prev | tail -1; done"
    $val = (& $gru -c "import xml.etree.ElementTree as ET; r=ET.parse(r'$root\实盘日更\inputs\pcf\563030_$T8.xml').getroot(); d=[x.text for x in r if x.tag=='PreTradingDay'][0]; print(f'{d[:4]}-{d[4:6]}-{d[6:]}')").Trim()
    foreach ($suf in @("slot${Slot}_all15", "slot$Slot")) {
      $sig = "$root\outputs\gru_live_extend_20261012\signals\blend_${Day}_$suf.csv"; if ($suf -eq 'slot15') { $sig = "$root\outputs\gru_live_extend_20261012\signals\blend_$Day.csv" }
      $o = "$out\$suf"; New-Item -ItemType Directory -Force -Path $o | Out-Null
      & $gru "pcf_overlay_20261012\live_targets.py" --pcf "实盘日更\inputs\pcf\563030_$T8.xml" --prices "实盘日更\inputs\prices\close_$($val -replace '-','').csv" --prices-date $val --calendar "实盘日更\config\calendar_2026.json" --console-config "实盘日更\config\console_config_target_file.json" --members "实盘日更\config\index_membership_20260917.parquet" --signal-date $Day --exec-date $Day --out $o --mode V5_tilt --c5 ($c5 -replace '/','\') --signal $sig --shadow --same-day --c5-date $Prev --refill industry --industry-map "实盘日更\config\industry_sw_l1_20260924.json" --max-turnover 0.7
      Write-Output "shadow target $suf exit $LASTEXITCODE"
    }
    $global:LASTEXITCODE = 0
  }
  Write-Output "SHADOW DONE $Day slot $Slot $(Get-Date -Format 'HH:mm:ss')"
} catch { Write-Output "SHADOW FAILED: $($_.Exception.Message)" }
finally { Stop-Transcript | Out-Null }
