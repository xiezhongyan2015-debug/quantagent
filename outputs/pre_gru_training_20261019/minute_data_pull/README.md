# 全 A 历史分钟数据抓取（2026-10-19）

目标：解决 60 分钟 GRU 只有中证500 成分股、只有 2018 年起 1 分钟数据的瓶颈。
本目录：`/Users/leslie/Downloads/中证500量化60min/outputs/pre_gru_training_20261019/minute_data_pull/`

## 结论速览

| 项 | 结果 |
|---|---|
| 转售接口 `stk_mins`（tl.kaixin8.top） | **可用**。1 分钟线能查到在市股和 2021 年退市的股票，**2009、2015、2018、2020 年都有数据**，含 amount。只读探测共 7 次 HTTP 调用。**没有在本地抓**：凭证只在云机上，要不要放到本地由你决定（方案和估算见第 2 节）。 |
| baostock 5 分钟小测 | **可用**。在市股和退市股都能查到；最早一天是 2020-01-02；每天 48 根 5 分钟线，时间是 bar 结束时刻（09:35…11:30，13:05…15:00）；当天成交量合计与日线完全一致（包含开盘集合竞价）；停牌日不返回任何行。 |
| 后台抓取 | **已启动**：PID **98046**（另有 `caffeinate -i -w 98046`，PID 98059，防止 Mac 空闲休眠），15:53 开始。全 A 5,451 只（含 2020 年后退市的 227 只）。 |
| 预计时长 | 实测约 5.3 秒/请求，平均每只股票约 3.5 分钟。中证500 历史成分股（1,028 只）**约 2.5 天**（约 10-22 凌晨）；加上中证1000 历史成分股约 6 天（约 10-25）；**全部约 12 天（约 10-31）**。 |
| 磁盘 | 预计约 9–10 GB（每行约 25 字节 × 3.75 亿行）。本机可用 198 GB，远低于一半。 |

---

## 1. 步骤 1：转售接口探测（云机，只读）

- 方式：脚本 `probe_stk_mins_cloud.py` 用 scp 传到云机 `C:\ChoiceBridge\live\patches\probe_20261019\`，用 `C:\ChoiceBridge\runtime\gru-venv` 加 `-X utf8` 运行（15:33，在 16:00 实盘流水线之前），**用完已删除该目录**（`Test-Path` 返回 False）。没有修改 C:\ChoiceBridge 下任何已有文件，没有碰任何进程或计划任务。token 在输出中全部替换为 `<token>`，本地结果文件 `probe_result_raw.json` 不含凭证。
- 共 **7 次 HTTP 调用**（上限 20 次）。凭证中的 url 是 `https://tl.kaixin8.top/tushare/sdk`，capabilities 用的是同一主机的根路径。

| 调用 | 结果 | 耗时 |
|---|---|---|
| GET `/tushare/capabilities/stk_mins` | 200，`enabled: true`，支持 1/5/15/30/60min，`required: [ts_code]`，`max_limit: 100000`，`cache_ttl: 300` | 1.1 s |
| GET `/tushare/upstreams/probe/stk_mins` | 200，但两个上游都报失败：`official-history-minute` 503 `upstream_pool_exhausted`（说明是"历史分钟来源契约暂未验证"），`relay` 502 `fallback_error`。**实际取数调用却成功了**，说明这个探测接口的结论不可靠，或者数据来自缓存或其他通道 | 2.1 s |
| stk_mins 000001.SZ 1min 2020-06-01 | 可用，240 行 | 8.3 s |
| stk_mins 600634.SH（退市富控，2021-07 退市）1min 2020-06-01 | 可用，240 行 | 2.5 s |
| stk_mins 000001.SZ 1min 2009-06-01 | 可用，240 行 | 2.1 s |
| stk_mins 000001.SZ 1min 2015-06-01 | 可用，240 行 | 1.6 s |
| stk_mins 000001.SZ 1min 2018-06-01 | 可用，240 行 | 2.6 s |

- 返回字段：`ts_code, trade_time, freq, open, high, low, close, vol, amount`。价格不复权；vol 单位是**股**，amount 单位是**元**（600634 当天合计 4,479,500 股、5,003,360 元，与本地日线完全一致）。
- 竞价 bar 和时间口径：**每天 240 根，不是 241 根**。但**两只股票的时间标注口径不一样**：
  - 000001.SZ（2009/2015/2018/2020 都一样）：09:31…11:30、13:01…15:00，用**结束时刻**标注。当天 vol 合计 87,855,036 股，比日线 88,296,055 股少 441,019 股（0.5%），**应该是开盘集合竞价量没有计入**。14:58、14:59 两根为 0 量（深市收盘集合竞价），15:00 那根是收盘竞价量。
  - 600634.SH：09:30…11:29、13:00…14:59，用**开始时刻**标注（和本项目 1 分钟源 `minute_data.py` 的口径相同），当天合计量与日线完全一致（含开盘竞价）。返回的 msg 一个是 `ok`，一个是空字符串，很可能来自两个不同的后端。
  - **用之前必须按"沪/深 × 年份 × 在市/退市"抽样核对口径，并统一转换成开始时刻标注**，否则小时切分会错一分钟。
- 最早可查年份：2009 年可查（2009-06-01 有完整 240 根）。

## 2. 步骤 2：方案选择

转售接口可以拿到"全 A 含退市股、2009 年起、1 分钟、带 amount"，是最好的源，但凭证只在云机上，按约束**没有在本地抓**。云机网络慢，也不在云机上抓。baostock 5 分钟作为免费保底已经在本地启动。

**如果你同意把转售凭证放到本地 Mac**（例如 `~/.config/tushare_reseller.json`，权限 600，不进项目目录），建议这样抓：

- 范围：全 A（5,451 只代码 + 2015 年前退市的股票另从 `stock_basic` 补），1 分钟，先 2015-01 到 2026-10，再视需要补 2009–2014。
- 每次调用：按股票 × 时间段取。capabilities 写 `max_limit 100000`，但上游官方是单次 8000 行，需要先试一次大窗口，看是否真的返回 10 万行（约 416 个交易日）。
- 调用量：2015 年以后全 A 约 1,145 万股票日 × 240 = **27.5 亿行**。
  - 如果单次 10 万行有效：约 **2.8 万次**调用；按实测 1.6–8 s/次（中位约 2.5 s），单线程约 **20 小时**。
  - 如果只有 8000 行：约 **34 万次**调用，单线程约 **10–12 天**（还要看转售方的频率和日上限，目前不知道）。
  - 2009–2014 年再加约 40%。
- 磁盘：1 分钟按约 20 字节/行算，2015 年以后约 **55 GB**，加 2009–2014 约 75 GB（本机可用 198 GB）。
- 风险：上游探测接口报"契约未验证 / 上游池耗尽"，长时间大量拉取可能不稳定；两种时间口径要统一；000001.SZ 那种口径缺开盘竞价量；转售的合规性未知。
- 抓到以后：可以直接复用 `gru_research/minute_data.py` 的 1 分钟审计和聚合流程（口径统一后），24 个特征全部保持原定义。

## 3. 步骤 3：baostock 全 A 5 分钟（已在后台运行）

### 3.1 环境
- `.pcf-research-venv` 安装了 **baostock==0.9.4**（记在 `requirements_pull.txt`；依赖只有 pandas，已经装好）。匿名 `bs.login()`，没有注册账号，没有用 API_KEY。

### 3.2 小测结果（2026-10-19 15:35–15:42）
| 测试 | 结果 |
|---|---|
| sz.000001 2020-06-01..06-05 | 240 行，每天 48 根；首根 09:35、末根 15:00（bar 结束时刻）。2020-06-01 合计量 88,296,055 股，与日线**完全一致**（含开盘竞价，09:35 那根量明显偏大）。 |
| sh.600634（退市富控，2021 年退市）同区间 | 240 行，能查到；合计量 4,479,500 股，与日线完全一致。 |
| sh.600175（退市美都，2020-08 退市）2020-05-25..06-05 | 只返回 05-25/26/27 三天，停牌的 05-28 至 06-05 **没有任何行**（不是 0 量行）。 |
| 起点 | 2019-12-25..2020-01-06 只返回 2020-01-02 起的数据（比官网写的 01-03 早一天）。 |
| 最新 | 2026-10-12..10-16 有数据。 |
| 复权因子 `query_adjust_factor` | 字段 `code, dividOperateDate, foreAdjustFactor, backAdjustFactor, adjustFactor`，每只股票 1 次请求。 |
| 速度 | 每页 2000 行算 1 次服务器请求；实测约 **5 秒/页**（白天，匿名节点）。一只 2020 年至今的全区间股票约 7.9 万行、40 页、约 210 秒。 |

字段：`time`（bar 结束时刻，datetime）、`code`、`open/high/low/close`（不复权，adjustflag='3'）、`amount`（元）、`volume`（股）。

### 3.3 股票清单（`build_universe.py` → `data/universe.csv`）
- 本地全 A 日线 `c5_core/data/A_Layer_Live/daily_source`（沪深，含退市）中 2020-01-01 后有交易的 5,425 只，
- 加上 baostock `query_stock_basic`（`data/stock_basic.csv`）中 type=1、在市或 2020 年后退市的股票，多出来的 26 只是 2026 年 7 月以后上市的新股；
- 合计 **5,451 只**，其中 2020 年后退市的 227 只。区间是 max(2020-01-01, 上市日) 到 min(2026-10-16, 退市日)。**截止日固定为 2026-10-16**（上一个完整交易日），保证整批数据口径一致；以后另做增量。
- 抓取顺序：先 2020 年以来所有中证500 历史成分股（1,028 只，priority 0），再中证1000 历史成分股（1,560 只，priority 1），最后其余股票（2,863 只，priority 2）。中途停下也能先用上指数成分股的数据。

### 3.4 抓取脚本 `pull_baostock_5min.py`
- 单进程、单连接；用 `state/pull.lock` 文件锁拒绝第二个实例（baostock 禁止并发）。
- 所有 socket 请求（登录、每一页、复权因子查询）都经过一个包装函数：请求间隔 **≥0.25 秒**，并计入**每日上限 40,000 次**（按本机日期，存在 `state/request_counts.json`，重启后接着计数）。到上限就登出，睡到次日 00:10 再继续。按实测速度一天最多约 1.6 万次请求，实际不会碰到上限。
- 输出：`data/5min/<code>.parquet`（zstd）和 `data/adj_factor/<code>.parquet`；两者都存在就跳过（断点续传）；先写临时文件再改名，不会留下半截文件。
- 日志：`logs/calls.csv`（每次查询一行：时间、代码、类型、区间、行数、请求数、耗时、状态、错误）、`logs/pull.log`（登录、错误、每 10 只一次进度）、`logs/progress.json`、`logs/nohup.out`。
- 出错时：指数退避（5 s × 2^k，最长 600 s）并重新登录；连续错误超过 20 次，或返回信息像是"黑名单/限制"，就停下来，原因写到 `logs/STOPPED_REASON.txt`。任何一页取失败，整只股票都算失败并重取，不会静默截断。

### 3.5 运行信息
```
PID：98046（python pull_baostock_5min.py），caffeinate PID：98059（随 98046 退出自动结束）
启动命令（在本目录下）：
  nohup /Users/leslie/Downloads/中证500量化60min/.pcf-research-venv/bin/python pull_baostock_5min.py >> logs/nohup.out 2>&1 &
  caffeinate -i -w $(cat logs/pull.pid) &
```
- **停止**：`kill 98046`（或 `kill $(cat logs/pull.pid)`）。收到 SIGTERM 后会把当前这只股票取完再退出（最多约 3.5 分钟）；要立刻停可以用 `kill -9`，已写完的文件不受影响，没写完的那只下次重取。
- **续跑**：在本目录再执行上面的启动命令；已完成的股票自动跳过，当天请求计数接着累计。如果有 `logs/STOPPED_REASON.txt`，先看原因，再续跑（脚本启动时会把旧的原因文件改名保留）。
- **查看进度**：
  ```
  cd /Users/leslie/Downloads/中证500量化60min/outputs/pre_gru_training_20261019/minute_data_pull
  cat logs/progress.json                 # 完成数 / 总数 / 当前速度 / 预计剩余小时
  ls data/5min | wc -l                   # 已完成股票数（含 2 只测试股）
  tail -5 logs/calls.csv; tail -5 logs/pull.log
  cat state/request_counts.json          # 每天请求次数
  ps -p $(cat logs/pull.pid) -o pid,etime,command
  du -sh data/5min
  ```
- **预计完成时间**（按 5.3 s/页、全部约 19.6 万页）：中证500 组约 2.5 天（10-22 凌晨），中证1000 组累计约 6 天（10-25），全部约 12 天（10-31）。速度会随 baostock 服务器负载变化，以 `progress.json` 为准。
- **磁盘**：每只全区间股票约 1.6–2 MB，全部预计 9–10 GB。

## 4. 步骤 4：5 分钟 → 小时特征转换草案 `convert_5min_to_hourly.py`

- 复用 `gru_research/minute_data.py` 的 `HOUR_FEATURES`、`_ratio` 和 `add_features_and_labels`（真实交易日网格、80 根窗口、执行窗口与 y1..y4 标签），只重写"日内 → 4 个小时"这一步。输出到 `data/hourly_5m/<SYMBOL>.parquet`，比 1 分钟版多 `bar_count` 和 `source` 两列。已用 sh.600175 跑通（580 行，85.5% 的小时完整）。
- 小时切分：bar 结束时刻 09:35–10:30 / 10:35–11:30 / 13:05–14:00 / 14:05–15:00，每小时 12 根，和 1 分钟源（开始时刻 09:30–10:29 等）覆盖同样的交易时段。
- 复权：价格 = 不复权价 × 当日生效的 `backAdjustFactor`（后复权）。所有特征和标签都是比值或对数收益，用后复权和用 iFinD 前复权（qfq）只差一个常数，结果相同；但 baostock 用"涨跌幅复权法"，个别除权日可能和 iFinD 有细小差别。

| 特征 | 5 分钟口径下 |
|---|---|
| h_return_1, h_open_gap, h_open_close_return, h_amplitude, h_upper_shadow, h_lower_shadow, h_close_location, h_volume_same_slot_20d, h_volume_prev, h_ma_deviation_4/20/80, h_return_4/20（14 个） | **不变**（只用小时 OHLCV；小时开高低收和量与 1 分钟聚合一致） |
| h_last15_volume_share | **不变**（最后 15 分钟 = 最后 3 根 5 分钟线） |
| h_realized_volatility | 公式相同，12 个收益代替 60 个，买卖价反弹噪声少，**数值偏小，分布变化** |
| h_minute_return_max / min | 变成 5 分钟收益的最大/最小，**量级变大** |
| h_minute_return_skew | 只有 12 个点，**很嘈杂**，接近失效 |
| h_return_logvolume_corr | 只有 12 个点，**很嘈杂** |
| h_up_semivariance_share、h_upminute_volume_share | 能算，但更粗、更嘈杂 |
| h_path_efficiency | 路径变短，**数值系统性偏高** |
| h_zero_volume_share | 5 分钟零量 bar 很少，**基本恒为 0，接近失效** |
| 执行窗口（1 分钟版：10:31–10:35 等 5 根 1 分钟开盘价的均值） | **没有精确对应**；草案用 10:35 / 13:05 / 14:05 那根 5 分钟线的 VWAP（amount/volume）近似。早盘用 09:40 那根，避开 09:35 根里的开盘竞价量，比 1 分钟版晚约 4 分钟。所以 y1..y4 标签会略有变化 |

建议：5 分钟版 GRU 要用 5 分钟口径重新标准化、重新训练，不要直接喂给 1 分钟训练出的模型。在中证500 的 2020–2026 重叠段上，用 1 分钟和 5 分钟两种口径各算一遍特征和 RankIC，量化退化幅度。

## 5. 已知问题
1. **速度**：baostock 匿名节点每页约 5 秒，全量约 12 天；不能并发。官方 V0.9.4 有付费 API_KEY / vip 节点，没有用。客户端每页 2000 行的设置没有改（改协议参数可能被认为是滥用）。
2. **Mac 必须保持开机和联网**：`caffeinate -i` 只阻止空闲休眠；合上屏幕（用电池时）或断网会让请求失败。连续 20 次错误后脚本会自己停下，续跑即可。本机同时在跑其他会话的大计算任务（score_2024_models 等），互不影响，但都会占用 CPU。
3. 截止日固定在 2026-10-16；2026-10-19 以后的数据需要另做增量。`convert_5min_to_hourly.py` 用本地日线（到 10-09）和 iFinD 成分日历（`next_known_open_time`）确定交易日网格，所以 10-09 以后的股票（例如测试股 sh.601091）暂时会跳过标签计算。
4. 停牌日 baostock 不返回行；转换时按真实交易日网格展开，停牌小时记为不完整，和 1 分钟版的处理一致。
5. 本地日线和 baostock 都只有沪深两市，**不含北交所**。
6. 转售接口 1 分钟的时间口径和是否含开盘竞价因股票（或后端）而不同，见第 1 节；upstreams/probe 报上游异常但实际取数成功，需要在大批量拉取前再做小样本核对。
7. 测试时多拉了两只股票（sh.600175、sh.601091），它们的数据就是正式数据，后台任务会跳过它们。当天请求计数（state/request_counts.json）只从抓取脚本开始计，之前小测用的约 20 次请求没有计入，离上限还差得很远。

## 文件
- `probe_stk_mins_cloud.py`：云机只读探测脚本（已从云机删除）；`probe_result_raw.json`：探测结果（不含 token）
- `build_universe.py`、`data/universe.csv`、`data/universe_daily_source_scan.csv`、`data/stock_basic.csv`
- `pull_baostock_5min.py`、`requirements_pull.txt`、`logs/`、`state/`、`data/5min/`、`data/adj_factor/`
- `convert_5min_to_hourly.py`、`data/hourly_5m/`（草案输出）
