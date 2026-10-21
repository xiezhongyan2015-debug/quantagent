# QuantaAgents

## A 股元研究框架与实时 GUI

元框架固定使用 `gpt-6-astra / xhigh`，已接入真实 A 股日线、可组合因子、分组年度诊断、含成本回测、逐轮两问自检和完整调用账本。支持冻结候选方法、多任务独立重复、暂停恢复和批次事故预算。首轮真实研究完成，但未达到稳定生成样本外 Sharpe > 1 的目标；真实股数/公司行动现金账与容量仍未验证。

```powershell
uv pip install --python .venv\Scripts\python.exe -r requirements-meta.txt
.\scripts\start_meta_gui.ps1
```

打开 `http://192.0.2.10:8769`。详见 [元框架使用](docs/META_FRAMEWORK.md)、[首轮真实结果](docs/META_ASHARE_FIRST_REAL_REPORT.md) 和 [迭代自检](docs/META_ITERATION_JOURNAL.md)。下文保留既有 V2 研究入口的说明。

基于 LangGraph 的多智能体量化策略实验框架，支持从实验 YAML 批量运行到回测结果落盘，并提供实时 Web 监控页面。

## 当前流程（与 Web 页面一致）

当前流程由 ManagerAgent 决定下一步，主要顺序为：

1. manager
2. hypothesis
3. strategy
4. validate
5. backtest（只用验证时期，按时间分段）
6. hypothesis（未通过时开始下一轮）或 final_test（已通过且明确许可）
7. done

图结构为环路：

- manager 根据 state.phase 决定下一步
- hypothesis、strategy、validate、backtest、final_test 执行后都会回到 manager
- manager 决策 done 时结束

开发回测默认使用 `validate_start` 到 `validate_end`，可反复用于研究。`backtest_start` 到 `backtest_end` 是锁定的最终时期，默认不运行；它只运行一次，而且结果不会传回策略生成步骤。两段日期相同、交叠或顺序不正确时，程序会拒绝最终测试。

对应 Agent 组件：

- ManagerAgent
- HypothesisAgent
- StrategyAgent
- ValidateAgent
- StrategyTester

## 项目目录

- experiments/: 实验输入 YAML
- experiment_traces/: 运行产物与每轮轨迹
- src/quanta_agents/: 核心代码
- src/quanta_agents/web/: Web 监控前端
- scripts/: 数据导入、调试辅助脚本

## 快速开始

1. 安装依赖

```bash
pip install -e .
```

2. 配置环境变量（建议放在项目根目录 .env）

```env
# OpenAI-compatible
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat

# Workflow retries / epochs
AGENT_MAX_RETRIES=10
STRATEGYAGENT_MAX_RETRIES=10
MAX_EPOCHS=10
MAX_STRATEGY_VALIDATE_ROUNDS=6

# Data backend
QUANTA_DEFAULT_DATA_ENGINE=sqlite
SQLITE_MARKET_DATA_DB_PATH=./market_data.db

# Backtest backend for StrategyTester
QUANTA_BACKTEST_DB_BACKEND=sqlite
SQLITE_BACKTEST_DB_PATH=./vnpy_bar.sqlite3

# Optional DolphinDB connection
DOLPHINDB_HOST=localhost
DOLPHINDB_PORT=8848
DOLPHINDB_USER=admin
DOLPHINDB_PASSWORD=123456
DOLPHINDB_TIMEZONE=Asia/Shanghai
```

3. 运行实验

```bash
python -m quanta_agents.main
```

## 本地 Parquet + Codex 账户运行

这条运行方式不调用 DeepSeek API，也不需要 `OPENAI_API_KEY`。它通过本机已经登录的 Codex 账户调用 `gpt-5.6-sol`，行情和回测都直接读取本地 Parquet。首次研究和新机制探索使用 `ultra`，常规改代码与检查使用 `high`，避免每一步都花很长时间反复推演。

PowerShell：

```powershell
.\scripts\run_codex_ultra_local.ps1 `
  -MaxEpochs 2 `
  -AgentMaxRetries 2 `
  -MaxValidateRounds 2 `
  -DevelopmentFolds 3 `
  -GapTradingDays 10
```

脚本会在当前电脑的量化目录中寻找日频 Parquet，并默认读取：

```text
/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin/instruments/csi300.txt
```

如需明确指定文件和成交价偏差：

```powershell
.\scripts\run_codex_ultra_local.ps1 `
  -ParquetPath 'D:\path\daily_by_stock\*.parquet' `
  -MembershipPath '/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin/instruments/csi300.txt' `
  -Slippage 0.001
```

生成的策略、验证记录、回测汇总、每日资金、成交记录和目标权重会保存在 `experiment_traces/`。

默认只完成开发期测试并冻结通过的候选。确认要读取锁定的最终时期时，另加 `-RunFinalTest`；这个开关不会让最终结果参与下一轮修改。

## 分钟量能与 Level 2 研究

先生成 2022—2025 年分钟候选文件。`--start` 比正式研究期早两个月，用来准备过去 20 个交易日的同分钟成交额：

```powershell
.\.venv\Scripts\python.exe .\scripts\prepare_periodic_minute_events.py `
  --minute-dir '/Users/leslie/Downloads/中证500量化60min/c5_core/data/minute/A股 1min 数据 2000-2026年/分钟数据_前复权_Parquet/data' `
  --output '.\data_cache\periodic_active_buying\minute_events_2022_2025.parquet' `
  --start 2021-11-01 --end 2025-12-31 `
  --event-start 2022-01-01 --event-end 2025-12-31 `
  --workers 8
```

检查资料或启动单个研究：

```powershell
.\scripts\run_periodic_active_buying_loop.ps1 -Profile minute -CheckOnly
.\scripts\run_periodic_active_buying_loop.ps1 -Profile minute -MaxEpochs 3
.\scripts\run_periodic_active_buying_loop.ps1 -Profile level2 -MaxEpochs 3
```

Level 2 会直接读取已有的 8.77 MB 候选特征文件，不会复制或重复扫描约 690 GB 原始逐笔资料。策略代码看不到 `forward_*`、`joint_minute_hit`、`target_buy_up_*` 等未来结果字段；这些字段只在选完事件后用于评价。A股受 T+1 限制，5 分钟结果属于事件研究，不能直接理解成当天买入又卖出的实盘收益。

循环优化时只反复使用验证期，`backtest_start` 到 `backtest_end` 留作一次最终检查，最终结果不会再传回下一轮。默认不会运行最终检查；确定规则不再修改后，可在对应实验 YAML 中加入 `allow_final_test: true`。2026 年 Level 2 最后一段在旧研究中已经看过，不能当作全新的未知样本；真正的最终检验要等后续新资料。

## 实验 YAML 格式

程序会读取 experiments/ 下所有 .yaml/.yml 文件，按文件名排序执行。

必填字段：

- user_idea
- train_start, train_end
- validate_start, validate_end
- backtest_start, backtest_end
- universe

约束：

- 日期格式必须为 YYYY-MM-DD
- 时间顺序必须满足：train_end < validate_start 且 validate_end < backtest_start

示例：

```yaml
user_idea: |
  基于20日突破 + 成交量放大确认的趋势跟随策略
train_start: "2020-01-01"
train_end: "2022-12-31"
validate_start: "2023-01-01"
validate_end: "2023-12-31"
backtest_start: "2024-01-01"
backtest_end: "2025-12-31"
universe:
  - symbols: hs300
    asset: 股票
    description: 沪深300指数成分股
evaluation:
  min_sharpe_ratio: 1.0
  max_drawdown: 0.25
  min_return_rate: 0.12
development_folds: 3
gap_trading_days: 10
run_cost_stress: true
run_delay_stress: true
min_fold_pass_ratio: 0.67
run_final_test: false
```

开发期还会检查最差一段、是否每段都胜过持有现金、成本翻倍、执行延迟一天，以及多次尝试后的夏普可信程度。分段间隔和延迟执行都使用本地行情中真实存在的交易日，不用策略输出行数代替天数。历史指数成分表会按训练或测试时期截断，策略在开发期看不到之后才发生的调入、调出日期。每个候选的类型、代码哈希、代码变化、各段结果和使用过的时期都会单独写入 `experiment_traces/`；即使图表或结果文件保存失败，已经算出的成绩仍会保留并计入本轮。

目前分段、成本翻倍和延迟执行这三项固定检查只在本地日频 Parquet 回测中完整运行。若给其他回测方式打开这些选项，程序会明确停止并提示，不会把未运行的检查当成通过。

## Web 监控页面

启动：

```bash
python -m quanta_agents.webapp
```

访问：

- 本机访问: http://192.0.2.10:8000
- 若在远程开发环境，请转发 8000 端口后访问
- 端口可通过 QUANTA_AGENTS_WEBAPP_PORT 或 PORT 覆盖（默认 8000）

页面功能：

- LangGraph 阶段流（manager/hypothesis/strategy/validate/test/done）
- 当前实验状态（active stage、latest epoch、更新时间）
- Trace 实验列表与目录树浏览
- Trace 文件预览（文本与图片）
- experiments/ 下 YAML 源文件列表、在线编辑与保存
- 终端日志实时窗口（读取 experiment_traces/terminal.log）

## 数据后端说明

当前推荐本地 SQLite 方案：

- 语义层数据：QUANTA_SQLITE_DB_PATH（默认 ./market_data.db）
- 语义层数据：SQLITE_MARKET_DATA_DB_PATH（默认 ./market_data.db）
- 回测库数据：SQLITE_BACKTEST_DB_PATH（默认 ./vnpy_bar.sqlite3）
- 回测后端切换：QUANTA_BACKTEST_DB_BACKEND=sqlite|dolphindb

路径是相对项目根目录解析，不会随启动目录变化。

## DolphinDB 到 SQLite 导出脚本

可使用脚本将 DolphinDB 里的 `market_data` 和 `vnpy_bar_db` 分别导出到本地 SQLite 文件，默认会覆盖 `market_data.db` 和 `vnpy_bar.sqlite3`：

```bash
python scripts/export_dolphindb_databases_to_sqlite.py --replace-existing
```

常用参数：

- `--market-src-db`, `--vnpy-src-db`
- `--market-sqlite`, `--vnpy-sqlite`
- `--start-date`, `--end-date`
- `--skip-market`, `--skip-vnpy`

如果还需要把 `sw2021*` 这几张表同步进 `market_data.db`，可以单独运行：

```bash
python scripts/export_sw2021_market_data_to_sqlite.py --replace-existing
```

## 其他运行参数

- QUANTA_EXPERIMENT_PARALLEL=true: 多文件并行运行实验
- QUANTA_RUNTIME_LOG_FILE: 自定义终端镜像日志路径
- QUANTA_HISTORY_TAIL: 控制主程序末尾输出 history 条数
- QUANTA_ENABLE_FAULTHANDLER: 是否启用 faulthandler（默认开启）

## 注意事项

- 本项目通过 OpenAI 兼容接口调用模型。
- 若使用 webapp 看到页面打不开，请优先使用 192.0.2.10 而不是 0.0.0.0。
- 策略回测 summary 的年化收益已加入百分比/比率自动识别逻辑，避免单位误判。
