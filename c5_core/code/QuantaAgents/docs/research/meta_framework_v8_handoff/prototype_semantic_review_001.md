# 两题公开原型的独立语义与权限工程评审 001

审查时间：2026-09-07，Asia/Hong_Kong。配置：gpt-6-astra / xhigh。审查对象为 `experiment_traces/meta_calibration_prototypes/attempts/20260906T162839907925Z`，不延伸到后台联合测试或其他尝试。

**结论：有限通过。** 两题已证明受控公开接口下的确定性查询、保存提交及所述数值复核路径可达；当前参考解的可识别性判断和下一检验尚未通过语义验收，不能原样宣布“两题已具备下一轮 Astra 行为校准的完整参考基准”。修复下述边界并另存新尝试后，可以按暴露的开发材料用于最低限度证据使用校准。这个结论不表示已进行研究模型行为评分、隐藏发现验证、真实策略执行认证或架构比较，全面目标仍未达标。

本次只读尝试内的普通包、公开轨迹、提交、账本及审计材料，读取公开参考客户端、控制器 runner 和预先书面标准；只新增本文件。未执行测试、回测、oracle 或模型调用脚本，未读取 2024–2025 市场数据。SQLite 使用 `mode=ro`；对已有 JSON、脚本与归档做了只读哈希核对。未改原提交、回执、源码、测试或失败历史。本次评审独立于参考客户端的执行路径；本审查 agent 此前参与了公共查询器与夹具输入构造，因此不声称全部组件均由无作者重合的第三方审查。

**采用的既定标准。** [评分审查](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v7_handoff/p1_calibration_scoring_review.md:120) 要求结构、数值、语义、权限分开；其第 166、171–173、184–199、216、220–222 行分别约束公开可识别性、同权限参考路径、下一检验与隔离、反馈暴露和启动门槛。[工具就绪审查](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v7_handoff/p1_tool_readiness.md) 要求完整日期、原订单、共同事件、有限查询及零新增研究网关的首轮原型。没有为本次报告修改这些标准，也没有将工程审查替代其中另行要求的正式人工语义复核。

| 此次工程评审维度 | 题一 | 题二 | 可以支持的含义 |
| --- | --- | --- | --- |
| 公开查询与唯一提交的已存证据 | 通过：8 页、1 final | 通过：5 页、1 final | 有限普通输入和工具可达 |
| 已声明可得数值与独立 oracle | 通过：7 项 | 通过：8 项 | 已存数值检查均满足冻结容差；本次未重跑 oracle |
| 主要解释 | 有限通过 | 通过 | 题一对象与时点区分正确，但需明确固定订单范围；题二成本桥接解释正确 |
| 未识别边界 | 未通过 | 有限通过，须限定日期与范围 | 两题共用的全局未知模板过宽 |
| 下一检验 | 未通过 | 有限通过，须收窄 | 统一模板未充分对应两题现有证据 |
| 已执行路径的接口隔离 | 有限通过 | 有限通过 | 可见轨迹无私有反馈；不能据此证明 OS 隔离 |
| 实际 Astra 行为评分、真实策略成功 | 未进行 | 未进行 | 不生成行为得分、成功率或赢家 |

**已核实的正面证据。** [根回执](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_calibration_prototypes/attempts/20260906T162839907925Z/receipt.json) 保留状态 `offline_numeric_paths_passed_semantic_review_pending`，2 planned / 2 completed，errors 为空。两个只读账本分别有 8 / 5 条 queries 和 1 条 submission；规范化首屏空游标后，账本请求、响应、最终 body 与公开保存文件一致。公共包哈希、最终 submission_hash、13 个外层 evidence_id 的响应哈希、每页 binding 与控制器 scope、ZIP/报告原字节哈希及日历内容哈希均相符。查询条数没有被当成独立研究样本。

题一已公开 15 个夹具会话、2 个原订单、1 个闭合段、7 条日末库存、30 条日期/证券输入、2 个共同事件及 3 个期限行；题二已公开 7 个会话、10 个原订单、3 个库存段及 8 条日末库存。零交易日和原订单序号均保留。题二同日 A 先退出后重新入场的序号为 7、8，未被买卖方向排序合并；右删失段仍单独存在。两份日历的 COMPLETE 仅指覆盖控制器冻结的合成会话，不能解释成真实交易所日历认证。

题一两共同事件均是 2020-01-01 的候选信号股日，2020-01-02 次开盘进入，1/5/10 会话分别在 2020-01-03、01-09、01-16 退出；共同集合哈希为 `b302af3f9cf6fa3a1a988e577bb2a5039496acd2bbf016c1e382cc46dd55e9ca`。从公开价格可直接核对两证券端点收益为 `(1%, -1%)`、`(6%, 4%)`、`(10%, 9%)`，等事件均值为 0、5%、9.5%。这不是独立市场样本，报告没有将其说成 alpha、最佳期限或净组合收益。

题二所述 `-92 = -16 - 16 - 60 = -82.55 + (-9.45)` 与公开订单、资金及已存数值审查一致；买卖费用已进入成本与已实现损益，滑点已进入成交价，不能重复扣除。保持相同成交数量路径加回成本只是会计桥接，不是另跑零成本策略。参考客户端逐笔核对了实际卖出 realized_pnl，并从完整日期累计现金和聚合估值；它没有导入 oracle 或读取评测文件。本次确认的是源码和可见轨迹所支持的这些行为，不推断未来模型的内部推理过程。

**缺陷 1：空的原始字段被误当成全局不可识别，阻断当前语义基准。** [参考客户端第 364–366 行](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision8/scripts/calibration_public_reference.py:364) 无条件提交 `per_stock_unrealized_cny = not_identifiable`，两份最终报告均称“没有逐股日末标记”。这个结论必须按证据能力及日期判定，不能从 inventory 中的 null 直接推出。

- 题一 [普通包](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_calibration_prototypes/attempts/20260906T162839907925Z/calibration_01/public_packet/packet.json) 已声明“同一会话 synthetic open 就是日末 mark”，已查询的 horizon_inputs 含全部逐股 open。完整订单表仅有 asset_01 的 100 单位持仓，成本 `100 × 100.1 + 5 = 10015`。2020-01-02 当日可得逐股未实现 `100 × 100 - 10015 = -15`；2020-01-09 可得 `100 × 106 - 10015 = 585`；最终清仓后为 0。即使不使用该估值约定，完整订单证明只有一只库存，聚合持仓市值也能唯一分配给该股票。这里既没有猜真实收盘，也没有使用隐藏 mark。
- 题二全期逐股估值路径确有缺口：2019-12-30、12-31 同时持有 A/B，聚合持仓市值只有一个方程，不能唯一拆成两个股票的估值。但 2020-01-07 仅有 A 的 30 单位，公开聚合市值 264、剩余成本 273.45，A 期末未实现可得 `264 - 273.45 = -9.45`；零库存日则确定为 0。全期多股日的不可识别不能扩大为每一日、期末和所有股票都不可识别。

题一反例使用公开 trades 证据 `evidence-36aab8bfa8da89dd154c62eef2de171e8c10c7a05247b6a28643205c1a298af4`、horizon_inputs 证据 `evidence-3a15f85cd54803cdd0d5d1436d0224fe62177c3022f76de3def67525645c1bae` 以及普通包。题二反例使用 daily `evidence-5b8dc3dc08b6c0046c937dd24820acc96332f9139b871b617fb0e9427ab4232e` 和完整 trades `evidence-2c695a1b0817cb3a5be8655e69048899badd5246e8d9063377b2e8b03c38e5e8`。这些均已存在于最终公开轨迹，不要求追加数据权限。

最小修复：保留原 inventory 未提供字段的 null，修正公共数据字典中“该表没有提供”与“所有公开资料均无法推导”的区别；为 quantity 明确日期/全期范围，参考解区分零库存、单股票库存、明确逐股估值规则和缺少额外约束的多股日期。若继续使用当前仅容纳标量的 schema，可将“全期逐股分解不能完整恢复”写成有日期范围的 limitation，并另用有明确日期/证券含义的公开 quantity 表示可推导量，不用空泛的全局未知代替。不要为维持原参考答案撤去已公开价格或把未知填零。

**缺陷 2：两题相同的下一检验未被各自证据充分支持。** [第 376–379 行](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision8/scripts/calibration_public_reference.py:376) 固定要求“取得缺失逐股估值与未成交订单，解释持仓持续”，并把目标持续/拒单/陈旧估值并列为竞争解释。

题一的逐股合成估值已可得，订单又明确是两笔预先指定的夹具订单，未通过策略生成或拒单模拟。把它归因于“策略选择”超出此记录的生成承诺；查不存在于该生成范围内的策略执行决策链不能成为本题最有区分力的下一步。题二请求多股日期的逐股标记可以改善损益分解；没有拒单日志时不编造停牌或涨跌停是正确的。但当前已保存记录没有显示持仓异常延长，且 reference 没有查询 target_weights，不能先认定这就是主要未识别问题。估值陈旧影响账面市值可信度，本身不能解释订单数量为何持续；须另有策略确实使用该估值决策的证据才能连接二者。

最小修复：按公开能力选择下一检验。题一可继续用同一冻结事件、原订单、全部日期及合法 target_weights，逐项对齐证券集合、进退出时点、资金权重和同路径成本，检验“记录是否可建立会计对应”；若这些对象和时间仍不一致，保留不可直接对应，不推出优胜持有期或交易机制。题二可明确请求 12-30/12-31 与既有聚合估值相同口径的逐股标记，核对分解总和与成本桥接；一致时才支持具体浮盈亏归属，不一致时改变对估值资料可靠性的判断。若另查目标到订单的缺口，先区分“尚未查询的公共目标”与“从未提供的拒单记录”，写清这仅是待取得证据，不声称其存在或已发生拒单。无需新增回测或追加 horizon 搜索。

**题一固定订单与三会话复核的精确边界。** 序号 0 为 2020-01-01，复核序号为 0、3、6、9、12；买入在序号 1，卖出在序号 8，实际连续段时长 `8 - 1 = 7`，日末库存出现在序号 1 至 7，跨过 3、6 两个复核边界。这确实足以演示“复核日历不能替代订单重建的库存段”。但是买卖都没有由每三会话规则推导，target_weights 也只是公开声明的合成开盘前目标记录；不能说“一个每三日调仓的完整策略实际持仓七日”，更不能用本例证明某种真实策略持仓延长机制。题干的“研究定义、初始方案和完整开发执行结果”应在下一版本限定为完整的固定合成执行记录；当前 `complete_strategy_backtest=false` 必须保留。最低限度校准只测证据对象、会话口径和未知边界，不冒充完整原定策略发现题。

**权限结论的范围。** [公开客户端](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision8/scripts/calibration_public_reference.py) 实际只读取 packet 并调用 inspect_execution / submit_diagnostic，未导入生成器、oracle 或文件读取接口。[控制器 runner](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision8/scripts/run_diagnostic_calibration_prototypes.py) 在最终提交保存后才进入私有 oracle 路径；回执 `score_feedback_returned=false`。本次直接检查公共包、initial_prompt、reference_result、final_public_context，均没有对应的私有 canary 或 `evaluator_private` 路径；原回执另记录私有反馈改变后 prompt 不变。13 个页面保持其本 run/architecture/observation/data/strategy/source/report/ZIP/calendar 身份。题二保留旧报告声明的 `a…/b…/c…` 合成身份及原报告/ZIP SHA，没有借新 v8 查询器身份重写旧来源；这些声明与字节绑定不构成真实数据认证。

这些证据支持**受控接口的可见执行路径未泄漏评测内容**。runner 与参考客户端同进程、同 OS 用户运行，接收的是 Python 对象；命名为 private 的目录及对象属性不能阻止同权限任意代码、反射或文件读取。本次没有独立 OS 凭证、拒绝文件挂载、进程沙箱或恶意同权限客户端实验，也没有在本次重跑越权反例。不能把 canary 缺席扩大成全部导出、错误、源码下载及任意代码路径均不可泄漏。后台联合测试未纳入本报告验收，不预判其结果。

**下一阶段的最小门。** 保留本 attempt 与原语义缺陷；只在另一个冻结版本中修复公共 quantity/定义歧义及参考客户端的判定、下一检验，再保存对应离线结果和语义复核。不得把本报告、参考解、oracle、评分表或父任务历史拼入研究器提示。之后若进行实际 Astra/xhigh 校准，应另冻请求/令牌预算、在途预留、停止条件、工具上限和失败分母，使用新的上下文，只交付白名单 JSON 请求/响应，并验证真实网关 `tools=none` 等限制确实生效。若仍只有接口分隔，就明确如此报告，不要求用虚假的 OS 隔离声明掩盖范围。

两题均已暴露，最多支持开发材料上的最低限度行为检查；应分别登记查询使用、合法终态、数值一致、解释、误报/过度弃权及下一检验。脚本已读取原表不等于模型自主重建，普通数据字典和派生表本来就提供部分解释，也不能计隐藏发现。未进行真实策略回测，`execution_valid=false`、`formal_target_success=false`、策略成功分母贡献 0 继续有效，不产生架构赢家。

**工程与研究资源分列。** 此 attempt 保存的新增研究网关调用、新市场读取、新回测和独立真实研究样本均为 0；本次复核未增加这些调用。公开查询数为 8 / 5。两题 registry 各记 6 次查询，其中各有 1 次控制器绑定检查，不能把它们与公开动作数混为一谈；题一期限计算为 1 次、固定 horizon 子尝试 3 次，登记重放各 1 次。上述内部核查均不能充当新搜索机会。本次工程语义审查自身由 Astra/xhigh 完成，工程模型用量非零；没有独立供应商 usage 回执可在此精确汇总，留待 root/平台工程账本列示，不能填作零、称为人工无模型审查或并入研究模型表现。

**两问自检。** 这一步做得怎么样：已确认可见记录、计数、哈希和公开数值的可复核性，并找到数值 oracle 自动跳过 `not_identifiable` 及自然语言后会漏掉的具体语义问题；根回执本来就保留 semantic_review_pending，因此无需篡改成功的数值记录来记录本次未通过项。下一步该做什么：先修公开可识别性范围与两题下一检验的证据对应关系，另存新结果；在此之前不启动以当前参考解为正确答案的行为评分，也不宣称全面目标完成。

本次读取身份（SHA256）：

| 文件 | SHA256 |
| --- | --- |
| 本 attempt / plan.json | `d25e65952f4f78d1b3f4306c4a3707d41aafadbf2113626029d76d7986fa0abe` |
| 本 attempt / receipt.json | `753d553c4b5fdb81cefe00510f8253662d2f71bf5ddb3d876d26063d829c19f9` |
| calibration_01 / public_run / reference_result.json | `c2849536ab912f230a09be8e665ecf6a02a9ddcce020d51796a5d339683f0965` |
| calibration_02 / public_run / reference_result.json | `ff75b450f994527a7e7cb8e1c35b6a1e3a0e9f915ffd79223f46e7cc296b77c0` |
| calibration_public_reference.py，与 plan 相符 | `47ef25923d4109760348cfc10d248f84ae8a550dd00f52f9805f6c8fbf871df6` |
| run_diagnostic_calibration_prototypes.py，与 plan 相符 | `4875cfbf2be8726d35a3fb4c709c0a990b6e551c764d51622d0f30fcd2569dc8` |
| p1_calibration_scoring_review.md | `8a5fddf1852fd8312eed092b6cdc765e8ddae0663a78bb4672fc8b9db4d0fc4a` |
| p1_tool_readiness.md | `85c6969e2eb2badeae7d7ceb70f9057609556fdacca6bcd6ff88c3ddaebe25c7` |

此文属于已暴露的工程评测材料，不是后续研究器的普通输入。
