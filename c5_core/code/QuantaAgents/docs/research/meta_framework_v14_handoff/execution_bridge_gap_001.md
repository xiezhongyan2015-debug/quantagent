# 原字段至日线执行链：最短缺口独立审查 001

审查日期：2026-09-07（香港）；Astra / xhigh 独立工程审查。只读现有源码、冻结计划和报告；新增网络、行情读取、模型研究调用、回测和测试均为 0。本文件是实现建议，不是执行许可或新研究结果。

**判断：已有可用的原股数会计内核和小组合模拟器，最短路径是补接入与明确逐日执行合同，不是继续等待全市场绝对认证。** 现有 846 股字段产物足以支持保存数据上的小范围工程研究；尚不足以把任何默认模拟成交称为真实可执行收益。必要缺口是选定范围内的公司行动、状态和成交约束，以及失败时保存完整资金路径。

依据：v14 四个被审模块分别与既有冻结版本同哈希；原价两批独立核验见 [raw_union_independent_001.md](../meta_framework_v13_handoff/raw_union_independent_001.md)，单次事件取证见 [source_probe_001.md](source_probe_001.md)，原会计链见 [META_RAW_PORTFOLIO_SIMULATOR.md](../../META_RAW_PORTFOLIO_SIMULATOR.md)、[execution_readiness_001.md](../meta_framework_v9_handoff/execution_readiness_001.md)。旧独立算术回执 `meta_raw_portfolio_pilot/attempts/20260906T123752659240Z/independent_arithmetic_audit.json` 已有完整资金、费用、分红与拒单对账；本次未重跑，也不把旧机械结果当策略成功。

## 已具备什么，尚未接上什么

- 两批保存结果为 575 + 271 股、无交集或遗漏，完整网格 846 × 1,217 = 1,029,582 行；历史 membership 559,770 行，其中 11,708 行未通过原字段检查。未通过行和全现金日必须留在原分母。保存名称的 U+FFFD 实际为 0，不能用终端乱码判定损坏；这也不证明历史 ST/退市状态或名称发布时间。
- `simulate_raw_portfolio(daily_data, calendar, targets, initial_cash, corporate_actions, …)` 已接受原字段表、明确日历及带 `signal_date / trade_date / available_at` 的目标。会保留拒单、现金、部分成交、税款预留和持仓。省略股票表示继续持仓，只有显式零目标才退出。见 [raw_portfolio_backtest.py:170](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision14/src/quanta_agents/raw_portfolio_backtest.py:170)、[目标校验:227](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision14/src/quanta_agents/raw_portfolio_backtest.py:227)。它只要求信号早于成交日，严格“下一交易日”仍需接入层核对。
- `RawShareLedger.apply` 有因果时间检查、同 ID 幂等及逐事件原子更新；买入批次下一会话才可卖，回放能重建状态承诺。它没有磁盘事务或整个组合日程的恢复协议。见 [raw_share_ledger.py:326](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision14/src/quanta_agents/raw_share_ledger.py:326)、[买卖:412](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision14/src/quanta_agents/raw_share_ledger.py:412)、[回放:636](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision14/src/quanta_agents/raw_share_ledger.py:636)。持仓缺收盘价时组合函数直接抛错，局部 journal 没有作为失败结果返回，接入层必须先解决失败证据保存，不能丢掉整条资金路径。
- 当前研究入口仍调用调整价权重回测，见 [ashare_case.py:391](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision14/src/quanta_agents/meta/ashare_case.py:391)。因此只增加公司行动文件，不会自动改变研究执行链。
- **sz000001 不在冻结 846 股名单中。** BaoStock 的 2019 `operate` 单条与已审原公告在每股税前 0.145 元及关键日期上一致；这是另一只股票的公开事件对照。接入时应把提供商行附到同一经济事件的来源证据，保留旧事件 ID 和公告身份，不能再生成一次现金权利。其税后字符串有分支，空转增字段不是确定零，支付日不是账户到账时刻。不能据此认证 846 股其他事件或空结果为无事件。

## 必要条件与可以明确建模的边界

| 层次 | 最低要求及可接受做法 |
|---|---|
| 会计、因果及交易约束 | 真实原股数和完整初始资金；现金、费用、已登记股利、未上市权益与欠税均连续保留；T+1、涨跌停、资金和容量约束不能省略。目标所用信息必须在决策时点已可得。持仓缺价、股份变动不明或税基不明不能靠删除持仓、填旧价或重选股票获得一条完整收益曲线。 |
| 有界日线模拟 | 历史字段抵达时间未知时，可冻结“完整前日行情之后才出信号”的日线可得性假设，并明确它未经历史到达验证。公告次会话可见、股利支付日之后的会话才可用现金，是现有适配器的保守调度合同；并不需要先取得每个真实账户的入账秒级记录才能做工程研究。精确分币与显式聚合四舍五入是可选会计政策，不是必须永远停在整分不除尽。费用仍需冻结适用账户和历史费税规则，不能以假设为由归零。 |
| 不能由“保守”一词自动补齐 | 现有前日成交量 × 5% 只是容量估计，前日名称推导今日 5% / 10% 限幅只是状态代理；固定 0.1% 滑点也不证明开盘成交。尤其函数用实际开盘价决定最终股数，同时按开盘价加滑点成交，不能描述成开盘前已下达的确定股数订单。须明确目标单如何转成可执行订单、成交窗口及容量证据；缺少支撑时拒绝新增交易或保留条件模拟，不能宣称已验证实际容量。没有必要把取得全历史逐笔队列作为所有日线研究的统一前置条件。 |
| 工程自加的认证门 | `allow_incomplete_for_integration=True` 是无条件显式门，所有结果固定 `execution_valid=false`；覆盖工具把 `declared_simulation` 也列入 blocked，且限 16 股 × 32 会话。它们比“允许有明确假设的开发研究”更强，不是数学或法规要求。可在 controller 另设研究准入与接受的假设清单，保留原覆盖结果和所有 false 标志；不能把模拟假设改写为 verified 来通关。 |

上述模型边界直接见 [成交政策与容量:259](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision14/src/quanta_agents/raw_portfolio_backtest.py:259)、[持仓缺价失败:461](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision14/src/quanta_agents/raw_portfolio_backtest.py:461)、[适配器:201](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision14/src/quanta_agents/corporate_action_adapter.py:201)、[覆盖政策:24](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision14/src/quanta_agents/meta/execution_coverage.py:24)、[覆盖阻断:207](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision14/src/quanta_agents/meta/execution_coverage.py:207)。本审查引用既有冻结历史规则，不是新的法律核验意见。

## 最多三项优先行动

1. **先接保存数据和持久失败链。** 新 controller 从两批已保存 `rows.json.gz / source_manifest` 核对哈希及统一计划，恢复所需列与原始价格字符串；不重读外部 CSV、不自动补价，不按当日完整 OHLC 的事后 accepted 标志筛选开盘机会。冻结日历、账户、全额本金、目标和来源身份，保证目标时间与下一会话严格对齐，显式记录清仓零目标。对模拟器增加逐日或逐事件持久输出边界，先保存尝试与输入，再保留成交、拒绝、余额、义务、最后可验证状态和错误；恢复只对账保存事件，不盲重跑。
2. **用很小且不因结果换样本的矩阵完成协议接线。** 可选公开工程矩阵：父计划原顺序前两股 `sh600004 / sh600006`，原日历自 2019-06-20 起连续 16 会话至 2019-07-11；首日作为滞后输入，初始仍沿用旧对照的完整 100 万元，固定一次目标进入和一次显式退出，不做参数选择。所有缺失、不可交易和余额原样保留；真实成交前还须补齐下一项。sz000001 继续使用原已审现金分红对照，单独分母，禁止塞入 846 股策略池。此矩阵用于暴露开发接线，不评选策略；义务越过末日则明确右删失或另行冻结延长日历，不能结账抹平。
3. **只补实际触及范围的执行与事件合同。** 为这两股、下单日及持仓/权利存续期准备冻结的交易状态、限价参考、成交窗口/量纲/容量政策、账户费税和公司行动清单。现金事件经现有适配器即可；送转、配股、上市权益等遇到现内核或适配器不支持的情形，保留失败并停该路径。新增下单所需字段未知可拒单；已持有资产或已登记权利的重要现金/数量事实未知必须中止数值结论。事件覆盖应覆盖这些经济暴露，而不是要求先证明全 846 股五年内每个无事件日的绝对真空。第三方事件清单可作为注明能力范围的来源候选，原公告用于必要冲突或关键条款核对，无须每个字段额外索取不可能的绝对身份认证。

**什么证据会推翻这条短路径：**（a）两批保存行无法与原计划、原始价格字符串或单位一致绑定，则第 1 项不能接入；（b）固定小矩阵出现未支持的股份变动、必要持仓估值缺失或未能确定的已登记权利，则不能完成该资金路径，也不能换股掩盖失败；（c）可靠交易状态或成交证据否定前日名称、5% 容量或开盘成交假设，则相应模拟成交无效，必须改成拒单或重新冻结执行模型，旧结果保留。

两问自检：本轮推进的是保存原字段到可审计日线模拟的明确接线方案，并指出平安事件与 846 股分母不能混用；尚未完成的是小矩阵执行状态/事件范围及持久失败接口的实际实施与验证。没有因此新增一次回测、取得真实执行资格或降低资金、费用、时点和失败保留标准。
