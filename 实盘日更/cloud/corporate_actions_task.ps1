# Read-only close evidence / public pre-open cache / durable alert retry.
param([ValidateSet('close','preopen','dispatch')][string]$Mode)
$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'
$root = 'C:\ChoiceBridge\quant\current'
$exe = 'C:\ChoiceBridge\runtime\choice-venv\Scripts\python.exe'
$module = 'C:\ChoiceBridge\executor\research\choice_pcf_automation\corporate_action_runtime.py'
$day = (Get-Date).ToString('yyyy-MM-dd')
New-Item -ItemType Directory -Force -Path C:\ChoiceBridge\live\logs | Out-Null
Start-Transcript -Path "C:\ChoiceBridge\live\logs\corporate_actions_${Mode}_$day.log" -Append | Out-Null
try {
 if ($Mode -eq 'dispatch') { & $exe -B $module --dispatch; $code = $LASTEXITCODE }
 else {
  $cal = Get-Content "$root\实盘日更\config\calendar_2026.json" -Raw -Encoding UTF8 | ConvertFrom-Json
  if ($cal -notcontains $day) { Write-Output "NOT A TRADING DAY: $day"; $code = 0 }
  elseif ($Mode -eq 'preopen') { & $exe -B $module --preload $day; $code = $LASTEXITCODE }
  else {
   & $exe -B $module --close $day; $code = $LASTEXITCODE
   if ($code -eq 0) {
    $next = $cal | Where-Object { $_ -gt $day } | Select-Object -First 1
    if ($next) { & $exe -B $module --preload $next; $code = $LASTEXITCODE }
   }
  }
 }
} finally { Stop-Transcript | Out-Null }
exit $code
