# P1/P2 工具准备审计与两道诊断校准题

2026-09-06，revision7 冻结接手材料。结论：**成本/持仓的描述性归因已经有可复用的零模型校验内核；完整期限曲线仍缺共同样本与观察对象绑定；真实 A 股收益评分仍不能通过执行有效性门。** 先完成两道诊断校准的小闭环，再讨论 8 题或架构比较。

本次只读代码、文档和明确保存的开发/合成结果，并新增本文件。没有执行模型、回测、测试、生成行情、读取 2024–2025 市场记录或修改 revision7 源码/tests。下文“已验证”指读到的历史独立证据，不冒充本轮重新运行；两道新题与新增接口是待实现设计。

## 1. 核验对象与当前接口

以下路径均相对项目根目录，源码以 `experiment_traces/meta_ashare_revision7/src/quanta_agents/` 为基准；主目录同名文件只用来辨别旧接口，不作为冻结 v7 功能判定依据。

| 入口 | 当前能做什么 | 对首轮校准的限制 |
|---|---|---|
| `meta.ashare_case.AShareCase(config)` | `include_execution_diagnostics=True` 显式开启执行诊断；manifest 冻结数据清单、研究定义、执行参数；默认 final 封存 | 配置开关默认 false；原价模拟器没有接到 evaluate；实例化真实默认配置会涉及真实数据清单，零模型题必须显式指定合成 fixture |
| `case.development_packet()` | 提供普通任务、起点、字段/表达式/执行说明，数据加载只到 development 截止日 | 没有任意文件、终端、网络、逐行未来字段入口；应保持这一边界 |
| `case.evaluate(strategy, split="development")` | 由因果表达式生成目标权重，调用 `run_target_weight_backtest`；返回全资金日收益、费用、回撤、年度分解等 | `execution_valid=False`、`capacity_verified=False`；可比较原近似模拟，不能评分为合格真实策略 |
| `case.diagnose(strategy, expression, horizon=5)` | 单个 1–20 交易日的 gross next-open 事件收益、全样本五分组、年度分组、同日高减低分组差 | 没有多期限共同样本/事件 ID；样本是过滤后合格股日，不是 top_n 选股、调仓日或实际成交集合；分组为 pooled development 分位点，仅描述性 |
| `case.execution_diagnostic_report(strategy)` | 返回已计算开发策略的完整诊断；不启动额外回测或市场读取 | 是公开 Python getter，但当前模型动作 schema 没有独立查询它的动作；普通 backtest 观察只含有界摘要，完整表为控制器 artifact |
| `build_execution_diagnostics(daily, trades, target_weights, *, capital, identity, policy=None)` | 纯 DataFrame 会计复核；不读市场、模型、网络或外部文件 | 接受 `adjusted_price_units_approximation` 且 identity 必须是 development、execution_valid=false；不是原股份税务账本 |
| `diagnose_execution_zip(path, *, expected_sha256, capital, identity, policy=None)` | 只读指定且哈希绑定的 daily.csv / trades.csv / target_weights.csv 三成员 ZIP | 必须同时验证策略/数据/源码/观察语义归属；字节哈希一致不代表归属正确；ZIP 往返经济一致不保证完整 JSON 完全相同 |
| `meta.ashare_research.action_schema(...) / run_step(...)` | 模型经 JSON 请求 diagnose/backtest/submit；固定计划可用 diagnostic_not_identifiable、no_supported_strategy | 每个动作仍要求 strategy/diagnostic_expression/horizon 占位；diagnostic_not_identifiable 消耗诊断槽但不是独立最终诊断提交；不宜把诊断题硬记成合法策略提交 |
| `meta.benchmark.development_packet()/evaluate(strategy, split)` | 公开 seed、公开生成器的单资产 synthetic 研究环路冒烟检查 | 可重构、并非隐藏机制正控；允许分数股、rf=0，其 Sharpe 不能直接套用 A 股 rf=2% 的正式口径 |

当前 `diagnose` 的精确定义为：信号日 t 收盘后观察特征，假设 t+1 开盘买入，t+h+1 开盘退出，`open[t+h+1]/open[t+1]-1`。尾部没有完整 h 的事件被剔除，不跨 split 补数据。不同 h 分别生成 mask 和分组边界，不能直接把各次均值连接后宣称发现收益期限衰减。

`_weights` 返回的是固定资格、有限评分和非零筛选同时满足的股日集合；实际权重只在调仓日选 top_n。`diagnose` 用前者作样本。因此“筛选合格股日”“选中股票日”“实际成交入场”“连续持仓段”至少是四个不同观察单位。此差别是题目要允许研究器自行核对的正常工具说明，不能由评审事后替它补足。

执行诊断有两条逐日及全期恒等式：

```text
净 PnL = 同一已成交数量路径的参考价格 PnL - 费用 - 滑点
净 PnL = 已实现净 PnL + 未实现 PnL 的变化
```

已有滑点已包含在成交价、资金与数量中；加回费用/滑点是对同一成交路径的会计桥接，不是改变成本后重新选择数量和交易的反事实策略。连续库存段不是 FIFO 税务批次；部分减仓不是一次完整退出；期末仍持有的段保持右删失。

## 2. 两道首先做的校准题

只把下面“研究器普通输入”及正常可用资料交给研究器。题目内部标签、机制、合成生成器、oracle、参考解、预期数字和评分表全部放在可信评测区。不能把本文件整篇放进模型上下文。对外题号使用中性 `calibration_01/02`，不暴露“期限错配”“双扣费”等答案标签。

### 题一：事件观察与已执行组合的对应关系

研究器普通输入：

> 有一项按日识别市场现象的研究，资料中保存了研究定义、初始方案和一次完整开发执行结果。请检验现有证据能否回答这个研究问题，解释你认为最需要核对的差异，并选择能区分主要解释的下一项检验。可以报告证据不足。

正常输入材料：匿名但有日期/字段含义的合成日线环境、普通初始策略及声明的收盘决策/次开盘执行规则、一份完整执行摘要；提供与常规研究相同的表达式和工具说明。不额外告诉模型“5 日调仓不等于 5 日持有”，不标明何时反转，不给优胜期限或规则。

可信评测端的构造要求（不进模型输入）：

- 固定多个合成证券与可独立算出的事件后开盘路径。首个可识别版本让连续持仓跨越至少两个调仓边界，实际持有期仍在现有 1–20 日窗口内。用独立库存状态机取得真实连续段，不从 rebalance_days 推导答案。
- 用明确解析路径产生“短期观察与较长持仓覆盖不同价格变化”的实例，并另有不会反转/没有差异的实例；不能让题名、证券代码或样本编号暗示哪种机制。后续改变随机实现、机制族和信号密度，不能仅换 seed 而保留明显模板。
- 第一轮先提供完整 horizon 共同覆盖的非重叠事件，独立枚举所有入场、终点与分组。随后单独加入近截止日事件、重复/重叠事件和持仓右删失，以检查工具会不会静默删掉损失或把不足当已识别。
- 预注册检查只要求识别观察单位和时间口径、用合法工具获得相应证据、保留不可识别内容，并提出可推翻的下一项实验。不要要求特定表达式、特定“获胜”持有天数或一定作出有 alpha 的结论。

现成可用路径：`case.evaluate(..., "development")` 的 execution_diagnostics.summary 提供实际连续持仓段分布；多次 `case.diagnose(..., h)` 可提供不同 h 的描述性观察。**当前完整题尚未就绪**：研究器没有公开共同样本身份，不能仅凭相同 observations 数量证明事件集合相同；也不能把候选股日分组自动认作实际成交持仓检验。第一轮可零模型验证这两个现成原语，最终的可自主解题验收需补下一节的共同样本接口。

独立正控复现：另一个不导入被测 horizon 计算函数的实现，从原始合成日期/开盘价逐行列出 t、t+1、t+h+1，用 Decimal 或明确分数算收益；另用订单数量手工/独立状态机重建库存段。参考研究解必须只通过研究器同样可用的公开工具和普通资料得到结论，不能读取隐藏未来数组、直接调用私有 `_load/_weights` 或引用 oracle 标签。两条独立路径均通过后，这题才能被用于比较发现能力。

评分分开记录：观察单位识别、入退出时点识别、共同样本检查、重叠/截止不足处理、对持仓段与调仓频率的核对、合法下一检验及诚实弃权。不合成收益分；不进入真实策略成功率分母。

### 题二：保存的资金与持仓报告能解释什么

研究器普通输入：

> 这份开发执行结果没有达到研究目标。请根据保存的资金、订单和持仓资料核对结果，说明哪些损益来源可以确认、哪些判断还缺资料，并给出最有信息价值的下一步。所有成交、未结束持仓和没有交易的日子都保留。

正常输入材料：初始资金、完整日期覆盖、普通策略/执行说明，以及已有有界执行诊断摘要。若希望研究器逐项自行复核，需要提供基于当前运行已登记 artifact 的公开只读分页查询；不能让模型随意读取文件系统，也不能把含 expected 字段的评测端 plan.json 暴露给模型。

可信评测端的首个原型可复用已保存 7 日、10 订单、2 个合成证券的独立 Decimal 路径。保存证据的答案为：初始现金 20,000，期末现金 19,644、持仓市值 264、余额 19,908；净 PnL -92；同路径参考 PnL -16、费用 16、滑点 60；已实现 -82.55、未实现变化 -9.45；闭合段 -129.10 和 +49.60；另有右删失段。这些数字只用于 oracle，**不要放入普通题干或未请求的提示**。

该路径包含加仓、部分减仓、库存归零后的同日重新入场、盈利与亏损段，以及缺少逐股日末标记时的未识别部分。新的盲测版本应另行生成金额、顺序与机制组合；已经公开且本次阅读过的这一原型只能作开发校准，不声称隐藏测试。

当前就绪程度：**有界摘要层面的诊断已具备**。`include_execution_diagnostics=True` 的正常 backtest 返回 scope/identity/integrity/summary，足以识别桥接关系、持仓段/右删失、原价认证缺口。完整表目前只能由控制器公开 getter 获取，尚未成为模型的独立研究动作；若评分要求模型逐日/逐段查账，必须先加公开只读查询，而非把工具无法获取资料算成模型失败。

独立正控复现要用原始订单和现金公式独立累计，不能通过调用同一 `build_execution_diagnostics` 生成期望值。必须核对每个归档日、年度分解、全部订单及正负段；故意把 realized_pnl 改错 0.01、删除现金日、漏掉右删失段、或混入错误 strategy/data/source 归属时，应拒绝或明确指出缺口。正确参考解只使用模型同样的查询工具；oracle 可检验参考解数值，但不能给参考解额外交易信息。

评分分开记录：现金/净资产恒等式，是否重复扣费，已实现/未实现区分，连续库存段/部分退出/右删失解释，是否把成本加回误当反事实收益，是否虚构缺失字段。两条会计恒等式复核容差沿用冻结诊断政策 CNY 1e-6，收益关系 1e-12；不要根据实际误差调宽。

这题是可判定诊断能力校准，正确指出“无逐股日末价格，无法归因逐股浮亏；无拒单日志，无法判断是否停牌导致久持”属于正确信息边界。归因报告再准确也不是产出了一条策略。

## 3. 最小实现切口（下一隔离修订；本轮不改冻结 v7）

优先顺序是公共证据接口、独立 oracle/门控、再接模型；无需先增加 agent 角色。

1. **共同样本期限工具。** 新增受控 `diagnose_horizons(strategy, expression, horizons=(1,5,10,20), *, sample_policy="common_full_horizon", observation_unit="eligible_signal_stock_day")`。此签名为建议，当前不存在。一次冻结 horizons，最初仅使用原字段和 development。以最大 h 的可用终点构造共同 mask；固定共同分组边界；返回每个 h 的结果、共同样本哈希/总数、按日期/股票的覆盖、各 h 单独可用数、共同样本删除原因与数量、终点截止日、依赖说明。完整事件 ID 与时间戳放受控 artifact。不得仅添加循环调用原 diagnose 后拼接结果。
2. **把实际持仓证据接成可查询动作。** 建议 `inspect_execution(strategy_hash, *, table="summary", cursor=None, limit=50)`，只允许本运行、本架构、已有合法 development observation 的已登记 artifact；table 枚举 summary/daily/spells/inventory，不允许路径。源ZIP/报告/策略/数据/观察语义绑定仍执行。`summary` 为当前有界原样证据；全表按时间排序分页，返回总行数、截止和下一游标，不能按盈亏选取。getter 缓存缺失不得静默重跑；若使用已有恢复接口进行原策略重建，要独立记录 model_calls=0、search_opportunities_added=0 和重建次数。
3. **独立诊断题终态与评分。** 在校准适配器声明 `task_kind="diagnostic_calibration"`，允许结构化 `diagnostic_report`/`insufficient_evidence` 终态；不强迫填策略占位再算合法提交/策略成功。本建议不是 v7 已有动作。首轮判断用独立数值/来源校验和明确人工复核规则即可，不能为了评分额外暗中调用研究模型。将发现、误报、不可识别、执行有效性和研究资源分别保存。
4. **查询与内部尝试记账。** 每个 horizon、条件分组、候选/失败都登记子尝试；一次批量函数不是一次独立研究样本。区分动作次数、缓存命中、实际回测次数、恢复重放与新的搜索机会。首轮只冻结小范围 horizons 和表查询，不在工具内部自动扫描几十个参数或对得分排序。
5. **匹配对照放下一步。** 当前同日 high-low 只控制部分日期构成，不能控制股票/行业/风格差异。待定义 decision-time 协变量、同日期共同资格/成本、重叠处理、匹配可用性/缺失分母后，再加匹配对照；不能在首轮把简单分组叫因果归因。

最小校准包要求三个隔离区：普通输入与工具契约、冻结原始 fixture 数据、评测端 oracle/机制/答案。包级清单绑定各自哈希、样本截止、来源暴露状态、生成器版本、执行/评分政策、资源和停止规则。研究器工具只允许普通输入与受控证据；测试应实际证明无法取到 oracle，而非只在提示词说“不要看”。

## 4. 已存失败与证据如何复用

| 已存证据 | 可以复用的事实 | 不得声称 |
|---|---|---|
| `docs/META_ASHARE_DEV_BATCH_01_REPORT.md` 与 `experiment_traces/meta_ashare_v2/batches/dev_20260906_01/independent_audit/audit.json` | 8 个计划、7 完成1失败、91调用及91份执行ZIP；归档身份显示开发加载截至2021-12-31。候选全部不使用事件诊断而增加组合试验，说明期限工具可用性需要校准 | 不能说已证明“正确期限诊断”改善架构；候选没实际做这些诊断。共享路径/年份不能增加独立金融样本 |
| 同批相对强弱/量能归档与逐运行 accounting/details 文件 | 已保存亏损/未达标回测可做成本桥接、持仓段重建；以已登记 ZIP 哈希、原策略/数据身份只读复用；失败运行的已完成观察仍属开发证据 | 不补第7轮或最终提交，不把最高中途回测替代实际最终提交；不删除失败/重复路径/现金日 |
| `experiment_traces/meta_execution_diagnostics_review/review_v6/bounded_accounting_check/accounting_result.json`、`plan.json`、`synthetic_execution.zip` | 已保存独立 Decimal 会计验证与正负/右删失反例，可作为题二原型及 oracle 开发材料 | 此原型已暴露，且合成，不能称新盲测或真实策略成功 |
| 同目录 `recovery_before_semantic_fix/result.json` 与 `recovery_after_semantic_fix/result.json` | 错误 strategy/data/source/summary 归属的修前失败和修后拒绝矩阵，可校准证据身份；保留修前错误 | 不把文件哈希一致当语义正确；不把保存答案重新导入看成新独立研究启动 |
| `experiment_traces/meta_raw_portfolio_pilot/attempts/20260906T123752659240Z/independent_arithmetic_audit.json` | 明确保存了固定 9 股原价试点，6 个估值日、63请求股票日（含输入覆盖）、16成交、2次未上市拒绝、22事件重放的历史核算；净PnL -7,196.63，execution_valid=false | 不是完整股票池/多年公司行动或真实成交验证，不计算可认证Sharpe，不选择该短路径作为策略样本 |

原价试点的旧零滑点尝试 `20260905T185839345618Z` 继续保留。最新已读的明确日期尝试不是通过本轮重新运行得到；其 receipt 曾标 pending independent audit，另有独立 audit 文件确认固定案例核算通过，应分别保留二者时间与含义。

Q 网关失败及已保存完整模型回答可继续用于 P0 请求回执恢复验证。它们不是诊断题正控、没有新增研究独立性，不能为让题目显得可解而付费补跑或把旧失败改成功。新恢复产生的材料若供元设计者阅读，应登记反馈暴露；研究器评测输入不能夹带旧候选指导、提示技巧或其他架构轨迹。

## 5. 原价真实执行仍缺哪些门

v7 已包含独立接口：

```python
simulate_raw_portfolio(*, daily_data, calendar, targets,
    initial_cash="1000000", corporate_actions=(),
    allow_incomplete_for_integration=False, participation_rate="0.05",
    slippage_fraction="0.001", rounding_policy="exact_only",
    start_date=None, end_date=None)
```

调用必须显式 `allow_incomplete_for_integration=True`；返回恒为 accounting_integration_only、execution_valid=false、formal_target_success=false、company_action_coverage_complete=false。目标必须绑定 symbol、signal_date、trade_date、带时区的 available_at 和权重；缺省股票维持库存、显式0才退出。日线研究 evaluate 目前仍走旧 `run_target_weight_backtest`，不能因为同一目录已有原价函数就说已经接通正式收益链。

| 门 | 当前证据与缺口 | 下一最小验证 |
|---|---|---|
| 原价/研究价格的身份与时序 | 研究价为历史锚定调整尺度；原价执行有raw open/previous reference及次日目标要求；历史价格/字段真实到达时间仍未知 | 将冻结开发事件的因果策略权重转为带signal_date/next-calendar trade_date/available_at的原价targets；逐项校对数据身份，扰动未来OHLC/成交量不得改变当时开盘决策 |
| 全部持仓日估值与完整日历 | 原价函数缺持仓有效收盘就拒绝；外部日历完整性未独立认证。旧日线模拟允许缺价沿用旧标记 | 逐日对照独立日历，保留缺失/不利持仓日与拒绝，不静默跳日或按原名称猜停牌；明确估值缺失策略及来源 |
| 公司行动与税务 | 原股份账本、现金分红和部分税务路径已有独立试点；全池分红/送转/配股/合并退市/批次税务谱系不完整 | 限定真实最小范围并对其中每股每期行动作完整覆盖证明，覆盖不足仍阻断评分；范围在看收益前冻结，不能事后删除持仓亏损行动 |
| 点时资格与交易状态 | 历史成分区间、上市/ST/退市、价格带/停牌的完整公告时序不充分；主板名称代理不能代表全部真实规则 | 引入独立历史状态与公告可用时间；IPO/ST/停复牌/除权参考价边界的拒单证据必须逐案核对 |
| 实际原股份/T+1/现金 | 原价账本支持整百买入、零股完整退出、T+1实际批次FIFO、现金及税预留；现主evaluate仍是调整单位近似 | 独立逐订单复算股份、可卖库存、现金/应收/税准备金，含部分退出/同日回补/未上市资金闲置；不能靠adjusted-unit T+1标记认证 |
| 成本与成交 | 原价模拟滑点入实际价格、分笔费用舍入、触价带拒绝；費率属于冻结模拟假设，不是券商实收费证明 | 核实范围内历史费用与对账，固定滑点/限价/最小佣金敏感性；原路径加回成本与改变成本后重跑分别记账 |
| 容量与成交条件 | 昨日成交量5%为名义上限；无开盘集合竞价深度、队列和实际fill证据 | 冻结保守容量与拒单政策并获适当数据验证，报告成交缺口；没有证据不设capacity_verified=true |
| 长历史吞吐与保存 | 小账本/单事件优化不证明多年多股多公司行动性能；税务全日志扫描仍有规模风险 | 固定机械组合上先做完整日历与事件分母的性能/崩溃恢复测试，禁止为提速删历史批次、损失或税务检查 |
| 正式评分 | 日线目标口径是全资金、全部成本/现金日、252年化、ddof=1、明确2%无风险；原价函数不输出晋级策略指标 | 执行各门通过后才接统一评分；rf=0另报敏感性。保留风险/交易数/覆盖/容量与冻结样本外、前瞻验证门 |

以上多数不是简单代码开关。若最小真实范围仍不能给出完整公司行动/状态/价格/时序与保守执行证据，该题只能测流程或诊断，不得进入真实策略成功率分母；允许弃权不等于可放宽这些门。

## 6. 首轮通过条件、停止与两问自检

首轮模型预算为0：构造两题的冻结普通包和独立 oracle，检查公共接口可达性，跑原语与负例，保存新目录结果。原型与隐藏变体分开；只有参考研究解在相同工具、信息、成本与约束下可独立复现，才允许后续固定 Astra/xhigh 的最小模型校准。参考解不能使用比研究器更强的数据权限来证明“题可解”。

出现共同样本不一致、时间越界、预期数字由被测函数自身生成、成本重复扣除、未知字段被猜出、oracle可被研究工具获取、来源错配或执行未合格却计为策略成功，立即停止推进该题。任何修复另存版本与反例结果，不覆盖旧失败。后续模型调用总预算、在途预留、试验/工具子尝试数和停止机制必须在启动前单独冻结；本文件没有授权新的真实运行。

**这一步做得怎么样？** 已将现有接口、来源绑定和有限经济验证与尚未实现的能力分清，发现单期限诊断无法安全拼共同样本曲线、完整执行getter尚未进入研究动作、原价模拟器尚未接入当前收益评分等具体缺口。已保存结果支持复用亏损/会计反例，不支持正式收益或架构能力结论。本轮没有重跑历史测试，接口就绪性仍需下一隔离修订的零模型实测。

**下一步该做什么？** 先完成题二原型的公共证据查询与独立评分闭环，再补题一共同样本工具并用独立时序/库存 oracle 验证。若题一在公共工具下不可识别，修题或承认不可识别，不以提示答案提高通过率；若题二会计桥接正确但模型仍捏造拒单原因，记为误报。先评估诊断发现/误报/弃权与执行有效性，暂不扩样做收益架构赢家判断。

本次读取时源码身份（SHA256）：

- `meta/ashare_case.py`：`c0c213e5ad6325a7353f95505566ec1df514b93a7c3375418a0f747177ee3893`
- `meta/benchmark.py`：`d05a1f838d45856a0af6bb1466897f57f1f9ff68203951ff1bfbfcab0c99f1d4`
- `meta/ashare_research.py`：`60b5ccad8b6c0ea3a0518cdaa6ab60ca3952315605ecd23a8cd598158cde6320`
- `meta/execution_diagnostics.py`：`bfb496de608691a9cde916b991a4c721e94b82b2cab4ed4dc2309bbff2b8d1b1`
- `raw_portfolio_backtest.py`：`1e68e6c37d1e6aa5b2158cba22532d21557fc046f4f63f16a83cb2c36299143f`

主要证据文档：`docs/META_FRAMEWORK_IMPLEMENTATION_PROMPT.md`、`docs/META_RAW_PORTFOLIO_SIMULATOR.md`、`docs/META_ASHARE_BENCHMARK_CASES.md`、`docs/META_ASHARE_PROTOCOL_DRAFT.md`、`docs/META_EXECUTION_DIAGNOSTICS_REVIEW.md`、`docs/META_ASHARE_DEV_BATCH_01_REPORT.md`、`docs/META_RAW_LEDGER_PERFORMANCE_REVIEW.md`，以及上述明确列出的存量审计JSON。
