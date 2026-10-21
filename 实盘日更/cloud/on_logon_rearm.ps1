# At Administrator logon (interactive): if a sealed release exists for the next session and nothing is armed, arm it and report.
$ProgressPreference='SilentlyContinue'; $env:PYTHONUTF8='1'; $exe='C:\ChoiceBridge\runtime\choice-venv\Scripts\python.exe'; $py='C:\ChoiceBridge\runtime\gru-venv\Scripts\python.exe'
Start-Sleep 30
$st = & $exe C:\ChoiceBridge\staging\arm.py --status | ConvertFrom-Json
$cfg = Get-Content C:\ChoiceBridge\executor\research\choice_pcf_automation\console\settings.json -Raw | ConvertFrom-Json
$today = (Get-Date).ToString('yyyy-MM-dd')
if (-not $st.active -and $cfg.execution_date -ge $today -and (Test-Path ("C:\ChoiceBridge\live\release_receipt_" + $cfg.execution_date + ".json"))) {
  & $exe C:\ChoiceBridge\staging\clear_stale_lock.py; $out = & $exe C:\ChoiceBridge\staging\arm.py 2>&1 | Out-String; $code = $LASTEXITCODE
  Add-Content -Path C:\ChoiceBridge\live\logs\logon_rearm.log -Value ((Get-Date -Format s) + ' exit ' + $code + ' ' + $out) -Encoding UTF8
  if ($code -eq 0) { $msg = "ℹ️ 登录后已自动恢复执行器预约：" + $cfg.execution_date + " 09:30 自动开始" } else { $msg = "⚠️ 登录后自动恢复执行器预约失败（" + $cfg.execution_date + "，退出码 " + $code + "），明天不会自动交易，请人工检查 live\logs\logon_rearm.log" }
  & $py C:\ChoiceBridge\quant\current\实盘日更\tools\notify.py $msg
} else { $state = if ($st.active) { "执行器已在运行或预约（" + $st.status.execution_date + "，" + $st.status.status + "），无需恢复" } else { "没有待恢复的预约（最近配置执行日 " + $cfg.execution_date + "）" }
  & $py C:\ChoiceBridge\quant\current\实盘日更\tools\notify.py ("ℹ️ Administrator 已登录；" + $state) }
