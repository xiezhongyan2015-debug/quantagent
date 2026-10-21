param(
    [int]$MaxEpochs = 1,
    [int]$AgentMaxRetries = 2,
    [int]$MaxValidateRounds = 2,
    [string]$ParquetPath = "",
    [string]$MembershipPath = "/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin/instruments/csi300.txt",
    [double]$Slippage = 0.001,
    [switch]$RunFinalTest,
    [int]$DevelopmentFolds = 3,
    [int]$GapTradingDays = 10
)

$ErrorActionPreference = "Stop"

if ($DevelopmentFolds -lt 1 -or $DevelopmentFolds -gt 12) {
    throw "DevelopmentFolds must be between 1 and 12."
}
if ($GapTradingDays -lt 0 -or $GapTradingDays -gt 252) {
    throw "GapTradingDays must be between 0 and 252."
}

$env:QUANTA_LLM_PROVIDER = "codex_exec"
$env:CODEX_EXEC_MODEL = "gpt-5.6-sol"
$env:CODEX_EXEC_REASONING_EFFORT = "high"
$env:CODEX_EXEC_STRATEGY_REASONING_EFFORT = "high"
$env:CODEX_EXEC_VALIDATION_REASONING_EFFORT = "high"
$env:CODEX_EXEC_MULTI_AGENT = "false"
$env:CODEX_EXEC_TIMEOUT_SECONDS = "1800"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Project Python was not found: $pythonPath"
}

if (-not $ParquetPath) {
    $financeRoot = Split-Path -Parent (Split-Path -Parent $projectRoot)
    $ParquetPath = Get-ChildItem -LiteralPath $financeRoot -Directory |
        ForEach-Object { Join-Path $_.FullName "february_march_results\cache\daily_by_stock\*.parquet" } |
        Where-Object { Test-Path -Path $_ } |
        Select-Object -First 1
}
if (-not (Test-Path -Path $ParquetPath)) {
    throw "Local Parquet files were not found. Pass -ParquetPath explicitly."
}
if (-not (Test-Path -LiteralPath $MembershipPath)) {
    throw "CSI 300 membership file was not found: $MembershipPath"
}

$env:QUANTA_DATA_ENGINE = "parquet"
$env:QUANTA_BACKTEST_DB_BACKEND = "parquet"
$env:QUANTA_PARQUET_DATA_GLOB = $ParquetPath
$env:QUANTA_CSI300_MEMBERSHIP_FILE = $MembershipPath
$env:QUANTA_BACKTEST_SLIPPAGE = [string]$Slippage

$env:MAX_EPOCHS = [string]$MaxEpochs
$env:AGENT_MAX_RETRIES = [string]$AgentMaxRetries
$env:MAX_STRATEGY_VALIDATE_ROUNDS = [string]$MaxValidateRounds
$env:QUANTA_EXPERIMENT_PARALLEL = "false"
$env:QUANTA_HISTORY_TAIL = "30"
$env:QUANTA_RUN_FINAL_TEST = if ($RunFinalTest) { "true" } else { "false" }
$env:QUANTA_ALLOW_FINAL_TEST = if ($RunFinalTest) { "true" } else { "false" }
$env:QUANTA_DEVELOPMENT_FOLDS = [string]$DevelopmentFolds
$env:QUANTA_GAP_TRADING_DAYS = [string]$GapTradingDays

Push-Location $projectRoot
try {
    & $pythonPath -m quanta_agents.main
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}
