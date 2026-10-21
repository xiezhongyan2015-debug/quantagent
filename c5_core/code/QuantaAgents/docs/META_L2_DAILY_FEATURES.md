# L2 日因子模块：revision4 实现与固定边界

已在隔离副本 `experiment_traces/meta_ashare_revision4/src/quanta_agents/meta/l2_daily_features.py` 实现纯 DataFrame → 收盘日因子接口。它不读文件、调用模型、计算收益或重建订单簿，不改变主 `src` 或 revision3。真实 24 股日的读取与落盘由 root 的有界 reader 负责；本文的测试结果不冒充这次真实投影已经通过。

背景证据是 `META_L2_CONTRACT_SAMPLE_AUDIT.md` 的固定 8 股 × 3 日审计。模块把其中可确认的量价关系与不能确认的方向/时序边界分别落实到输出。所有结果均为 `execution_valid=false`、`formal_target_success=false`、`promotion=false`。

## 调用接口

```python
from quanta_agents.meta.l2_daily_features import (
    FeaturePolicy, QUOTE_COLUMNS, TRADE_COLUMNS, build_daily_features,
)

features = build_daily_features(
    quotes,
    trades,
    expected_counts,  # DataFrame: date, wind_code, quotes, trades
    policy=FeaturePolicy(),
)
```

`expected_counts` 定义全部申请股票日及两张原始表的预期行数，行数包含 C 类等非成交记录。每个 `(date,wind_code)` 必须唯一，计数不能未知、负数或为小数。请求表本身有歧义时抛出控制输入异常；原始数据有质量问题时保留对应股票日，返回拒绝行。请求表不含的股票日不会参与计算，也不会影响过去日期的特征或数据摘要。

当前限定 `60xxxx.SH`、`00xxxx.SZ` 主板代码。这是已审计范围，不表示已经通过 ST、停牌、涨跌停或可交易性检查。

最小输入投影如下，公共常量可直接供 reader 使用：

| 表 | 必需字段 |
|---|---|
| 公共 | `date, wind_code, event_ts, time_raw, source_row_no` |
| quotes 附加 | `cum_volume, cum_amount, ask_price_1_x1e4, bid_price_1_x1e4, ask_volume_1, bid_volume_1` |
| trades 附加 | `trade_code, trade_price_x1e4, trade_quantity` |

`time_raw` 按 HHMMSSmmm 与 `event_ts` 交叉核验。无时区的源时间按中国本地交易时钟解释；显式有时区的时间先转换到 Asia/Shanghai。不能混入无法统一解释的时区。源事件必须属于申请日期，不得晚于固定的当日 15:05:00。这个截止值给收盘打印留出明确缓冲，是事件筛选假设，**不是承诺数据在 15:05 已经到达**。

## 普通成交与数量处理

普通成交代码采用市场精确白名单：沪市只能是 null，深市只能是字符串 `"0"`。深市 `"C"` 单独记为排除的撤销类记录；即使 C 类误带正价格也不能进入成交量额。沪市 C、空字符串、整数 0、其他新代码均不被猜测为普通成交，所属股票日进入拒绝状态。

普通成交还必须价格、数量都为正；必需数值不可缺失、非有限或违反整数口径。数量未知不会被 `sum(skipna=True)` 偷换成零。主板正价格须符合本版固定的 `_x1e4` 整数尺度和 100 个缩放单位的价格步长。正数量不强制为 100 的整数倍，历史成交拆分和零股卖出不应被删去。

日成交量是普通有效记录的数量之和；金额为 `sum(price_x1e4 × quantity)/10000`，VWAP 为金额除以数量。三者包含完整当日普通成交，未通过官方定义确认前，不随意删除 15:00 之后可能属于收盘打印的记录。量单位仍标记为 `empirical_share_like; supplier_unit_unverified`，金额是与样本累计额经验一致的候选人民币口径。

最后一条按规则排序的 quote 提供累计量额。成交数量必须与最终累计量完全一致；金额默认绝对误差允许 1 个候选元，并额外容纳浮点计算的 1e-8 误差。累计量或金额发生回退会拒绝，不能挑一个“看起来正常”的较早快照遮盖重置问题。

## 连续交易盘口的时间权重

本版事前固定默认值，不依据策略收益选择：

| 参数 | 默认值与理由 |
|---|---|
| 连续时段 | `[09:30,11:30)` 与 `[13:00,14:57)`，共 14,220 秒；开收盘集合阶段分离 |
| `max_carry_seconds` | 10 秒；允许常见几秒快照间隔，不允许在较大空档长期沿用旧盘口 |
| `min_quote_coverage` | 95%；总连续时长及上午、下午两个时段分别满足 |
| 同时刻快照 | 取该表该股票日内 `source_row_no` 最大的一条，明确表示后一个源快照替代先前快照 |
| 有效双边报价 | 买卖价与买卖一数量均为正，买一价不大于卖一价 |

每条有效快照的权重为以下三者的最小值：距下一条快照的时间、距本交易时段终点的时间、10 秒。下一条即使无效，也会结束前一条有效快照的生命期；不能跳过坏快照后把旧盘口继续填上。午休、集合竞价、盘前盘后均不提供连续交易时间权重，也不从上个时段带入快照。

输出采用时间加权均值：

```text
spread_bps = 20000 × (ask1 - bid1) / (ask1 + bid1)
depth_imbalance_l1 = (bid_qty1 - ask_qty1) / (bid_qty1 + ask_qty1)
daily_value = sum(value × valid_seconds) / sum(valid_seconds)
```

这里只使用一档深度，不能称为十档深度指标或完整订单簿。覆盖不足时整条股票日拒绝，五个数值特征均为 null；当前实现没有让成交类字段单独绕过整行质量门。每时段的覆盖秒数、覆盖比例、无效快照数、长间隔数及最大观察间隔都保留在 `quality`，便于下次讨论是否需要另行冻结分特征的数据门。

## 时间顺序与可用时间

每个 `(table,date,wind_code)` 内稳定排序 `(event_ts,source_row_no)`。同时间戳的不同成交全部保留，不能按时间戳去重。源键重复会拒绝，不能自动选一条“正确记录”。源顺序与时间发生回退的次数会记录到质量摘要，但确定性排序不等于证明原始事件的真实因果顺序。

quotes 与 trades 的同时间戳不被当成有已知先后关系；源行号也不是跨表全局序列。本模块不做逐笔对先前盘口的因果归因、主动侧识别或排队模拟。

真实接收时间未知，所以输出 `available_at=None`。可以显式传入已经冻结的研究模拟时间：

```python
features = build_daily_features(
    quotes, trades, expected_counts,
    simulated_available_at={"2026-01-06": "2026-01-07 09:00:00"},
    simulation_label="t_plus_one_0900_v1",
)
```

映射必须恰好覆盖请求日期，并且模拟时间的中国本地日期严格晚于信号日期。输出会单独写入 `simulated_available_at`、`simulation_label`、`availability_evidence_type="frozen_simulation"`，真实 `available_at` 仍为空。映射和标签参与冻结合同摘要。调用方必须用真实交易日历选择 t+1，模块只验证“晚于信号日”，不会自行假定周一至周五就是交易日。

特征只能在明确的研究可用时刻后被消费。t 日 EOD 特征用于 t+1 买入时，t+1 新买股票最早 t+2 才能卖出；股份可卖时间及公司行动属于独立执行账本，不由本模块制造。模拟时间不消除历史数据实际晚采集、修订或未来复权的来源风险。

## 返回字段与拒绝结果

| 字段 | 输出 |
|---|---|
| `trade_quantity_native` | 普通有效日成交数量 |
| `trade_amount_price_x_quantity` | 普通有效日成交金额 |
| `trade_vwap_price` | 原价尺度 VWAP |
| `quote_spread_bps_tw` | 连续交易时间加权价差 |
| `quote_depth_imbalance_l1_tw` | 连续交易时间加权一档深度不平衡 |
| `aggressor_direction, active_buy_amount` | 固定 null；方向正式契约未通过 |
| `order_imbalance_or_cancel_rate` | 固定 null；没有读入或解释委托事件 |
| `feature_status, accepted, reason_codes` | 仅 `accepted_provisional_research` 或拒绝；失败原因不删除 |
| `quality` | 预期/实际行数、源键与时钟检查、投影摘要、有效/排除/未知事件数、EOD 残差、盘口覆盖 |
| `policy_sha256` | 参数、时段、代码规则、模拟时间等冻结合同摘要 |

完整冻结合同另存于返回 DataFrame 的 `attrs['frozen_contract']`。落盘时调用方需显式保存这份字典，因为普通 CSV/部分 DataFrame 导出不会保留 attrs。逐股票日的输入摘要来自必要列按确定性顺序排列后的 pandas 行哈希，不混称为原文件 SHA256。

主要拒绝原因包括缺股票日、行数不符、必需列缺失、数量未知、重复源事件键、时钟/日期不一致、超过 EOD 截止、新成交编码、累计值回退、日末量额不符、总或分时段覆盖不足。拒绝行的数值特征保持 Python null/None，不是补零；原始预期分母与质量信息仍然存在。

正式执行门始终关闭。缺少的真实到达时刻、供应商字段说明、公司行动现金/股份可卖账、ST/停牌/涨跌停和容量证据，需要各自补齐，不能用研究因子生成成功代替。

## 检验与下一步

测试文件为 revision4 的 `tests/test_meta_l2_daily_features.py`，本轮 **35 项通过，6.32 秒**。已覆盖价格/数量手算、C 类正价仍排除、沪深精确代码白名单、未知数量不补零、缺股票日保留、源键重复拒绝、同时间戳多笔成交保留、同时间戳快照替代、输入扰动确定性、午休与竞价隔离、长间隔上限、无效快照终止旧盘口、单时段覆盖不足、源时钟错误、未来日期不影响过去、跨日/晚于截止的事件拒绝、模拟时间隔离及日末量额核对。

这一步做得怎么样：已把审计中的隐含假设落实成显式接口、固定参数、null 字段和拒绝行，避免用方向猜测、缺值补零或无期限盘口填充产生看似完整的信号。

下一步由有界 reader 使用冻结的 24 股日原始投影调用模块，保存全部通过/拒绝结果及合同摘要，再核对真实覆盖与拒绝原因。结果只说明研究数据入口是否成立，不能证明 alpha、T+1 成交可执行或架构取得收益进步。
