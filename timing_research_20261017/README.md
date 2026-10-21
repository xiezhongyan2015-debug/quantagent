# 择时仪表盘（只读）

口径与依据见 `reports/择时研报深度解读_2026-10-17.md`。只进日报，不参与目标生成与交易。

| 文件 | 内容 |
|---|---|
| `xy_swing.py` | 兴证系列二自适应波段划分与点位效率（公众号原文公式） |
| `lppls.py` | LPPLS 正/负向泡沫置信度：126 个窗口，Shu & Zhu 过滤条件，兴证消散规则 |
| `dashboard.py` | 汇总五块：波段、LPPLS、宽度、3.0 战术、外部读数 → `dashboard_<T>.json/.md` |
| `xy_external_log.csv` | 兴证周报（不定期择时）和月报（ERP 3.0、大小盘、成长价值）的手工登记 |
| `push_external_log.sh` | 把本地登记表推到云机 `C:\ChoiceBridge\live\timing\xy_external_log.csv` |
| `cache/` | 本地缓存：指数日线（腾讯，2010 年起）和 LPPLS 逐日结果 |

## 云机

- 代码：`C:\ChoiceBridge\quant\current\timing_research_20261017\`。
- 缓存与输出：`C:\ChoiceBridge\live\timing\`。
- `daily_cloud.ps1` 在"daily report + push"前调用 `Timing-Dashboard`：
  - 自带 try/catch 并在局部关掉 Stop，**失败不会影响流水线**；
  - 日报从 `live\timing\dashboard_<T>.json` 读 `lines`，读不到就跳过。
- 2026-10-17 部署：备份为 `daily_report.py.bak_20261017` 和 `daily_cloud.ps1.bak_20261017`。云机烟测 7 秒，退出码 0。
- 每次大约 7 秒；第一次没有缓存时，LPPLS 要补 60 天，约 3 分钟。

## 每周要做的手工事

兴证周报（周末，"兴财富"）和月报（月初）出来后：
1. 在 `xy_external_log.csv` 追加一行。`item` 用已有名字，日报会自动取每个 `item` 的最新一行。
2. 运行 `bash timing_research_20261017/push_external_log.sh`。

查找方法：搜狗微信搜 `XYQuantResearch 不定期择时模型`。打开结果要在内置浏览器里执行 `location.href=a.href`。

## 已知偏离与限制

- **3.0 战术：** CR10 用的是并集池（38 个申万组）而不是全 A 申万一级；EMA 跨度和 Min-Max 窗口原文没给，这里取 20/20。
- **宽度背离：** 周频定义（指数 vs 上涨比例 8 周均值的 8 周相关为负且下降）是我对系列六的解读。
- **LPPLS：**
  - 搜索空间收窄到 m∈[0.1,0.9]、ω∈[2,25]，以避开 m→0 的退化解；
  - 没有做 Lomb 检验和单位根检验；
  - 历史上识别出 2015-06 正泡沫 0.40、2018-10 和 2024-02 负泡沫 0.11–0.13，信号稀疏。
- **点位效率概率：** 兴证原文没有公开公式。这里用"已完成同向段终点效率超过当前值的比例"。
- **以上全部没有做预测力检验**，那是 P2 的工作。读数只用于观察。

## P2 / P3（2026-10-17）

- **P2 检验：** 协议在 `reports/P2_择时信号检验协议_2026-10-17.md`，结果在 `reports/P2_择时信号检验结果_2026-10-17.md`。
  - 代码：`p2_signals.py`（信号和目标历史）、`p2_lppls_backfill.py`（LPPLS 回填，每 5 日一次）、`p2_eval.py`（检验与门槛）、`p2_explore_hold.py`（未登记的探索）。
  - 输出：`outputs/timing_p2_20261017/`。
- **P3 beta 倾斜（10-19 起实盘）：** 协议在 `reports/P3_beta倾斜影子登记协议v2_2026-10-17.md`（补充 2 = 实盘），代码 `beta_tilt_shadow.py`（信号、delta、记录）+ `beta_tilt_live.py`（实盘包装）。
  - 云机 daily.sh `--beta-tilt`：未倾斜目标放行 → 算 S7 持有 pos 和 delta → `live_targets.py --overlay` 重跑全部闸门 → 失败就恢复未倾斜目标。这一步在数据审核和封存发布之前。
  - 记录在 `C:\ChoiceBridge\live\timing\beta_shadow\`：`<T+1>/applied_<T+1>.json`（是否执行）、`delta_realized_<T+1>.csv`（整手后实际权重差）、`beta_shadow_log.csv`、`beta_shadow_pnl.csv`（倾斜贡献，成本加倍）。
  - 未倾斜目标备份在 `实盘日更/outputs/targets/<T+1>/untilted/`。日报有一行"beta 倾斜：已执行 / 未执行"。

## v3 日报块（2026-10-17 部署）

- **代码：** `dashboard_v3.py`，由 `dashboard.py` 调用。日报里"趋势族 / 拐点族 / 兴证合成 + 三维仓位"三行替换了原来的 LPPLS 行和 3.0 战术行；v3 失败时自动退回原来的两行。
- **云机依赖：** 不新装任何包。
  - 拓扑用 ripser 0.6.12 的编译核心 `pyRipser.cp313-win_amd64.pyd`，只依赖 numpy；GPR 用纯 Python 的 `xlrd`。两者都放在 `C:\ChoiceBridge\runtime\timing-extras`，由 GRU 环境加载。
  - 直接调用 pyRipser 与完整 ripser 的 H2 结果逐一相同。
- **数据：**
  - 东方财富：国债收益率、月度新增贷款、USDCNH，每天抓取；
  - GPR：每天下载 xls；
  - 申万二级：每天用 Tushare `sw_daily(trade_date)` 增量补一天，种子在 `cache/v3/sw_l2_close.parquet`；
  - 股息率：研究种子截至 2026-09-18，之后用 c5 面板 `fd_v_dv_ttm` 按比例拼接延伸。
- **云机烟测（10-16）：** 无错误，97 秒。ART −1，趋势族 −3，拐点族 −2。性价比 3.0 的上月末值我们是 45%，兴证是 20%，偏差仍在。
