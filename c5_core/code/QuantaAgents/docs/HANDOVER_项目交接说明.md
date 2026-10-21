# 项目交接说明（中证500 指数增强 · 兴证全球杯量化赛道）

整理日期：2026-10-09。本包是代码与文档的完整快照，不含任何行情、财务数据和模型产物（约 50 GB，另行传输）。

## 一、先看什么

| 想了解 | 看哪里 |
|---|---|
| 策略是什么、怎么做出来的、收益多少 | `docs/research/competition_reports/` 下按日期排序的六份报告，最后一份《策略开发总结与最新冻结版本收益报告》是总览 |
| 比赛规则与运营纪律 | `docs/research/framework_review_20260913/competition_discipline_manual_v1.md`、`docs/COMPETITION_OPS.md`（日更流程、登记卡数字） |
| 每一轮研究的原始记录（含失败项） | `docs/research/framework_review_20260913/A11_*` 到 `A18_*`，经验库 `PV_ALPHA_PLAYBOOK.zh-CN.md` |
| 最新冻结版本 c5 的规则与重放结果 | `docs/research/framework_review_20260913/frozen_c5/`（规则 JSON、研究期与前向重放、各臂对比表、单因子表） |
| 整个项目的时间线 | `master.md`（第 28 节起是比赛线） |

## 二、代码结构

- `src/quanta_agents/factor_lab_a/`：A 层因子实验室。`dsl.py` 因子表达式语言；`panel.py` 面板（一字段一文件的日期 × 股票宽表）；`evaluate.py` 单因子评价与准入；`combine.py` 树模型组合；`competition.py` 比赛口径的两桶账本、评价、冻结；`live.py` 实时面板、排名、打分、目标持仓；`fundamental.py` 基本面时点对齐（A18）。
- `scripts/`：`competition_daily.sh` 日更链（数据 → 面板 → 基本面装配 → 排名 → 打分 → 各版本目标）；`competition_data.py` 公开接口行情更新；`competition_union_combine.py` 组合重训；`competition_compare.py` 多规则对比；`pv4_*` 为 A17 结构项；`fd_*` 为 A18 基本面接入全流程（合并、建字段、装配、校验、审计、准入、各臂、冻结、前向、日更增量）。
- `src/quanta_agents/`（A 层之外）：早期的多智能体元框架，比赛线不依赖它。
- `tests/`：单元测试；`experiments/`、`research/`：早期研究留档。

## 三、运行所需的数据（不在本包内）

| 数据 | 用途 | 来源 |
|---|---|---|
| 全 A 前复权日线（2015 起）、指数成分生效日名单、中证500 价格指数 | 研究面板与实时面板 | 本地日线源 + 公开行情接口（日更链自动拉） |
| 十年全 A 基本面 20 张表（约 3.6 GB） | 基本面字段与排雷否决 | 第三方 Tushare 兼容接口下载的原料库；已合并为每表一个文件 |
| 1 分钟行情（约 46 GB，2000—2026-05） | 只用于研究中的成交时点核算，日常不需要 | 本地分钟库 |
| 396 个因子的排名缓存、训练好的最终模型、冻结分数 | 日更链打分 | 由脚本重建；重建全部约需数小时 |

路径约定：研究根 `/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research`、样本外 `/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS`、实时 `/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Live`，在各脚本顶部为常量，换机器时统一改这三处。

## 四、环境

- Windows，Python 3.10，`requirements-alayer.txt`；树模型用 LightGBM；24 线程、约 10 GB 可用提交内存即可跑一次重训（约 20 分钟）。
- 日更链用 bash（Git Bash），15:10 之后跑：`bash scripts/competition_daily.sh --nav <子账净资产> --holdings <持仓 csv>`。
- 基本面日更增量（`scripts/fd_update.py`）需要第三方接口密钥，只从环境变量或标准输入读取，不落盘；本包不含密钥。

## 五、当前状态（2026-10-09）

- 线上主线 c2；c4、c5 为影子臂，每日只记录目标持仓不下单。c5 已冻结存档，研究期净超额年化 +6.6% / 夏普 2.33，前向（2025 至 2026-09-16，看过一次）+5.1% / 1.18。
- 待办：换版决定（c2 → c5）；基本面日更联调（需密钥，否则 c5 影子臂的基本面字段过期失效）；12 月 11 日收盘后核对成分名单。
- 已证伪、不必重做：再加量价因子、更复杂模型（含序列模型）、训练目标变体、行业中性、环境切换、因子交叉复合、业绩预告与快报类因子、延后成交时点、把基本面分数混进量价分数。

## 六、研究纪律（读数字前必须知道）

2016—2018 只定因子方向；2019—2024 只做选择；2025 到 2026-09-16 只在规则定好后看一次，且已被累计评价 208 次，前向数字只能当"未被否定"。任何改动要两颗随机种子方向一致、净超额至少 +0.5 个点、夏普至少 +0.1 才算有效。
