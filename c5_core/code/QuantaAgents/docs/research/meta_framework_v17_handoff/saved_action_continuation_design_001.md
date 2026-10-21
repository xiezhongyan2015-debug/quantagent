# 保存动作与原预算续研究：最小衔接设计 001

2026-09-07，Astra/xhigh 只读架构审查。**可行，建议独立 v17 continuation controller；不恢复旧 v15 loop，也不修改 v16 纠错器。** 当前父线程报告的起点为：原失败一次、纠错成功一次，纠错返回 `inspect_inputs(table=inputs, limit=50, cursor="", evidence_id="")`，工作台动作仍为零；已报告 input/output 合计 11,235，原未知预留 80,000。本文据该已报告状态设计，未重新打开实际工作台数据、执行动作、读取价格或发模型请求。新付费许可仍关闭。

## 同一逻辑预算域，新增治理层

在原 campaign 的同一个 SQLite 中增加有版本的 continuation plan/state、transports、action_intents 记录及审计事件。新目录只保存 v17 请求与控制器原件，不承载新额度、新四题或新 seed。原 v15 plan/run/call body、原 4 个 task identity、v16 correction 行与原件全部保持；continuation state 才记录新的当前任务、语义轮次、机会计数、暂停原因和任务终态。

新准入 overlay 必须绑定原 plan/rubric/budget/scope、原四题公开材料、原 rejected call 及 80,000 quarantine、已完成 correction 的 intent/receipt/response/usage/exit 文件和纠错许可消费记录、原工作台零动作 snapshot、v15 工作台及其完整依赖来源、v16 回执验证来源和 v17 新源/入口/公开预算说明。它明确接续的是同一次四题调查，不是新尝试或架构对照。修正传输 schema 与原精确返回 schema 分别记录，仍仅使用已冻结的 horizons 兼容修复，不改变研究语义。

所有付费准入共享原 registry 的唯一 `.dispatch.lock`。原 SQLite 的 `BEGIN IMMEDIATE` 中核对 continuation 控制状态、指定原 quarantine、全部原 calls + v16 correction + v17 transports、当前任务及 scope/source、原时钟、任务/阶段预算，再原子保存新 intent 和预留。不能只累计新表，不能把同一纠错同时计作两个新请求，也不能用第二个 registry 获得另一份配额。原 v15 仍 paused；旧 unknown 继续阻止旧入口，已消费的 v16 许可继续阻止再次纠错。

## 三种序号必须分开

| 记录 | 首题传输序号 | 语义轮次（从零） | 工作台槽（从一） |
|---|---:|---:|---:|
| v15 HTTP 400 原失败 | 1 | 0，未产出动作 | 无 |
| v16 已保存纠错回答 | 2 | 0，待应用 | 固定为 1 |
| 应用完成后的首个新模型请求 | 3 | 1 | 返回动作将对应 2 |

原失败没有 assistant answer，不能伪造消息或加入“研究结论历史”；它继续计传输和费用债务。纠错回答是真实保存的模型 response，只出现一次，并与随后实际返回的本题 public observation 组成第一个语义轮次。新模型请求每个语义轮只允许一次原子传输，失败不换槽重问。

起点合计 **2/17（首题）、2/68（阶段）**；首题最多再 15 次，其余三题各最多 17 次，合计最多再 66 次。16 query / 12 candidate / 1 final 是工具机会帽，不是保证可以全部使用。若本次 inputs 查询实际被工作台接纳，计 query=1、candidate=0、final=0；剩 query=15，但只有 15 个传输槽，若还需要一次 final，最多再用 14 次查询。不得补一轮让所有上限都用满。

名义暴露当前为 `11235 + 80000 = 91235`。在没有其他新已知费用的情况下，首题名义剩余额为 508,765、阶段为 2,308,765；下一模型请求另预留 80,000 后暴露为 171,235。input/output 计一次，cached/reasoning 子项不重复计；80,000 未知是保留额，不是实际账单上界。只允许已独立认证终态拒绝的**原指定那一笔**继续 quarantine；任何新的未知/在途未结、缺回执或不明工具结果都停止整个 continuation。

六小时仍从原 `created_epoch=1788728483.6826499` 计，下一付费准入截止为 **2026-09-07 03:01:23.682649 UTC**，暂停与工程修复不退还时间。单次 3,600 秒、1 MiB 完整上下文及原总额度不变。时钟耗尽后不得新发模型；是否仅应用已存动作是需要明确写入许可的本地管理范围，不能从过期的付费 loop 授权推导。

## 首次应用已存回答：明确的可恢复协议

建议接口分成 `register_continuation(...)`、`apply_saved_action(...)`、`run_remaining(..., gateway=...)` 和纯只读 `inspect_saved(...)`。注册不得隐式加载市场、应用动作或构造 Gateway；第一次 run 若拥有明确的新 scope，可先调用唯一 apply_saved_action，再在其完成后进入剩余原任务 loop。不能重新询问模型来获得相同 query。

1. 只读重核 correction 全部文件及 receipt：用独立保存回执验证函数核事件完成、真实退出、原 request identity、新 wire schema 与原精确 schema，确认费用已完整结算，并核原工作台 snapshot。不要调用 v16 controller 的 `_check/inspect_saved` 作为未来恢复总入口：它要求四个工作台仍为 ready/0，应用之后理应拒绝。
2. 原 campaign SQLite 先保存确定性的应用 intent：origin_kind=transport_correction、origin_id、response 原值及 hash、目标 task/scope、semantic_round=0、WB slot=1、pre-state、receipt/artifact pins、唯一 request_id。唯一键同时约束 origin 和 `(task, WB slot)`，不能在两个 intent 中应用相同回答。
3. 在唯一 controller 租约内调用**原 v15 Workbench** 的 `execute(1, saved_response)`，让它实际执行原跨字段、证据、机会和输入分页规则；不得把 v16 的 original_schema_valid 当工作台合法性证明。它先保留机会，再产生结果/失败。无 query_view 替换，不给它私有答案，不能复制新工作台重放成额外独立样本。
4. 原工作台已保存 terminal receipt/observation 后，continuation SQLite 再以 CAS 校验 intent/pre-state 与 slot/request/scope、完整返回结果 hash、实际 accounting flags/post-state，标应用完成并推进语义轮次和机会计数。只能从实际返回的 accounting 计 query/candidate/final，不能在两个账本各计一次或从模型自述推断。

这是两个数据库间的持久意图协议，**不是跨库原子事务**。SQLite WAL/ATTACH 不能被当成天然分布式提交。下一模型准入只在两边一致且无 pending 时开放。

| 崩溃/恢复位置 | 允许的后续动作 |
|---|---|
| continuation intent 未提交 | 无工作台动作；仍可在有效显式 scope 内建立同一唯一 intent。 |
| intent 已提交、工作台 slot 不存在 | 纯 saved-only 返回 `saved_action_not_started`；新的显式 apply 管理步骤才可首次执行该 slot，不能在 inspect 中偷执行。 |
| 工作台已 reserved/pending，缺完整结果 | 停止为 unknown；保留机会与子尝试，不能换 slot、重新计算或再问模型。 |
| 工作台结果已完整保存，continuation 未提交 | 只校验和交付保存结果，显式幂等本地对账可补应用记录；不再调用计算器。 |
| 两边均已完成 | 重复读取和重复同值管理对账不得多应用、多扣机会或生成新 request。 |

已知 query/候选失败保留消耗与证据，依原公开规则可进入下一语义轮；已知无效 final 保留失败并终结该题。未知结果立即暂停全阶段，其余题保留 not_started，不自动跳题/扩额。完整模型回答被工作台拒绝仍是已收费完成的模型调用，不能重标免费网关失败。

## 旧源码加载与无写读取

必须诚实使用原 v15 Workbench 源及其全部已冻结依赖；不能把 v17 版本的同名类拿去加载旧 scope。建议在一个显式隔离 Python package namespace 中加载原 v15 package，并给模块路径/源码 hash 做断言，防止 `sys.modules` 混入 v17 的 store/horizon/public 模块。无需复制 seed 或重建原题；这只是 controller/source 的明确接续。不能为解决导入冲突而修改旧源码或冻结 scope。

一个实际接口陷阱：原 [`Workbench.prompt():277`](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision15/src/quanta_agents/meta/casebank_workbench.py:277) 会调用 `recover_saved`；后者在 [:543](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision15/src/quanta_agents/meta/casebank_workbench.py:543) 调用 Store.update，即使终态幂等也可能更新时钟。**它们不是严格只读 GUI/inspect API。** v17 应从保存的 terminal observation、request、bundle/receipt/artifact pins 和工作台状态只读重建公开上下文；未保存 observation 时不得猜值。显式应用/对账入口可以使用原对象，纯查看入口必须证明零 SQL 写和零计算。

v16 correction 回放则绑定其原 permit、原工作台 pre-snapshot 和请求原件，而不要求当前工作台仍为零。原 snapshot 作为历史证据固定，不回填成新状态。独立 `verify_saved_completion` 与原精确 schema 可以在不改 v16 源的情况下验证这条历史回答；其 `_check` 后来因工作台推进拒绝，不应被描述成原纠错失效或触发重调。

## 完整上下文与 GUI

每个新请求包含本题原公开 packet/契约、所有真实已核验模型 response JSON、所有本题实际 public observations；第一项必须包含 correction 的原 response 和 slot1 返回值。只给模型已经实际返回的页面/摘要，不能把工具保存但未公开的全量结果额外喂入，也不能混入其他三题、私有机制或评审提示。已知失败 response/action 依原公开规则保留；HTTP 拒绝没有 response，不补造内容。

公开增加冻结的资源事实说明：本题已用/剩余传输、已用/剩余 query/candidate/final、共享名义 headroom、未知保留额和原下一准入截止。传输剩余不能继续写“17”，也不能暗示 16 次查询之后一定有 final 机会。说明只解释预算和来源，不包含选条件/答案提示。动态工作台 schema、wire schema、精确 schema、完整 prompt 和每轮预算 snapshot 都分别保存 hash；超限停机，不截断或摘要丢历史。

GUI 以原 root 为一个逻辑研究域：并列原 v15 paused、v16 纠错已停止/已消费、v17 continuation 当前治理状态；合计所有传输和费用，保留原 80,000 未知与原失败。任务状态来自 continuation 后态，原 run 中 ready/0 作为历史快照，不冒充当前计数。格式通过、工作台合法 final、答案正确性、策略成功分开显示。`running` 只是准入状态，worker 是否存活/独占另有本地观察；不自动启动 worker。

必须先有的独立反例：重复 apply 同保存回答；intent/WB 两库各崩溃边界；两进程竞争一个 slot/一次传输；纠错计费重复或漏记、原 quarantine 被释放、新 unknown 被豁免；首题第18次/阶段第69次/过原截止；原源混入新 namespace、当前 WB 推进后历史 correction 仍能只读回放；prompt 丢 correction 或混入他题/隐藏全量表；known failed query 与 invalid final 的原机会计数；`inspect` 的 SQL authorizer 拒绝所有写/零 Gateway/零 kernel；GUI 不因原 run 静态0覆盖 continuation，也不把无心跳的 running 说成存活。

两问自检：**是否需要重做四题或重问第一个动作？** 不需要；同一来源、同一 scope、同一已保存回答即可做唯一首次应用，后续只用剩余原额度。**本设计是否已批准续研究？** 没有；当前仅代码/契约只读审查，未创建 continuation、读取实际价格、应用工作台或派发。v17 具体 scope、源迁移、反例验收及唯一控制权尚须独立准入。研究仍限暴露合成开发，`costs_applied=false`、`execution_valid=false`，不计正式策略/P4/净优势正控成功。
