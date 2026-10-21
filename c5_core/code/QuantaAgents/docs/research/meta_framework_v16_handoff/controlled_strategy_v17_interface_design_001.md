# v17 受限策略程序到可信保存执行：只读接口设计 001

建议选择一个小而完整的切口：**有序命名因子表达式 + 直接目标权重表达式 → 冻结程序 artifact → 合成资料上的完整目标表 → SavedRawResearch → 带来源与失效记录的本侧记忆**。复用白名单解释器和原价股份内核，不接旧 Python agent 的同进程 exec。它允许研究者改因子计算、条件分支和组合结构，不是预置五字段权重优化器；它也不是任意 Python 开发或 OS sandbox。

这是 v17 文件划分与验收建议，**本轮没有建代码、测试或执行计划，没有运行合成或真实模拟，也没有模型/Gateway 调用**。只读 v16 继承源码、已有测试定义、前份差距报告和实施提示词 P2/P3/日线要求。没有读取真实 price CSV、当前四题回答或 evaluator 私有答案。原原价计划的 192 项 pending 状态，以及四题一次纠错成功后 paid closed / 0 WB action 的范围，以 root 当前事实为准，本设计不变更它们。

## 1. 可直接复用的边界

| 已有入口（当前 v16） | 本增量复用方式 | 必须保留的限制 |
|---|---|---|
| [factor_algebra.py:125](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision16/src/quanta_agents/meta/factor_algebra.py:125) `validate_expression`，`:130` `evaluate_expression` | 校验并解释每个声明表达式；调用者提供字段矩阵和 PIT rank universe | 4096 字符、256 AST 节点、深度 24、窗口不超过 120；无属性、索引、导入、任意函数、动态窗口、未来 shift。它不认证字段真实 available_at。 |
| [raw_saved_research.py:73](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision16/src/quanta_agents/raw_saved_research.py:73) `freeze_saved_research_plan` | 接收已冻结 targets 与可信控制器给出的合成 source artifacts / obligations | 最多 2 股、16 个 session，2017–2021 development；target 必须恰好下一 session、15:05 后形成、下一开盘前可用；最后 session 只能零目标。 |
| 同文件 `SavedRawResearch.create:257`、`execute_saved:316`、`reconcile_saved_only:348` | 独立子目录保存一次机械执行与原始 checkpoint；恢复只读保存证据 | 不自动重模拟；失败/中断保留前缀；原 6 类 obligation、完整本金、flags 和 engine_sources 不变。 |
| [raw_portfolio_backtest.py:241](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision16/src/quanta_agents/raw_portfolio_backtest.py:241) | 仅由 SavedRawResearch 调用 | 缺 target 的股票维持旧仓位，因此新适配器必须输出全股票目标表；不直接调用该较宽接口绕开 SavedRawResearch 的 exact-next-session 门。 |
| 原 Store / casebank 控制模式 | 复用 BEGIN IMMEDIATE、唯一 slot、意图先保存、完整本侧上下文、按 scope 分页的设计 | 不把新动作偷塞进旧四题合同；不打开原 casebank/诊断/原价数据库，不改本轮预算或付费准入。 |

本次读到的依赖 SHA256：factor_algebra `84783c6f907bcc9331602277605cdcd250d3a89680b51b9a2ecaf5bfd8021d30`；raw_saved_research `fbebcfa6a1e0bd288e4d736d9c903ccba8a1bcede401e178a11a6b34c70bc5cb`；raw_portfolio_backtest `cfdd9ae7da28014c0e3a2c4e89febce69ff5f73a803e5e047d763304e69cd23a`；raw_share_ledger `dfac220269f283c40b4956e57fe6d2a8ca8f132c62722a2e5db3ad7d63f3b387`；corporate_action_adapter `166e8d2a4fb94f1e90275fbcac689597f02562ebcfb37ce982528da78a3b7608`。新 controller 的 source manifest 应覆盖新两个模块、解释器和 raw 四文件，不能只 pin 外层文件。

## 2. 最小子语言与公开策略产物

公开版本建议 `factor_strategy_program_v1`。普通请求只含 JSON，不接受文件路径、Python code、callable、source hash、预算、初始资金或 execution_valid 开关。

```text
StrategyProgram = {
  version: "factor_strategy_program_v1",
  factors: [{name: identifier, expression: string}],  // 0..4，有序、唯一
  target_weight_expression: string,
  hypothesis: string,                               // 有界说明，不参与执行
  applicability: [string],
  invalidation_conditions: [string]
}
```

每个 expression 使用完整已有因子白名单，而不是从几个预制策略 ID 中选一个。命名因子只能引用公开字段和前面定义的因子；禁止自引用、后向引用、循环、重名、覆盖原字段或内置函数名。最多 5 个表达式，每个继承已有资源上限，整个请求另公开 UTF-8 总字节上限。所有字面阈值、窗口及组合关系进入程序源文本和 AST 摘要；工具没有优化器、随机参数、网格搜索或自动纠错分支。

公开语义：`target_weight_expression` 返回每个信号日、每只股票的**目标本金比例**。这使研究者能够写条件现金门、非线性因子和不同组合关系；本版不提供持仓状态读写或自定义成交回调。算法的直接产物是权重，不是净收益。普通说明必须完整公布：非有限值/缺失 → 该股票零目标并登记原因；任何有限负权重、单股大于 1、或同日合计大于 1 → 整次候选失败，不能自动 clip、归一化或缩小本金。解释器为 float；目标序列化使用固定 `Decimal(str(value))`，超额哪怕来自浮点边界也明确拒绝；首版不引入隐式容差。模型可在表达式中显式使用已公开的 `clip`。

完整 artifact 分别保存原始请求字节 hash、规范 spec hash、语言版本、解释器/适配器 source manifest、AST/依赖摘要、binding 和冻结政策。程序字段是研究者声明；源码、数据和范围身份由控制器补入，不能让模型伪造已验证来源。`hypothesis/applicability/invalidation_conditions` 是带作者来源的待检验陈述，不因存入 artifact 就成为事实。

## 3. 数据时点和执行合同

可信 preparer 冻结一个**另外命名的新合成 scope**。只用最多 2 股 × 16 session，普通 fixture 公开明确是 exposed engineering；符合 raw 符号语法的代码只是合成技术 ID，不读取其真实市场历史。原 192 pending 计划不复用、不覆盖，也不将未知 obligation 变成 fixture_complete。

决策输入建议为普通 JSON：`calendar / codes / fields / field_rows / eligibility_rows`。每个字段记录包含 `session / symbol / field / value / effective_at / available_at / source_evidence_id`，字段合同声明单位、含义和是否允许模型使用。每个 eligible 记录也带有效及可用时间。代码只接受控制器冻结的字段白名单，不让研究者输入字段路径；开盘成交资料通过另一受信 source handle 留给原价内核。

首版采用容易验算的保守时点规则：

1. 每个 signal session 的决策截止固定为当地 `15:10:00+08:00`，不能由策略修改；本行字段须 effective_at 不晚于截止且 available_at 不晚于截止。缺值、无时区或身份不一致不填补；缺元数据是输入失败，已知晚到值在本行不可用。
2. 晚于本行截止的历史记录永久保持该行未知，**本版不做迟到历史值回填或修订版 as-of 数据查询**。这是公开子语言输入限制，不是声称这些信息在所有未来时刻也不可能使用。后续支持真实多版本来源需要另冻结数据合同。
3. 同一完整 session 轴上计算 lag/rolling，不删除停牌或缺值行。每一个嵌套 cs_rank 使用该历史行当时可见的资格，不用期末成分反推。控制器不能把“字段存在”自动解释成可交易资格。
4. 只在下一 session 开盘执行；输出每一股票、每个 trade session 的显式目标，零目标也保存。遗漏股票不能导致旧仓意外保留。首日保留初始全现金；最后 session 的目标由公开固定结束政策设为全零，同时保留原表达式建议值和覆盖原因。清算若遇停牌/T+1/缺价仍按实际拒单与剩余持仓报告，不能假定已变现金。
5. 每日记录 `decision_cutoff / accepted_input_hash / masked_reason_counts / factor_values / weight_raw / weight_effective / target_available_at / signal_date / trade_date`。完整目标及其 hash 保存后，才冻结 raw 子计划并允许其读取成交 outcome。保存顺序与 hash 证明的是控制器内顺序，不是外部真实发布时间认证。
6. `freeze_saved_research_plan` 继续接 `source_artifacts`、完整 `codes × calendar × 6` obligations 和原政策；raw execution 仍处理整手、T+1、费用、容量、涨跌停/停牌、公司行动、缺持仓估值与现金。拒单预算不挪给别股，不删亏损，不让策略重定义这些规则。

## 4. 具体 API 与持久边界

以下是建议新增 API，不是现有已实现接口。

```python
# factor_strategy_program.py — 内部纯函数，不作为免费模型校验工具
validate_program(spec, *, public_field_contract) -> validated_program
build_targets(validated_program, *, decision_fixture, frozen_policy) -> target_artifact

# strategy_development.py — 唯一公开/持久控制器
StrategyDevelopmentWorkbench.create(root, *, frozen_plan,
    public_decision_fixture, raw_source_bindings, controller_cursor_key)
StrategyDevelopmentWorkbench.load(root, *, expected_plan_sha256,
    controller_cursor_key)
workbench.public_contract() -> dict
workbench.execute(slot_id, request, *, expected_state_sha256) -> bounded_result
workbench.recover_saved(slot_id, *, expected_request_sha256) -> bounded_result
workbench.query({"observation_id", "table", "cursor", "limit"}) -> evidence_page
workbench.export_for_audit() -> saved_artifacts_and_counters
```

`create/load` 仅由可信 harness 调用，路径/源绑定/预算不来自模型。新 registry 与新 ledger 由 root 明确分配；没有旧 ledger 的可替换路径选项。公开 request 的 action 首版只有 `develop_strategy`、`record_memory`、`invalidate_memory`，其余证据与记忆通过同一受控 query 读取。所有字段和 one-hot 规则事先公开；不能再靠隐藏提交规则收费后才拒绝。

`develop_strategy` 的步骤固定：

1. 在唯一调度 lease 与同一 BEGIN IMMEDIATE 中检查 binding、scope、当前 slot、状态和冻结研究机会额度，登记原 request hash / bytes 与三个子阶段：`compile`、`target_generation`、`raw_mechanical_execution`。**先登记，再解析程序与读取数值。** 非法或超长但可识别的有限 JSON 采用有界拒绝 envelope 并消耗对应机会；无无限大正文入下一轮上下文。
2. compile 只读程序与公开字段描述。合法产物在计算前保存并 fsync；语法/资源拒绝保留错误和未运行阶段，不自动修一次再试。
3. target_generation 读取被 pin 的新合成决策输入、生成完整 artifact、保存摘要和 hash。完整 artifact 包括每个命名因子、每个缺值/时点屏蔽原因、全日全股票分母和参数。目标表完成前不能读 raw execution source。
4. 将 `strategy_hash=program_artifact_hash`、`data_hash=冻结决策与raw来源承诺hash`、`source_hash=本增量与raw依赖manifest hash` 映射到现有 raw identity；保留 run_id / architecture / observation_id，额外 linkage 放外层 artifact，不修改 raw 六字段合同。调用 `freeze_saved_research_plan`，保存其完整子计划及 hash，再对该候选独有的新子目录调用 `SavedRawResearch.create/execute_saved` 一次。
5. 保存父子 plan / receipt / evidence ID 链，并生成本侧有界记忆条目。返回三条分别命名的状态：`program_status`、`mechanical_status`、`execution_valid=false`；成功执行机械核验不能把开发可运行提升成真实执行有效。

一次普通请求只有一个 program，没有内置参数扫描。计数明确分开 `candidates_requested`、`internal_variants_requested`（首版为 1）、三个子阶段的 requested / started / completed / failed / reused / not_run，以及每阶段真实 expression evaluations、session×symbol cells、raw run count。表达式内部循环/函数求值是资源计数，不包装成独立研究样本；三个子阶段也不是三个独立研究样本。若以后增加变体扫描，每个变体必须预先枚举、单独扣内部候选与子尝试，不能继续沿用当前单变体计数。

同 slot 的重复调用只核对原 request hash / bytes 并返回 saved 结果，不新增计数；同一有限请求改一字则拒绝。新 slot 重复同 spec 仍消耗候选、原轮工具机会和三个 requested 子阶段，绑定已有 complete/known-failed 结果为 reused，actual compute=0。已有同身份未结清的工作不允许借新 slot 重算。任何内部失败不退还机会。

恢复只允许校验已保存完整 artifact、父子 hash 和 `SavedRawResearch.reconcile_saved_only`。意图后无结果、只创建了 raw 子目录尚未执行、缺文件、父子状态不明，均返回 interrupted/unknown 并停止；恢复不能调用 `build_targets`、create、execute_saved 或模拟器。已完整保存但父 receipt 未落时，可用同一幂等 evidence ID 对账已存结果；不能重生成结果去“证明相同”。此保守边界可能损失一次尚未计算的机会，应保留为工程失败。

预算数值不在本设计新增：候选、query、内部计算、持久字节和阶段时间的**完整上限须由 root 与预算连续研究合同共同显式冻结**；不能把旧四题的 16/12/1 或 17 call 当作自动授予新协议的额度。未来若接真实模型，Gateway 的 Astra/xhigh / request intent / 原子 token reserve / unknown stop 仍由另外的受信研究 harness 负责，新工具内部不构造 Gateway，也不改变本侧完整上下文策略。

## 5. 记忆与公开证据

初版不实现语义搜索或自动摘要模型。每个工程结果自动追加一条记忆，研究者可显式补充解释，但原证据和原记忆不覆盖。

```text
MemoryEntry = {
  memory_id, scope_hash, run_id, arm, task_run_id, program_artifact_hash,
  source_evidence_ids, applicable_conditions, claim_text,
  counterevidence_ids, invalidation_conditions,
  status: active | invalidated | scope_expired | evidence_unverifiable,
  supersedes, created_from_slot
}
InvalidationRecord = {
  memory_id, reason_code, source_evidence_ids, explanation, created_from_slot
}
```

适用条件分两类保存：控制器可检查的身份/字段/时点合同，以及作者提出的待检验条件。身份漂移、artifact 缺失可使条目不可复用；收益无显著差异不能自动标成机制已被推翻。机械失败说明本实现或输入在该条件下不能执行，不等于因子没有预测能力。人工/模型宣称失效须指向本侧已有证据，记录作者声明，不改内核评分。

`query` 的 table 建议只含 `programs / decisions / targets / execution_events / failures / memories / invalidations`，limit≤50，游标 HMAC 绑定 run / arm / task / scope / observation / table / offset。每页绑定完整 artifact hash、page hash、全部分母和有界 limitations；私钥、source 文件路径、别侧记忆与 evaluator private 不进返回。`record_memory` / `invalidate_memory` 是公开有成本动作，用冻结 query/行动额度登记；自动生成结果事实条目是已登记 develop 请求的子产物，不伪装新增模型推理。GUI 全面浏览和检索性能不是首版交付门。

## 6. 文件 owner 建议与停止点

建议一个实施 owner 仅新增 v17 `src/quanta_agents/meta/factor_strategy_program.py`、`src/quanta_agents/meta/strategy_development.py`，以及 `tests/test_meta_factor_strategy_program_v17.py`、`tests/test_meta_strategy_development_v17.py`。第一个模块负责子语言/时点/目标表，第二个负责身份、持久阶段、机会计数、raw 适配与小型记忆表。独立 reviewer 只新增专属反例文件；root 维护冻结来源、外层预算准入及联合验收。必要的单个 fixture demo 脚本由同 owner 在 root 划定后新增；只能生成/执行新暴露合成资料，不运行原 192 pending 计划。

**预计无需修改 factor_algebra、SavedRawResearch、raw portfolio/ledger、Gateway/campaign/live 或旧 Workbench。** 若真正实施发现这些共享接口必须修改，先报具体缺口，由 root 分配文件并另验边界，不能在适配器中复制一套略宽松的 kernel。开始实施前先冻结本子语言、公开失败计费和资源预算；本设计完成即停止，不创建 v17 目录或试运行。

## 7. 十组有意义的合成反例

以下是拟验收要求，本轮没有执行。独立预期应来自小数组/Decimal/显式日历手算，不能调用被测编译器或用其输出造 expected；公开输入、预期和生成逻辑分目录，明确 exposed 工程，不称隐藏题或同预算参考发现。

1. **不是模板参数选择。** 两份普通程序改变命名因子依赖和 where/rolling 结构，在同一 2 股小序列上产生手算可区分的权重与现金；记录真实源文本/AST 差异，无优化器或隐藏候选。
2. **解释器与资源边界。** import/属性/subscript、负 lag、动态窗口、前向引用、函数遮蔽、循环与节点超界均在候选登记后失败；用 traps 证明未到任何同进程 exec/eval、源文件读或 raw 执行。超长有限 JSON 也消费机会且只保存有界错误证据。
3. **available_at 反例。** 同一有效日值分别在 cutoff 前/后发布；后者本行不可用，不能靠 lag/嵌套 rank 读回。扰动未来值/未来资格不改变任何已保存过去权重；无时区时间拒绝，等于 cutoff 的边界有明确一致规则。
4. **完整日期轴与缺值。** 缺一个值不删除 session，不压缩 rolling 窗口；重复坐标、错轴或缺 availability 元数据拒绝。非有限权重产生明确零目标与原因，保留全部 2×日期分母，不丢持仓股票。
5. **权重与全现金。** 负权重、合计 1.0001、NaN、全不合格分别验证已公开路径；超额不能归一化，某股失败不能将预算重新分给另一股，初始现金及未投资现金留在净值分母。
6. **next-open 与终点。** 首日信号只能下一 session 开盘，不能同日交易；缺交易 session、强行末日新开仓请求不能绕开固定清算合同。信号期末的 raw 建议与强制零目标并存；未卖出的真实持仓不能伪报成现金。
7. **成交约束归原内核。** 最小路径验证费用/整手/容量、涨跌停拒单、T+1 不可卖及缺持仓收盘估值。一个未知公司行动 obligation 在已有持仓时停止，保存损失和不完整净值；不换成 fixture_complete，不改原 192 项。
8. **机会原子性。** 两连接抢最后候选 slot，最多一个准入；已知失败、新 slot 重复成功和重复失败都收候选及 requested 子阶段，复用 actual compute=0；同 slot 改正文拒绝，空 pin/换 scope 不能获得免费重试。
9. **崩溃与 saved-only。** 在 compile 意图、targets 写后、raw 子计划创建后、raw event_intent 后和最终父 receipt 前分别中断；恢复将所有 compiler/build/execute_saved/simulator 设为 traps。完整已存结果可对账，缺失/未开始阶段停止；新 slot 不能绕开同身份 unknown。
10. **来源与失效记忆。** 一字 source/spec/fixture hash 漂移不能复用，另一 arm 的 evidence/cursor/memory 不可访问；原条目与失效条目追加并能按本 scope 查出，不能篡改旧理由。机械失败不能自动生成“统计无效”结论，所有完成/失败路径 execution_valid 与 formal_target_success 均为 false。

可复用但本轮未重跑的测试位置：`tests/test_meta_factor_algebra.py` 的未来扰动/缺值/轴/资源组；`tests/test_raw_saved_research_v15.py` 的 exact-next-session、未知义务、持久中断、saved-only、文件边界组；`tests/test_raw_portfolio_backtest.py` 的完整本金、当日未来字段不影响开盘成交、容量不重分配和持仓估值组；`tests/test_raw_share_ledger.py` 的 T+1、费用、原子拒绝、迟到证据、journal replay 与不可提升标志组。后续按实际改动复用冻结凭证，新增接线反例，不机械重跑整个历史套件。

## 8. 两问与未解决范围

**这一步能否让普通策略输入实际走到可信执行？** 在新暴露合成资料与上述受限语言中可以形成可实现接口，开发者需要用完整 artifact、原价子计划和保存事件证明它走通；当前仍是设计。它不等于真实策略有效，不计正式成功率或收益改进，也不对当前四题补动作。

**什么会推翻“这是最小且可信的接线”？** 任何自由 Python 执行、隐藏参数搜索、晚到/未来信息进入过去决策、执行义务被策略覆盖、未登记重算/免费失败、丢失现金/拒单/持仓，或缺产物时靠重跑恢复，都足以阻断。若现有 kernel 必须扩接口或多版本数据需求无法用本版保守规则表达，应缩小交付并回报具体缺口，不修改成功口径。

本版不解决自由代码安全沙箱、持仓状态型策略/事件回调、复杂订单、多资产/做空/杠杆、真实多版本 PIT 来源认证、完整公司行动覆盖、原 192 pending、真实市场或留出发现、阶段连续付费控制、全量 GUI、向量记忆与摘要模型。它也不声称固定 2×16 合成题能验证策略泛化。实施和评审仍按 root 统一 Astra/xhigh；本子任务新增项目 Gateway 调用为 0，工程设计模型用量非零另列。
