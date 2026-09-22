param(
    [Parameter(Mandatory=$true)][string]$Config,
    [ValidateSet('close','execute')][string]$Job = 'close',
    [ValidateSet('paper','choice')][string]$Mode = 'paper',
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$configPath = (Resolve-Path -LiteralPath $Config).Path
$env:PYTHONPATH = Join-Path $repoRoot 'src'
Set-Location -LiteralPath $repoRoot
$logFolder = Join-Path $repoRoot 'output/pcf_automation/tasks'
New-Item -ItemType Directory -Path $logFolder -Force | Out-Null
$logPath = Join-Path $logFolder ((Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + $Job + '.log')
& $Python -m quanta_agents.factor_lab_a.pcf_automation --config $configPath $Job --mode $Mode 2>&1 | Tee-Object -FilePath $logPath
exit $LASTEXITCODE
