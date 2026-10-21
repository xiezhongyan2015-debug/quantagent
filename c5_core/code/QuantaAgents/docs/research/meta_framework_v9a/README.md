# V9A：独立因子研究入口

2026-09-12。实际仓库：`/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents`。

V9A 以新的 `factor_research` 包组合调用 V6 表达式/标签/分组与 V7 固定 Ridge，不继承 V9Study，不运行 DailyAccount。它交付原因子的精确公式、方向、数据/时间/股票池约定、全部尝试和冻结后证据。可执行、历史年度目标、独立稳定性和盈利分别判定。

## 本轮冻结口径

- 2015 预热；2016—2018 估计单因子方向及固定预测基线；2019—2020 指导结构/参数开发；冻结后仅一次进入 2021—2024 诊断。
- 年 IC 为方向固定的原始单因子值与 `open[t+6]/open[t+1]-1` 的日度截面 Pearson IC 的年均值；标签不排名，不年化。另报同样本 Spearman RankIC。
- 每自然年末六个信号交易日排除，以保证标签在当年完整退出；所有分段汇总使用相同年度端点规则。有效日至少 200、日截面至少 100 股、逐年评价覆盖至少 80%。未知或不完整年份不能从最差年排名中删除。
- 历史目标要求 2019—2024 六个预声明检查年度各自 IC ≥ 0.10。训练内数字单列；不能按年翻方向、取绝对值或用组合预测 IC 替换单因子。
- 股票池为信号日历史沪深300成员、120 日有收盘记录、20 日平均成交额为正、非 ST/退市；表达式内部每个截面排名使用同一池。历史观测仍保留用于因果滚动窗口。
- 单因子原始值不做额外缩尾/横截面缩放。若公式本身含排名或归一化，精确记录在表达式中。
- `open/high/low/close` 是首个有效请求日固定锚的复权价格；成交量来自现有转换器的股数口径，成交额为元。该转换源码口径不是独立原行情认证，复权修订和历史到达时间仍未认证。

2015—2024 已被旧研究曝光；本轮不恢复独立样本外地位。2025 只准日历、来源和文件元数据，不物化数值行。保存 Parquet 文件哈希不等于读出其所有年份数值，底层物理页面可能跨越逻辑日期过滤边界。

## 有界程序与模型协作

初批三臂各 12 个主要提案尝试：精确现有定义、纯规则窗口扰动、本次 Astra xhigh 的结构提案。24 个匹配控制尝试单列，60 次尝试形成 47 个不同公式；重复保留，不补名额。匹配控制消耗的计算和共享缓存复用单独记录，不能据更多控制计算直接宣称框架优势。

预声明预算允许一次开发证据反馈后的结构修订，最多增加 12 个主要尝试；所有批次总计最多 72 个不同公式。初批三臂公平比较保存在 `initial_*` 原件；修订额外预算单列。固定预测器配对最多 54 次，确认访问最多一次，单研究进程、数值墙钟上限 7,200 秒、进程私有内存上限 4 GiB、总产物 8 GiB，并保留至少 2 GiB 空闲盘。限制在任务间检查，单次数值操作不是进程外硬实时抢占。

模型在本次原生 Codex 主/子代理会话中提出结构、读取开发证据并修订；Python 执行参数展开、去重、统计、对照、预算、冻结、恢复和导出。每个参数点不额外调用模型。CLI 的 `run` 可重放当前冻结提案队列；它不会另行启动一个未经记账的模型生成流程。未来新的模型响应通过严格提案 JSON 和来源回执导入。

每个结构有可执行对照和证伪指标。交互比较保留两个主效应；训练拟合反号时，方向化因子证据与原预期正向机制分开。局部配对正增量不是六年 0.10 达标，分块置信区间未校正整个自适应搜索。

固定基线为精确 F2/F3/F7，Ridge 正则 0.1、2016—2018 一次拟合，之后不重拟合。其学习目标是旧 V7 的当日收益排名去均值，预测评价仍使用未排名五日收益。分别保存 B 原覆盖、B 共同样本、B+f 共同样本，以及覆盖变化与重拟合归因；这些全部是预测器证据。

## 运行与恢复

使用仓库已有 `.venv\Scripts\python.exe`。默认研究根为 `F:\V9A_Factor_Research_20260912\study`。

```powershell
.\.venv\Scripts\python.exe scripts/run_factor_research_v9a.py init
.\.venv\Scripts\python.exe scripts/run_factor_research_v9a.py calibrate --limit 3
.\.venv\Scripts\python.exe scripts/run_factor_research_v9a.py develop
.\.venv\Scripts\python.exe scripts/run_factor_research_v9a.py compare
# 有一次已授权开发反馈修订时，先导入严格提案 JSON，再 develop / compare。
.\.venv\Scripts\python.exe scripts/run_factor_research_v9a.py revise --proposals <完整JSON路径>
.\.venv\Scripts\python.exe scripts/run_factor_research_v9a.py freeze
.\.venv\Scripts\python.exe scripts/run_factor_research_v9a.py confirm
.\.venv\Scripts\python.exe scripts/run_factor_research_v9a.py report
```

同一命令可以恢复尚未完成的队列。源码、协议、基线、提案、股票列表、来源文件和冻结候选具有哈希约束；已有成功和失败原件保留。候选冻结后，生成与开发永久关闭；确认中断恢复重用同一准入，不开启新的搜索。旧 V7/V8/V9 关闭状态和预算不恢复。

本轮的交易用途证据限定为五分组及固定 top40 的前瞻毛收益。top40 在信号时选定，未来标签缺失不补位；完整40只与部分可观察均值分别报告。这不是资金账户、成交、复利收益、费用或容量检验。

因子包在本机导入后用现有引擎重算并逐值核对，同时保存供独立审计的 raw scores/open/pool/date/symbol 快照。快照因子值未乘方向。跨机器迁移仍需安装对应计算器并映射包内源路径；当前验收为本机复现。

## 证据入口

- `F:\V9A_Factor_Research_20260912\protection`：已有修改保护、Git 状态、差异、5,630 文件归档和哈希。
- `F:\V9A_Factor_Research_20260912\receipts\team_receipts.json`：Astra ultra 主任务、三个 Astra xhigh 子代理的真实本地会话与用量快照。货币费用无账单时保持未知。
- `F:\V9A_Factor_Research_20260912\verification`：工程测试、实际面板等价与独立复算；失败测试原件一并保留。
- `F:\V9A_Factor_Research_20260912\study`：冻结协议、来源清单、逐日/逐年结果、基线对照、尝试分母、确认和因子包。
- [独立审计](INDEPENDENT_AUDIT.md)：独立公式行为反例、标签/统计 oracle、控制器故障注入与实际研究验收状态。

工程检查不证明金融目标。实际结论以本轮 `result.json`、`REPORT.zh-CN.md` 和最终验收记录为准。
