# 首次 schema 拒绝后的费用与单次纠错设计 001

2026-09-07，Astra/xhigh 独立只读工程审查。**有条件支持设计一次显式纠错；当前仍不准派发。** 请求终态已拒绝与费用是否已结算是两件事。可以对有充分终态证据的单个拒绝保留未知费用债务，同时准入一笔有独立预留的新请求；这不是把 HTTP 400 当零账单，也不是通用“忽略 unknown 后 resume”。原规则保持关闭，checkpoint 018 的 paid=false 没有被本报告解除。

## 当前证据与原实现

原 call 为 `reference_v15_original4_001/reference_v15_original4_001_case_01_reference_round_0-1`，intent_id 为 `9247fc45e5dd4fd398e23d80c1007d4f`。保存事件确含 HTTP 400 / invalid_request_error / invalid_json_schema / text.format.schema，随后 turn.failed；本地子进程保存 exit=1。没有 agent_message、turn.completed、最终 usage 或工作台动作。SQLite 中原 call.status=failed，四个 usage 字段均为 null，stage=paused、pause_requested=true，4 题仍在，case_01 的 semantic round=0，另三题未开始。

这证明本次本地请求链的明确终态拒绝；未捕获完整 HTTP wire body，也没有额外供应商 request id、零账单回执或实际模型身份认证。不得把 thread id / invocation id 冒充供应商计费确认。根代理此前保存的进程退出观察支持没有在途子进程，但真正派发前仍需重新验证控制权，不能把旧观察当当前存活探测。

- 原事件 SHA：`ee3b52d18bbe938741fe687925986cc7d2462859d0c8fff2658421dfda5d49a8`；process_exit SHA：`a3078edb3cf6d24f2ca8ce07c9313a946e153476c0f39aa1ae398641173cb854`。
- [failure_audit_001/receipt.json](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_casebank_references/campaigns/reference_v15_original4_001/failure_audit_001/receipt.json) SHA：`15bc2fccf3c7344374ce01b5bbd46a19d93441ef08cd983f2b3e3b7968015307`。
- [checkpoint 018](../meta_framework_v8_handoff/checkpoint_20260907_018.json) SHA：`ecb215c0a778195f6839a1015de34cd1b0eee033141ace74bbe495e6e34d3af3`。

[`store.py:28`](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision15/src/quanta_agents/meta/store.py:28) 对未 completed 或费用不全的请求保留完整预留；[:144](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision15/src/quanta_agents/meta/store.py:144) 在同一 BEGIN IMMEDIATE 事务中核预算、唯一槽与写入意图。现 [`casebank_campaign.py:224`](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision15/src/quanta_agents/meta/casebank_campaign.py:224)、[:319](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision15/src/quanta_agents/meta/casebank_campaign.py:319) 明确禁止未知调用后的 reserve/resume；[`casebank_live.py:71`](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision15/src/quanta_agents/meta/casebank_live.py:71) 又要求连续每轮都已完成并应用。这些旧门不能由改变 status、step、attempt 或另建 registry 绕过。

## 最小实现：同预算域的一次性纠错 controller

建议 v16 新增独立 one-shot controller，不接原完整 execute_task 循环，不修改原计划、原 call body、schema、提示词或原工作台。在**原 campaign 的同一 SQLite 预算数据库**增加命名清楚的 correction/permit 表及只追加审计事件，保存这一次纠错。它是原逻辑 campaign 的扩展账，不是新 campaign/独立额度。单独目录只放新请求原件，不成为新的预算或身份根。

同库是最小可验证的事务边界。另一个 SQLite 文件即使在同一 registry，也不能凭“两个文件都写了”保证原子预算核算；尤其不能假设 WAL 下跨库提交天然原子。若必须独立数据库，应先设计唯一预算协调者和崩溃恢复协议，超出本轮最小方案。

原 registry 的 `.dispatch.lock` 仍是唯一模型派发租约。原 controller 保持 paused，旧未知费用门继续阻止旧入口；新 controller 只能使用新冻结许可指定的原 run/call/task。新的同库聚合视图必须包含原 calls 和 correction requests，不能只看新表。原 active owner 不变，不能为新源版本制造第二个 active campaign。

| 项目 | 纠错前 | 新请求预留提交后 |
|---|---:|---:|
| 原调用次数，计入同一任务/阶段 | 1 | 1，永久保留 |
| 新纠错传输次数 | 0 | 1 |
| 合计任务/阶段传输次数 | 1/17、1/68 | 2/17、2/68 |
| 原未知费用预留 | 80,000 | 80,000，不释放 |
| 新请求预留 | 0 | 80,000 |
| 当前已报告费用 | 无报告 | 加所有确实已报告的 input/output |
| 当前名义暴露 | 80,000 | 至少 160,000 |
| semantic round / 已用 query、candidate、final | 0 / 0、0、0 | 仍为 0 / 0、0、0，直到另行实际应用动作 |

定义 `nominal_exposure = 原调用已知I/O + 原80,000保留预留 + 新请求已知I/O + 新请求尚未结算的80,000预留 + 同域其他调用暴露`。cached input 是 input 的子项，reasoning output 是 output 的子项，不重复加。新请求有完整已核验完成和 usage 后只能结算**新**预留；原 80,000 继续显示为 `billing_unknown / quarantined_rejection_debit`。本修订不包含任何释放原预留或设定实际费用为零的命令。后续可信计费证据也必须另追加、独立对账，不能覆盖原未知历史。

任务 600,000 / 阶段 2,400,000、17 / 68 次均不变，软警告和完整上下文上限不变。原 created_epoch=`1788728483.6826499`，六小时截止为 **2026-09-07 03:01:23.682649 UTC**；修复、等待和暂停耗时均不退还。只在原截止前准入，单次 timeout 仍为 3,600 秒；它不是精确供应商账单上界，也不是六小时整点必杀在途进程。

必须在新许可中明确承认：这是原 attempt=1 / no-retry 政策的**一次人工冻结协议修订**，不是宣称从未重试。原 call 保持 attempt=1；纠错另有唯一 correction_id、`corrects_call_id`、`transport_ordinal=2`、`semantic_round_index=0`。新 step 的 attempt=1 仅表示新传输槽自身没有重发，不能遮蔽它是第二次传输。case_01 最多还剩 15 个额外传输槽，不能另补 17 个；因此可能无法同时完成原最大 16 query 加 final，必须保留因额度不足未完成的结果。四题描述性分母、原失败和另三题 not_started 均不重置。

## 许可必须绑定的证据与停止条件

新 overlay/许可应冻结原 plan content hash `3cda519a83180b6b5b4fe96c447a406efee82b5df73b31cc450fd10555437173`、原全部请求原件 hash、原 failure/checkpoint、原工作台状态与公开包、rubric/budget/scope、v15 原依赖及 v16 新 controller/wire schema 的源码 hash。原 wire schema 与新 wire schema 分列；公开语义合同不放宽，不排序、填补或改写模型返回值。传输修复尽量放独立 adapter，保留原语义工作台；如果需要改工作台源身份，应另冻结明确迁移证明，不能拿新源冒充旧 scope。原 prompt 文件 hash 与意图逻辑文本 hash 是不同口径，分别核对，不混为一个值。

仅在以下条件全部满足时，未来的新许可才可以成立：

1. 重新核对原件哈希、本地 invocation/request/intent 绑定，明确的终态 400 schema 错误和退出；没有任何可用回答、usage 完成、工具动作、未结 action 或不明重连/后续请求。只缺计费结算。对 timeout、失联、503、部分回答、语义拒绝、schema之外错误、隐藏重试，均不能套此例外。
2. 原四题状态、原预算、原时间原点、唯一 registry owner 与原预留不变。只允许这一条原 call 成为 quarantined rejection；任何第二个未知请求或额外 pending 都阻止新准入。
3. 新生成 schema 有离线结构风险检查、原严格语义合同的反例测试和具体修复 diff。离线通过不称供应商已接受；原错误没有字段路径，因此数组 enum 仍只是优先修复假设，不能事后写成唯一确定原因。
4. 独立准入明确许可最多一次新传输，许可有唯一 id、到期时间、原 scope/hash、预期纠错请求 hash；公开输入不添加私有答案/别题/评审提示，工具仍关闭。根代理确认旧进程不在途、当前唯一控制权。checkpoint 018 之后须有新的明确准入，旧准入不能重用。

并发与崩溃边界：持唯一 dispatch lease 后，在同一 BEGIN IMMEDIATE 中重新检查所有 pins、原暂停状态、全部原/新请求、唯一纠错资格、时间、总预算和任务预算；原子插入许可消费记录、不可变纠错意图和新 80,000 预留，设唯一键 `(base_run_id, corrects_call_id)`，拒绝第二个纠错。提交后才允许准备文件和 mark_dispatch_started，再核暂停/许可/时间/源身份后启动进程。两个实例最多一个预留；reserve 已提交就消费该唯一机会，即使尚未发出便崩溃也保留，不自动补发。若派发后未知，保留两笔 reserve 并停止，不能再豁免第二个未知。

预算锁不应跨网络持有，派发 lease 则覆盖该单次过程。暂停是独立强制门，不能在 reserve 和实际派发之间被忽略；`dispatch_started` 持久化后进程启动失败/状态不明也不自动重发。文件持久化失败、来源漂移、额度不足、六小时已过、同许可重入或已知 usage 增长导致预算超出，均保存原因并停，不扩额或回退到旧入口。

## 返回与恢复

**成功、HTTP 拒绝、超时或任何异常都停止这个 one-shot controller。** 成功只保存完整原事件、真实退出、原 answer JSON、已核验请求/wire/语义身份和费用回执；不会自动进入第二次模型调用，也不会隐式 `Workbench.execute`。一个合法 query/final 只是待应用的模型动作，不能为了显示“完成”而补造工具结果或改变机会计数。

单独 `inspect_saved_correction` / `reconcile_saved_only` 只读取保存原件和账务，复核后交付原回答；无 Gateway、无查询器、无工作台新动作、无暗中补算。完整费用结算若需落库应是明确的幂等本地对账事务，而非再请求供应商；缺任何必需原件则返回未知。后续是否首次应用该动作、继续原任务或处理其余三题，须另行冻结管理准入，不能从本次“最多一传输”的许可自动推出。

最小定向测试应覆盖：双实例同许可仅一笔预留；原 unknown=80k 永不消失；成功结算新费用后原债务仍在；原/新已知子项不重复计费；case/stage 预算临界值与原六小时截止；任何非指定 400 / 部分答案 / 第二 unknown / 源漂移拒绝；reserve 前后和 dispatch 前后崩溃不重发；请求 ordinal 与 semantic round 分离；saved-only 不触发 Gateway 或 Workbench.execute；成功和失败均只停、不进入完整循环。无需大迁移或重复无关套件。

两问自检：**是否把未知账单消失了，或替原失败增加免费机会？** 没有：原 80,000 和一次失败传输完整计入同预算域，新请求另预留并消耗第二次传输；原研究机会及四题分母保持。**目前是否已经具备付费继续许可？** 没有：这是条件化设计，不改任何原账本或 v15/v16 源码；实现、反例验收、具体许可和根代理独占控制验证仍是下一门。若无法保证同域原子性、明确终态或完整来源绑定，合理方案仍是保持停机，而不是换 registry 重开。
