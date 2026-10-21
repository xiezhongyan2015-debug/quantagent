"""Prepare daily dataset, launch controls and adjustment-independent caches."""
from pathlib import Path
import shutil

SRC = Path(__file__).resolve().parent
DEST = Path(r'E:\中证500_历年成分股_日频_2010-2026_iFinD_后复权')
CONTROL = SRC.parent / 'data_expansion' / 'ifind_daily_hfq_control'
DEST.mkdir(parents=True, exist_ok=True)
for folder in ['audit', 'memberships', 'scripts_backup']:
    (DEST / folder).mkdir(exist_ok=True)
minute = Path(r'E:\中证500_历年成分股_1min_2010-2026_iFinD_后复权')
for file in (minute / 'memberships').glob('*.json.gz'):
    target = DEST / 'memberships' / file.name
    if not target.exists():
        shutil.copy2(file, target)
calendar = minute / 'audit' / 'trading_calendar.json'
if calendar.exists() and not (DEST / 'audit' / calendar.name).exists():
    shutil.copy2(calendar, DEST / 'audit' / calendar.name)
for name in ['ifind_api_worker.py', 'ifind_daily_worker.py', 'ifind_parallel_dispatch.py',
             'download_ifind_csi500_1min.py', 'download_ifind_csi500_daily.py', 'setup_ifind_daily.py']:
    shutil.copy2(SRC / name, DEST / 'scripts_backup' / name)
shutil.copy2(SRC / 'ifind_daily_README.md', DEST / '使用说明.md')
for name in ['000_statistics.json', '001_daily_recent.json', '002_daily_2010.json']:
    source = CONTROL / 'results' / name
    if source.exists():
        shutil.copy2(source, DEST / 'audit' / name)
if (CONTROL / 'runtime_config.json').exists():
    shutil.copy2(CONTROL / 'runtime_config.json', DEST / 'audit' / 'runtime_config_at_setup.json')

pause = f"""New-Item -ItemType File -Path (Join-Path $PSScriptRoot 'STOP') -Force | Out-Null
New-Item -ItemType File -Path '{CONTROL / 'STOP_WORKER'}' -Force | Out-Null
Write-Host '已请求暂停日频下载，断点保留，已发出的请求会收尾。'
"""
resume = f"""$ErrorActionPreference = 'Stop'
$control = '{CONTROL}'
$statusFile = Join-Path $PSScriptRoot 'progress.json'
if (Test-Path -LiteralPath $statusFile) {{
    $saved = Get-Content -LiteralPath $statusFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($saved.pid -and (Get-Process -Id $saved.pid -ErrorAction SilentlyContinue)) {{
        Write-Host '日频下载进程仍在运行。'
        exit
    }}
}}
foreach ($marker in @((Join-Path $PSScriptRoot 'STOP'), (Join-Path $control 'STOP_WORKER'))) {{
    if (Test-Path -LiteralPath $marker) {{ Remove-Item -LiteralPath $marker }}
}}
New-Item -ItemType File -Path (Join-Path $control 'RESET_ERRORS') -Force | Out-Null
$workerStatus = Join-Path $control 'worker_status.json'
$worker = $null
if (Test-Path -LiteralPath $workerStatus) {{
    $ws = Get-Content -LiteralPath $workerStatus -Raw -Encoding UTF8 | ConvertFrom-Json
    $worker = Get-Process -Id $ws.pid -ErrorAction SilentlyContinue
}}
if (-not $worker) {{
    Start-Process -FilePath 'D:\\Anaconda\\pythonw.exe' -ArgumentList @('-B','"{SRC / 'ifind_daily_worker.py'}"') -WindowStyle Hidden
    Start-Sleep -Seconds 3
}}
while ($true) {{
    $ws = Get-Content -LiteralPath $workerStatus -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($ws.status -in @('ready','cooldown','requesting')) {{ break }}
    if ($ws.status -in @('crashed','stopped','login_cancelled')) {{ throw '接口进程已停止。' }}
    Start-Sleep -Seconds 2
}}
Start-Process -FilePath 'D:\\Anaconda\\pythonw.exe' -ArgumentList @('-B','"{SRC / 'download_ifind_csi500_daily.py'}"','--allow-missing-inout') -WorkingDirectory '{SRC.parent}' -WindowStyle Hidden
Write-Host '日频下载已启动，请查看 progress.json。'
"""
(DEST / '暂停下载.ps1').write_text(pause, encoding='utf-8-sig')
(DEST / '继续下载.ps1').write_text(resume, encoding='utf-8-sig')
print(DEST)
