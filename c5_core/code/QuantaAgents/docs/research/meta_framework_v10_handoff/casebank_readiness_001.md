# P1 八题开发库与 P4 比较准备：只读差距核验 001

2026-09-07，Asia/Hong_Kong。审阅配置：继承 Astra/xhigh。**目前具备两道已暴露诊断题的受控执行链、五个真实研究题的普通起点注册；尚不具备冻结的八题开发库，也未具备正式 P4 赢家判断条件。** 新实施提示的八题构成，与旧六题草案是不同层次的清单，不能相加或改名视为已经完成。

本轮只读文档、源码和已接受的两份普通 packet；未构造 Engine、打开运行账本、运行模型或回测、生成新题/隐藏机制/未来答案，也未读取真实行情或封存的 2024–2025 市场数据。唯一 campaign `diagnostic_20260906T172850309804Z` 由 root 负责。本报告收尾时主控通报：已结束，13 次调用全部完整、238,797 token，两题因未充分公开的提交约束（非 numeric 单位、未登记 quantity）失败，没有重复模型调用；v11 正隔离修复提交合同和只读 GUI，下一付费仍关闭。这些是主控提供的运行事实，本审阅没有另读账本或独立重算该费用。**本审阅子任务新增项目 CodexGateway 调用为 0**，不能扩写为当前全项目或接手期间调用为 0。工程审阅模型用量非零，精确 token 数不由这里的研究账本计量。

## 1. 八题逐槽位状态

依据 [实施提示](../../META_FRAMEWORK_IMPLEMENTATION_PROMPT.md:46)，八题是 2 诊断、2 隐藏可发现机制、2 无信号/机制失效、2 真实开放研究。下表的“槽位”只是该要求的审计索引，不是新建题号、题目或隐藏答案。

| 槽位 | 已有实现及证据 | 仍是设计/待冻结部分 | 对策略产出与真实执行的边界 |
|---|---|---|---|
| 诊断 1 | `calibration_01` 普通包、共同样本期限表、完整候选/排除表、固定执行记录、分页、唯一 final、独立公开参考与数值 oracle 均已有；v9 语义复评通过 | 主控报告本次付费小闭环在公开提交合同边界失败；须保留原尝试，先完成 v11 合同修复和独立验收，不能提前判模型诊断通过/失效 | 已暴露合成固定订单，不是完整策略回测；策略成功分母贡献 0 |
| 诊断 2 | `calibration_02` 普通包、资金/订单/连续持仓与可识别性证据、公开参考与独立 oracle 已有 | 同上；脚本参考可达和调用完整，不等于最终动作已合法应用或模型已自主识别 | 已暴露合成记录；识别不了的逐股量不能填零，聚合可识别也不代表逐股全可识别；策略成功分母贡献 0 |
| 隐藏可发现机制 1 | 没有找到符合八题合同的已冻结公开包、隔离生成器和独立同预算参考回执 | 机制族、随机实现、独立参考可达性、公开信息/工具/成本约束、允许不同有效解法的评分需先固定 | 旧 `benchmark.py` 只能作公开合成联调，不能占此隐藏正控槽 |
| 隐藏可发现机制 2 | 同上；也没有第二个合格机制族及独立参考链 | 不能把同一公开模板换 seed/题号计为第二个未见机制；本轮不设计或生成它 | 合成机制即使通过，也不能混入真实金融策略成功率 |
| 无信号/机制失效 1 | 旧 N01 给出了 `null_score` 设计合同；当前 `FEATURE_COLUMNS` 没有该字段，TASKS 无 N01 | 可信隔离字段、不可选 seed、仅因果 null 特征、相同资格/资金/换仓的对照及增量评分未实现 | 原始多头 Sharpe 高不等于 null 有预测力；负控的正确弃权单列 |
| 无信号/机制失效 2 | 未找到第二个可执行负控或失效校准题的注册、受控输入与评分 | 需先明确相对哪个基准、哪个识别对象无优势，以及样本不足与失效的区分；不能拿任意失败真实题冒充已知无信号 | 负控结论不能靠低功效下“没有显著性”自动认证；本轮不造新失败机制 |
| 真实开放研究 1 | 已有五个注册起点可供后续按事前规则选入，见下表 | 八题计划尚未固定究竟选哪一个、普通文本或带基线输入模式、暴露状态、合法弃权及评分合同 | 当前研究 `evaluate` 仍走调整单位近似，`execution_valid=false`；不能计为合格真实策略产出 |
| 真实开放研究 2 | 共用同一个真实 case 适配器，但并未因此自动形成第二个完整独立任务 | 与上一槽独立冻结题目/家族/暴露与配对资源；共享市场日期不等于两个独立金融样本 | 相同执行阻断；两个真实槽也远少于正式政策所要求的至少六个真实留出案例 |

`DiagnosticWorkbench.freeze_packet` 目前明确只接受这两个中性题号、`exposed_development_prototype` 和固定诊断政策（[代码](../../../experiment_traces/meta_ashare_revision10/src/quanta_agents/meta/diagnostic_calibration.py:46)）。这是一道真实限制；不能把另六题 JSON 放进去就声称八题已接通。v9 [语义复评](../meta_framework_v9_handoff/prototype_semantic_review_002.md) 和 v10 [live 接入审查](live_readiness_review_001.md) 也只支持上述有界阶段。

## 2. 旧六题草案与实际注册的对应

[旧题库草案](../../META_ASHARE_BENCHMARK_CASES.md) 是五个真实题加 N01，共六题；五个真实题来自三个家族。对照 [ashare_tasks.py](../../../experiment_traces/meta_ashare_revision10/src/quanta_agents/meta/ashare_tasks.py:4)，当前五个普通定义/基线已经全数注册，R02/T01 不再只是“需要任务登记”的文档状态。这里只读 AST 与实现，没有调用行情加载或再次回测。

| 草案 ID | 代码 task key | 已实现 | 未完成/不能声称 |
|---|---|---|---|
| R01 持续下跌修复 | `price_repair` | 任务定义、基线、通用因子/组合研究路由 | 已是开发题；不能称新增留出题 |
| R02 单日急跌修复 | `sharp_drop_repair` | 普通定义和对应因子起点已注册，可进入共同适配器 | 注册不证明从未暴露；缺八题正式选入及逐运行暴露核验 |
| T01 区间突破 | `price_breakout` | 同上 | 同上；定义可解析不证明价格机制有效 |
| T02 中期相对强弱 | `relative_strength` | 普通定义、60 日相对排序起点与共同适配器 | 必须核对历史轨迹暴露；不是新市场时期 |
| V01 放量上涨 | `volume_expansion` | 普通定义、成交额相对过去均值起点与共同适配器 | 不含“主力建仓”或旧优胜条件作为答案；仍缺正式机制归属判定 |
| N01 null | 无注册 | 仅合同草案 | 没有 `null_score` 受控适配，不能直接执行草案 JSON |

五个真实起点都会进入 [AShareCase.evaluate](../../../experiment_traces/meta_ashare_revision10/src/quanta_agents/meta/ashare_case.py:382)，使用同一近似回测路径。该方法的 `eligible` 是活动量门（100 日、30 个卖出实现订单、60 个持仓日），代码自己声明不构成晋级或统计证明；卖出订单也不等于 30 个完整退出批次。正式政策的 252 日、30 退出批次、风险和容量条件不能由它替代。

独立原价股份/税务/公司行动模块以及 `execution_coverage.py` 已提供工程构件；它们尚未成为这个研究评分入口的合格原价执行链。覆盖检查即使全断言满足也强制 `execution_valid=false`，不认证来源真实性。原价/股份、持仓跨日义务、公司行动及更正、当时状态/到达时间、费用与可成交容量仍需共同范围证据。刚补的 sh603786 上市日期说明只影响一个事后状态维度，不能减少 63 格、填七格缺价或放行其他门。

## 3. 共同内核存在与研究器实际可调用之间的差距

当前没有单独 `meta/kernel.py`；可信内核责任分散在 case/因子解释器、研究动作控制器、证据视图、Store、campaign 和 gateway。权限分工已有局部实现，不能把一个文件名或 CLI 参数当作 OS 隔离证明。

| 能力 | 已实现的实际入口 | 通用真实研究的缺口 |
|---|---|---|
| 因果因子、诊断、回测 | `factor_algebra` 有有限 AST 与非未来窗口；`ashare_research` 只解释 JSON 动作 | 任意策略代码开发/提交仍未开放；家族归属/任务逸出主要靠提示，没有强制机制验收 |
| 共同样本期限曲线 | `AShareCase.diagnose_horizons` 与 `horizon_diagnostics`；[CalibrationEvidenceView](../../../experiment_traces/meta_ashare_revision10/src/quanta_agents/meta/calibration_views.py:228) 可供两题查表 | **已确认接线不一致：** [development_packet:490](../../../experiment_traces/meta_ashare_revision10/src/quanta_agents/meta/ashare_case.py:490) 宣告此工具，但 [action_schema:21](../../../experiment_traces/meta_ashare_revision10/src/quanta_agents/meta/ashare_research.py:21) 只接受 diagnose/backtest/submit；[run_step:409](../../../experiment_traces/meta_ashare_revision10/src/quanta_agents/meta/ashare_research.py:409) 仍调用旧的单期限 diagnose，没有曲线路由 |
| 成本/实际库存段证据 | 成功开发回测可选保存执行诊断摘要与完整 artifact；独立两题可查询七种执行表 | 通用研究动作没有公共 `inspect_execution`/分页动作，保存 artifact 不等于研究器已能读取全部表。当前全表仍是调整单位口径，不等于真实股份 FIFO 税务批次 |
| 分组/对照 | 旧单期限 `diagnose` 有描述性分组与同日高低组差；新曲线按共同样本 | 没有匹配因果归因；旧分组不是共同多期限曲线，更不是策略反事实。不可循环旧诊断拼出假共同样本 |
| 子尝试与恢复 | 诊断视图分别报告 horizon computation 1、三个 h 子尝试、查询次数；campaign 固定付费意图和幂等 action | 这些完整计数尚未统一进入通用研究工具合同；函数一次调用不能掩盖内部三个 h 或以后搜索的候选 |

这一接线差距不阻断独立 `diagnostic_live` 的查询路径，它通过自己的 `inspect_execution` 协议访问已冻结的两题视图；它阻断“校准过的增强工具已经成为真实架构双方可调用共同环境”的说法。两题此次在提交合同处失败是另一条已确认边界，不能归因于通用期限动作缺失。

## 4. 普通输入与答案隔离

本轮读取的是已接受原型 `20260906T164840172917Z` 的两个 `public_packet/packet.json`，没有读取或生成新的隐藏答案。题号中性；ordinary_input 不给参考解、预期金额、获胜因子或主因标签。公开数据字典包含时点、单位、估值约定、库存计法与允许查的表；这些定义是可独立验算所必需的合同，不能为制造难题而故意省略。

同时，公开 quantity_dictionary 已列期限均值、库存段、成本与期末唯一股票等检查对象，且解释单表缺字段不等于跨表不可推导。因此这是**普通定义加技术说明的、有明确测量方向的诊断校准**，不能写成完全无提示的开放发现。它不直接泄露哪条因果解释成立，却缩小了检查空间；后续自然语言输入稳健性要另登记，不能与当前模式混合计分。题二还保留通用字典中的期限名词，但实际 table allowlist 没有 horizon 表；实际能力以 allowlist 为准，通用字典不是工具授权。

受控 prompt 只拼本题 packet、已验证的本题查询记录及 final schema；inputs 固定文件 allowlist，查询与证据绑定 run/arm/observation、packet、来源与公共页哈希；final 不返回评测答案。已有 canary、外来表/范围、packet 重哈希和 evidence 字节变动的拒绝证据支持这个接口路径。[接入审查](live_readiness_review_001.md:15) 仍明确不构成 OS 级隐藏文件隔离。未来两个隐藏正控需要更强的生成器/参考/评分权限隔离，不能继承“已暴露原型可用”就视为通过。

还需区分答案隔离和公开合同是否足够。当前 `submission_schema` 将 quantity_id 定义为普通字符串、unit 定义为统一枚举；`submit_diagnostic` 又按 claim_type 执行更严格的注册 quantity/单位/空值规则。主控的真实失败通报说明不能只凭 schema 已发给模型就判公共合同充分。v11 应明确呈现这些跨字段规则，同时保留“字段约束说明”和“诊断答案”区别。这个问题应先由当前负责人修复；既有完整付费调用不得删掉、改成未调用或借合同修复自动补跑。

真实五题的基线表达式及常量是事前公开起点，不是获胜答案；但当前 prepare 自动回测并给双方相同起点证据，所以它实现的是带起点/失败证据的研究模式，尚未实现完全移除初始 JSON 的自然语言开放模式。共同 packet 的 `research_prompt` 仍硬编码价格修复等方向；这不是已知答案泄露，却会给其他家族额外方向提示。`frozen_candidate` 只校验有限文本与哈希，没有语义检查其是否夹带旧股票/公式/人工技巧；提示中的禁止不能独立证明不存在答案输入。正式比较前应审阅并记录候选来源、普通输入模式、人工帮助与题目暴露。

旧公开 `benchmark.py` 的 manifest 自述代码和 seed 可重建 final，模型接口仅隐藏 final 行；其单资产 DSL、零无风险、可重建合成序列只支持 smoke。不读取它的未来行，也不能用它填充“两个隐藏正控”或混到真实成功分母。

## 5. P4 公平比较：已经有的约束和仍缺的门

| 比较维度 | 已实现/可复用 | 正式比较前仍需冻结或补齐 |
|---|---|---|
| 模型与 effort | runtime、gateway、campaign 固定 Astra/xhigh；保存请求和回执身份 | `model_verified` 与独立供应商请求认证分开；模型/网关漂移要另批，run ID 不是随机 seed；不能静默降级 |
| 输入、数据、工具 | case/data/source hash、共同起点、独立 arm 历史、受控工具和查询范围已有 | 正式候选必须在题目前冻结；同题基线轨迹生成候选只能算开发。两侧实际 schema/路由/分页/执行设置相同，不能只比较工具说明文本 |
| 基线强度 | 现 baseline 可自主选 diagnose/backtest；候选是另一份研究指导 | 强单 agent 与固定 harness 简单搜索基线还没有共同八题验收链；检索当前 meta 和 scripts 未发现注册的完整简单搜索比较入口。不能故意贫化基线或给候选更多选择权限 |
| 动作与资源 | 旧研究 6–18 次/架构；Q 固定六槽；新两题每题最多 17 次调用、16 query+1 final，题内 600k/全批 1.2m、80k 在途预留、300k 软提示 | 这些是不同合同。两题诊断不是 A/B 比较，也不是通用研究预算。P4 主轨道固定全栈资源规则、允许自主分配动作；窄消融才固定动作机会。实际更多回测可以是整体效果，不能直接归因为诊断改善 |
| 全成本与失败 | 已知 I/O 和子计数、未知预留、失败槽、唯一 attempt、暂停/恢复、不自动重付均有持久控制 | 元设计、摘要/专家/评审、工具扫描及工程模型成本要分列；补 CPU/内存/I/O/时间与实验工作量配额。名义阈值不是供应商账单绝对上限；其他服务/账户任务不在新 registry 覆盖内 |
| 最终选择与弃权 | `previously_evaluated` 要求本 arm 既有策略身份，可 no_supported_strategy；不自动取中间最优 | 默认 evaluate_on_submit 与固定先评估策略模式不同，且前者终轮强制 submit。比较需共同冻结选择/合法弃权规则；最后合法提交才是单位，中间最好值不替代它 |
| 配对与顺序 | batch 可冻单候选、独立每 arm、按题/重复交错顺序、共同数据门 | 尚无包含八类题的统一配对 manifest；未建立可据单次重复估计题目波动的证据。Q 与旧批次不能当单一改动的因果比较 |
| 评分与不确定性 | 两题 final 明确不进策略成功率；旧比较 promotion=false 并报告失败 | 八题须分报发现、误报、正确弃权、执行有效与真实收益；不合成可投机总分。8×2×1 的 16 次仅开发筛查，无赢家/稳定率结论；真实题、机制族、模型重复、共同市场日期不能相乘当独立 n |
| 暴露与真正留出 | 真实 case 共用 evaluation_family_id，换任务不清市场暴露；确认访问有 Store 记录，2024–2025 默认封存 | 当前本地账本不能认证全项目/预训练未见，也未形成完整跨服务/人类反馈账。2017–2021 开发、2022–2023 已暴露确认的定位不变；最终冻结前不得开封 |

正式协议中至少六个真实留出题、三个家族、各三次启动及 252 日等仍是待实现政策，不是已完成试验。统计方法、最低有价值改进、样本量和相关性处理须在新分数前冻结；只有两个真实开放槽的八题开发筛查不能替代正式批次。

## 6. 当前提交合同修复后的一项有界后续改进

**先完成 root 正在处理的 v11 提交合同修复；此后建议在再一个明确冻结的隔离改动中，只接通并验收通用研究器的共同样本期限动作。** 本报告不实施该项，不改变 v11 正在处理的范围。依据已经确认的“说明存在、动作不可达”反例，把 `diagnose_horizons` 作为版本化研究动作接到现有 `AShareCase.diagnose_horizons`，首轮固定 `[1,5,10]`，只回传有界摘要并保存原完整 artifact；不重写期限内核，不改变旧诊断算法、执行评分、预算或当前 v10。策略/表达式须沿用已有验证，三个 h 子尝试、失败与重复请求计数明确落账。旧回执继续按旧协议解释，不原地升级。

验收只复用已经公开的小路径 fixture 与 fake case/controller，不读市场、不创建新机制或付费：从真实 action schema 经 dispatcher 到内核及落账，再到下一轮公开历史，验证这一路径能完成；同题两侧用相同动作/数据/源码/资源合同。不要为测试获得答案而执行 fixture 之外的额外回测；被拒输入与失败仍占应有机会。此项通过后，才有资格说新增期限能力进入双方共同环境，再决定八题注册和后续配对预算。

明确证伪/停止条件：允许的期限动作仍无法到达内核；三个 h 只记为一个子尝试；摘要或完整 artifact 的身份不一致；重复/恢复重新产生实验机会；模型能越过开发边界、修改执行规则或看到未授权完整表；两侧实际可调用能力不同；旧冻结回执必须改写才能通过。任一出现就判接线未通过，保留失败并停止扩题或付费。通过仅证明接口与计数一致，不证明模型选择了更好实验、期限信号有效或 P4 架构获胜。

## 7. 两问与审阅身份

这一步做得怎么样：逐槽位拆开八题要求、核对五个真实注册和缺失 null 适配，确认两条 harness 的实际工具差异，区分公开定义/必要技术合同/参考答案。只读 AST 确认 TASKS=5、FEATURE_COLUMNS 无 null_score、通用 action_schema 无期限曲线；未把静态核验当新的模型/市场检验，未动当前 campaign。

下一步如何改进：先由 root 保留当前两题完整调用和失败记录，完成已确认的 v11 公共提交合同修复及独立验收，再按上节有界接线取得共同工具可达性的证据。不能为了按计划扩题而跳过这个更早的具体错误。没有新的隐藏题、正式收益或架构效果证据时，继续明确未达标。

以下 SHA256 固定本报告核验的关键文件字节；这里只建立审阅身份，不认证数据来源或供应商真实性。

| 文件 | SHA256 |
|---|---|
| docs/META_FRAMEWORK_IMPLEMENTATION_PROMPT.md | dd8d995495d9082d0f1f5302c78764030338848d1f2d678b0946feb16ca4ea03 |
| docs/META_ASHARE_BENCHMARK_CASES.md | 186b9cbce3ac11a9ededc81684a2b11e75de0b5b19dd93d99e35cbd1ef969ecb |
| docs/META_ASHARE_PROTOCOL_DRAFT.md | fdaffe6c87f605c9ed2ea24c7abc01ee0cf29e9d7572f8831d2e0cec822e645f |
| v10 meta/ashare_tasks.py | ef1b832fed1e87e05423f732fb8256b7d618a88fbb6462ab78b8f1500f723601 |
| v10 meta/ashare_case.py | faa987145ff14ad619cd1b8b7b495dbf2bae4d62ff51bd3b8c35b62507902244 |
| v10 meta/ashare_research.py | 60b5ccad8b6c0ea3a0518cdaa6ab60ca3952315605ecd23a8cd598158cde6320 |
| v10 meta/runtime.py | 767cedf3f6bd010c836b00d95b684ad29a1695a90b9cb333a1484bf3c328510e |
| v10 meta/benchmark.py | d05a1f838d45856a0af6bb1466897f57f1f9ff68203951ff1bfbfcab0c99f1d4 |
| v10 meta/diagnostic_calibration.py | df19745b5918b8efeaa41db472d11069247ceb315732fb590b91d4289ac18c3a |
| v10 meta/diagnostic_inputs.py | eb443cb96b3b879c38e108616aee88fbfb233370f51955f72f7dd0df648fa864 |
| v10 meta/calibration_views.py | 7ca70e8a3dce9278a316d2f096e0cb1400b32c6a3264ef29cb9d3c3659f0b147 |
| v10 meta/diagnostic_actions.py | 0c143a37a64e64d182b793b82b8e8304adc4a426d2544976fefe90c388785b74 |
| v10 meta/diagnostic_campaign.py | 9ce41fff24d71099edfaa8f037b67906bc56c63878b2d61cadb9bf4ee7e29dfb |
| v10 meta/codex_gateway.py | f4b7d865ce2de39765324cad1afcd2a2f42cd856514eac997c2845ebb9f20e99 |
| v10 meta/batch.py | 6eb33629b84913de12c6afcb39aa71887639884fa29301cc4c3f52549b4d4029 |
| v10 meta/execution_coverage.py | 4200473546f9d816426736a05f2a9ad22fb63a9865e336386aba1657854dae3f |

普通包身份：calibration_01 `58b06b2b84b858aa9255c97ecf185f02f6175f92f2b6bc1ca9d50c95cad83cf3`；calibration_02 `5c3731b1c8dab3e267a4626e8e69c83151056e00c7215d92cda700759691c977`。两者均为已暴露原型，不能以本次重新阅读形成新的独立样本。
