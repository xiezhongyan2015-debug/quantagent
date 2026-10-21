param([int]$Port = 8769, [string]$RunRoot = 'experiment_traces\meta_ashare_v2')
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$scriptPath = Join-Path $PSScriptRoot 'run_meta_framework.py'
$logDirectory = if ([System.IO.Path]::IsPathRooted($RunRoot)) { [System.IO.Path]::GetFullPath($RunRoot) } else { [System.IO.Path]::GetFullPath((Join-Path $projectPath $RunRoot)) }
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '请先建立项目 .venv 并安装 requirements-meta.txt。' }
$healthUrl = "http://192.0.2.10:$Port/api/health"
try {
    $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
    if ($health.ok) { Write-Output "研究监控已运行：http://192.0.2.10:$Port"; exit 0 }
} catch { }
$processArguments = '"{0}" serve --root "{1}" --port {2}' -f $scriptPath, $logDirectory, $Port
$startedProcess = Start-Process -FilePath $pythonPath -ArgumentList $processArguments -WorkingDirectory $projectPath -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDirectory 'server.stdout.log') -RedirectStandardError (Join-Path $logDirectory 'server.stderr.log')
Write-Output "研究监控正在启动：http://192.0.2.10:$Port （进程 $($startedProcess.Id)）"
