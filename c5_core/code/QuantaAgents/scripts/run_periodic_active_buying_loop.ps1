param(
    [ValidateSet("minute", "level2")]
    [string]$Profile = "minute",
    [int]$MaxEpochs = 3,
    [int]$AgentMaxRetries = 2,
    [int]$MaxValidateRounds = 3,
    [string]$MinuteEventPath = "",
    [string]$Level2EventPath = "",
    [string]$RawMinuteDir = "/Users/leslie/Downloads/中证500量化60min/c5_core/data/minute/A股 1min 数据 2000-2026年/分钟数据_前复权_Parquet/data",
    [string]$Level2Root = "F:\2026(QQ群1097616051后续增量更新)\l2_a_share_parquet",
    [switch]$RunFinalTest,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "没有找到项目 Python: $pythonPath"
}

$financeRoot = Split-Path -Parent (Split-Path -Parent $projectRoot)
$legacyRoot = Join-Path $financeRoot "因子日历测试"
$legacyOutput = Join-Path $legacyRoot "outputs\periodic_active_buying_20260804"

if (-not $MinuteEventPath) {
    $MinuteEventPath = Join-Path $projectRoot "data_cache\periodic_active_buying\minute_events_2022_2025.parquet"
}
if (-not $Level2EventPath) {
    $Level2EventPath = Join-Path $legacyOutput "level2_candidate_features.parquet"
}

$minuteCandidatesPath = Join-Path $legacyOutput "minute_candidates.parquet"
$fourierFeaturesPath = Join-Path $legacyOutput "fourier_features.parquet"
$level2CatalogPath = Join-Path $Level2Root "l2_catalog.duckdb"

$env:QUANTA_RAW_MINUTE_DATA_DIR = $RawMinuteDir
$env:QUANTA_MINUTE_CANDIDATES_PATH = $minuteCandidatesPath
$env:QUANTA_FOURIER_FEATURES_PATH = $fourierFeaturesPath
$env:QUANTA_LEVEL2_ROOT = $Level2Root
$env:QUANTA_L2_CATALOG_PATH = $level2CatalogPath
$finalTestEnabled = if ($RunFinalTest) { "true" } else { "false" }
$env:QUANTA_RUN_FINAL_TEST = $finalTestEnabled
$env:QUANTA_ALLOW_FINAL_TEST = $finalTestEnabled

Remove-Item Env:QUANTA_MINUTE_EVENT_FEATURES_PATH -ErrorAction SilentlyContinue
Remove-Item Env:QUANTA_LEVEL2_EVENT_FEATURES_PATH -ErrorAction SilentlyContinue
if ($Profile -eq "minute") {
    $env:QUANTA_MINUTE_EVENT_FEATURES_PATH = $MinuteEventPath
}
else {
    $env:QUANTA_LEVEL2_EVENT_FEATURES_PATH = $Level2EventPath
}

$checkScript = Join-Path $PSScriptRoot "check_periodic_active_buying_data.py"
& $pythonPath $checkScript --profile $Profile
if ($LASTEXITCODE -ne 0) {
    if ($Profile -eq "minute") {
        Write-Host "先运行 scripts\prepare_periodic_minute_events.py 生成多年分钟候选文件。"
    }
    exit $LASTEXITCODE
}
if ($CheckOnly) {
    exit 0
}

if ($Profile -eq "minute") {
    $env:QUANTA_EVENT_FEATURES_PATH = $MinuteEventPath
    $env:QUANTA_EXPERIMENT_FILE = "periodic_volume_minute.yaml"
}
else {
    $env:QUANTA_EVENT_FEATURES_PATH = $Level2EventPath
    $env:QUANTA_EXPERIMENT_FILE = "periodic_active_buying_level2.yaml"
}

$env:QUANTA_DATA_ENGINE = "research_parquet"
$env:QUANTA_BACKTEST_DB_BACKEND = "event_parquet"
$env:QUANTA_LLM_PROVIDER = "codex_exec"
$env:CODEX_EXEC_MODEL = "gpt-5.6-sol"
$env:CODEX_EXEC_REASONING_EFFORT = "ultra"
$env:CODEX_EXEC_TIMEOUT_SECONDS = "1800"
$env:MAX_EPOCHS = [string]$MaxEpochs
$env:AGENT_MAX_RETRIES = [string]$AgentMaxRetries
$env:MAX_STRATEGY_VALIDATE_ROUNDS = [string]$MaxValidateRounds
$env:QUANTA_EXPERIMENT_PARALLEL = "false"
$env:QUANTA_HISTORY_TAIL = "40"

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
