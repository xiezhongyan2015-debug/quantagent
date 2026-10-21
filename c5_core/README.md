# c5 核心包（每日状态 + 下单清单）

打包日期：2026-10-12。用途：你们在自己的机器或集群上独立跑 c5 日更，每天收盘后得到当天的 c5 状态文件（excluded / vetoed 等）和下单清单，不再依赖我们发文件。

两个分卷：`c5_core_part1.tar`（代码、模型、规则、实盘数据、基本面）和 `c5_core_part2.tar`（vendor 日线、qlib 对账数据）。两个都解压到同一个目录，得到 `c5_core/`。

## 一、目录

| 目录 | 内容 |
|---|---|
| `code/QuantaAgents` | 代码。核心在 `src/quanta_agents/factor_lab_a/`（因子表达式、面板、比赛规则、实盘、基本面）；日更脚本在 `scripts/`；研究记录在 `docs/`、`master.md` |
| `data/A_Layer_Live` | 实盘数据：日线源（到 2026-10-09）、指数成分、除权事件、两个最终模型、各版本目标持仓、c5 每日状态。面板和 396 个因子的排名缓存不在包里，第一次运行会自动重建 |
| `data/A_Layer_Research` | 只带日更要读的部分：c5 冻结规则、396 个成员清单、因子定义与方向、基本面时点网格和原始表 |
| `data/vendor` | vendor 原始日线（到 2026-07-17），日更每天把它和公开接口增量拼成日线源 |
| `data/qlib_data` | qlib 日线，日更做对账用 |
| `data/ext` | Alpha101 / Alpha191 公式库和"因子日历"因子代码（396 个成员里有用到） |
| `data/TushareData` | 基本面增量下载客户端（不含任何密钥和本机网络配置） |
| `docs` | 之前发给你们的文件：c5 分数导出、09-29 至 10-09 状态、体检报告、交接说明 |

## 二、安装（一次）

1. **Python 3.10 以上**，然后 `pip install -r code/QuantaAgents/requirements-c5-freeze.txt`。这份清单是原机器虚拟环境的精确版本（Windows 上导出），Linux 上个别装不上的 Windows 专用包删掉那一行即可。核心依赖是 numpy、pandas、pyarrow、lightgbm、scikit-learn、requests。
2. **路径重定位**：进入 `c5_core/`，运行 `python install.py`。它把代码和规则文件里原机器的绝对路径（`F:/A_Layer_Live`、`D:/qlib_data` 等）改成本目录下的位置；整个目录以后再挪地方，再跑一次即可。`python install.py --check` 应输出 0。
3. **Shell**：日更脚本是 bash（Linux 原生；Windows 用 Git Bash）。用环境变量 `PY` 指定解释器，不设就用 `python`。
4. **基本面密钥**：`export FD_TUSHARE_TOKEN=...`（任意 Tushare 兼容接口账号）。没配的话基本面停在 2026-09-17，否决名单（vetoed）最多沿用 `--ffill-limit` 个交易日（默认 10），之后会逐步失效直到为零。excluded 不受影响。

## 三、每天（收盘后，北京时间 15:00 以后）

```
cd c5_core/code/QuantaAgents
bash scripts/c5_daily.sh                  # 可加 --sync <目录>，跑完自动把状态文件复制过去
```

- **产出**：`data/A_Layer_Live/c5_states/c5_state_YYYYMMDD.parquet` 和同名 `.csv`。列为 `date, code, score_pct, state, bucket, bucket_pct, raw_score, model_version`，code 写成 `600000.SH`，`date` 是信号日。另有累计文件 `c5_states_all.parquet`。
- **c5 目标持仓**：`data/A_Layer_Live/targets/shadow_c5_fdveto2_freecap/target_YYYYMMDD.csv/json`。
- **用时**：40 至 60 分钟，大头是全市场日线增量下载（腾讯、东方财富公开接口；东方财富列表接口不通时自动改用腾讯批量行情）。
- **资源**：排名一步三路并行，约占 5 GB 内存；有成员算失败会自动单路补算，补不齐就停下报错，不会用旧数据出结果。
- **第一次运行**会重建面板和 396 个排名，多花 10 到 20 分钟。

## 四、下单清单

```
python -m quanta_agents.factor_lab_a.live target \
  --panel-dir ../../data/A_Layer_Live/panel \
  --prediction ../../data/A_Layer_Live/pred_live_turnlabel.parquet \
  --nav 2000000 --holdings 持仓.csv --out <输出目录> --cfg '<c5 规则>'
```

- **c5 规则**：取 `scripts/c5_daily.sh` 里 `CFG_C5=` 那一行的 JSON。
- **持仓 csv**：列 `code`（`SH600000` / `600000.SH` / `sh600000` 均可）、`shares`，可选 `market_value`。不给持仓即按空仓建仓。
- **输出 csv** 每只股票一行：
  - `bucket`：csi500 / other / outside（outside 表示必须卖出）
  - `score_pct`、`state`、`held_mv`、`target_mv`
  - `exec_shares`：按最新收盘价取整到 100 股
  - `delta_shares`
  - `action`：buy / sell / trim / hold / skip_lot
  - `close`、`is_st`
- **输出 json**：
  - `precheck`：买单 100% / 80% / 50% / 0% 成交四种情形下，500 占比、仓位、单股上限是否越线
  - `style_exposure`：分数对换手、市值、波动、动量的截面相关
  - 订单数、取整误差等
- **执行**：下一交易日开盘集合竞价成交。排雷的收益几乎全在隔夜，拖到盘中会漏掉主要部分。顺序是先卖、再买 500 桶、再补其余桶。完整运维纪律见 `code/QuantaAgents/docs/COMPETITION_OPS.md`。

## 五、装好后的核对（用包里同一份数据快照，不联网）

```
cd c5_core/code/QuantaAgents
bash scripts/c5_daily.sh --skip-data --ffill-limit 15 --start 2026-10-09
```

生成的 `data/A_Layer_Live/c5_states/c5_state_20261009.parquet` 应与 `docs/给队友_c5状态_20260929_20261009/c5_state_20261009.parquet` 逐股一致：500 桶状态全部相同，`bucket_pct` 差在 1e-6 量级。我们打包后已在一个空目录里解压、安装、按这个步骤实测过，结果见 `docs/核心包实测记录.md`。

要用 10-09 这一天核对，不要用 09-28：面板每次重建都按最新一天锚定前复权价，历史日期会有微小漂移。

## 六、包里没有的东西

- **密钥**：基本面接口密钥、Choice 账号凭据、本机网络配置。
- **原机器的 Python 虚拟环境**：换平台不能直接用，按依赖清单重装。
- **研究用的大数据**：研究期面板、样本外面板、几百个因子的历史排名、1 分钟行情库。日更和下单用不到；`scripts/c5_replay.py` 这类研究脚本需要它们，所以在本包里跑不了。
