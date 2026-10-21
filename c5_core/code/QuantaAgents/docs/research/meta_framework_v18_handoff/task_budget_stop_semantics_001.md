# 首题预算停止与余题推进：独立控制审计 001

2026-09-07 05:04 UTC，继承 Astra/xhigh。结论：**此次全阶段暂停符合原冻结自动停止规则；首题已无下一次传输额度，但整阶段未耗尽。可另冻一次保存证据管理步骤，将首题保留为 `budget_stopped` 后推进原 case_02..04。现有 CLI 的 `resume` 本身不会完成这一管理动作。** 本审计未读候选或回答内容，未运行控制器、Store、Gateway 或工作台方法，未改源码、原库或许可。

## 已保存事实

| 项目 | 本次独立读取 |
|---|---|
| 全域传输 | 15：原失败 1、纠错完成 1、v18 新完成 13；完整调用合计 14 |
| 费用与预留 | 已知 input+output 491,954；原 HTTP400 的 billing-unknown 预留 80,000；exposure 571,954。没有新 unknown |
| 首题 | 14 个 applied 动作；工作台冻结槽 1..17 内 14 completed、14 queries、12 candidates、0 final；治理 task 仍 `ready`，stage 和 phase 已 `paused` |
| 余题 | 三个工作台均 0 动作、0 query/candidate/final；治理均 `not_started` |
| 停止事件 | continuation event 197，`2026-09-07T04:51:00.567546+00:00`，`admission stopped: original nominal exposure cap exhausted` |
| 当前身份 | v17c1 state SHA `551ee2ab4442003db718fe6966499d17d34b9a2a3708bc8fcc456d7df45f032a`；v18s1 scope `8d60a4fdef3d94c4f952edb721f9d620c62a17f51c760e2b67a4e9ed9beef66b`；admission `e57a5ed310ea7d8abd3fa43177fe0e07e1797d7665f00387c52cb8b5f230a163`；41 次治理审计 |

首题剩余名义空间仅 **28,046**，固定下一次预留必须是 80,000；`571,954 + 80,000 = 651,954 > 600,000`。因此剩余 2 个传输槽、2 个 query 槽及尚未使用的 final 槽不保证可执行，不能降低 reserve 或补一次廉价 final。全域尚余 1,828,046；余三题各 600,000 合计 1,800,000，在全域内有名义空间，仍逐调用核实际用量、预留与时间。余全域传输槽为 53，而余三题自己的上限合计 51；首题未用的 2 槽不能转让。

进程快照 `2026-09-07T05:02:14.4350855Z`：启动记录 PID 59904 已不存在，其子进程及匹配本 campaign/v18s1 的 Python/Codex/Node worker 均未发现。原启动记录仅记 `completion_verified=false`，没有独立退出码回执；故可报告 worker 已不在运行，**不据此猜 exit 0**。对原 registry `.dispatch.lock` 做一次不加锁、不开写权限的首字节读取成功；采样时未见 Windows 排他字节锁冲突，不把锁文件仍存在当活跃锁，也不把本次观察当未来派发许可。实际管理/派发仍须原排他 lease。

## 为什么没有自动进入下一题

原 `reference_scope_001.json:22` 明确“任何准入失败先停止全阶段”，并保留了“已知单题预算耗尽可由另行冻结的保存证据步骤管理”这一出口；原量表 `reference_evaluation_prereg_001.md:66,72` 同样说明预留算法、全阶段暂停、不自动跳题。300,000 soft 线只是告警，并未保证替研究者留出 final。

源码与规则一致：v15 `casebank_live.py:158` 将 reserve 异常转为 stage pause；v18 `casebank_continuation.py:744–757` 同时核原 stage/task cap，并用同一错误文本报告任一 exposure 门失败；`:790–804` 捕获准入错误后暂停并返回。此次算术可明确归因首题 cap，不是 2.4m 全域 cap。`:724–735` 只有工作台实际终态才自动推进 `current_task`。`budget_stopped` 虽在 `TERMINAL` 集合中，v17/v18 没有对应的公开管理入口。直接 `resume` 只把 stage 重新设 ready，仍会对首题再次触发同一 cap，不会形成新调用，也不会推进余题。

原 v15 量表的“无未结算”条件不能字面套用于当前仍保留的 80,000。v16 纠错与当前 v18 政策已经只为**该精确旧 HTTP400**建立了保留债务的例外；`supplementary_research_policy_001.json:29` 仍要求任何新 unknown 停止。因此管理准入应明确“除原已绑定隔离 call 外，全部新调用完整结算、无 pending 动作”，而不是宣称全域没有 unknown，或为第二个 unknown 开例外。

## 最小可行管理接口

可以新建独立、事前冻结的外部本地管理器，例如 `finalize_task_budget_stop(campaign_root, *, management_permit_bytes, expected_sha256)`，不改已冻结 v18 源、scope/admission 或模型入口。管理许可独立绑定管理器源码、当前 v18 源及入口、原计划、v18s1 scope/admission、准确首题 id、冻结的全状态/调用/动作哈希和预算计算。它不是模型派发许可。

1. 持有原 registry `.dispatch.lock`，在同一 `BEGIN IMMEDIATE` 中重新核保存来源、stage/phase paused、当前 case_01、其 14/12/0 计数不变、所有新 call complete、有且仅有原 80,000 隔离债务、所有已收费合法动作已 applied 且无未结工作台槽。核首题 `exposure+80,000>600,000`、余域及 case_02 的下一次准入空间，并拒绝来源/状态竞态、其他停止原因、过期后派发或新的 unknown。
2. 只更改当前治理行：首题 `status=budget_stopped`，保存无 final、停止原因、账务和来源哈希的 terminal evidence；`current_task` 推进固定 case_02；stage 仍 paused。调用当前 v18 `SupplementalStage._write_state`（`:351–371`）写完整 before/after 审计，并追加管理事件。原 v15 run/call/plan、v16 correction、既有 v17/v18 call/action、工作台行与所有计数原件不改。原 80,000 继续属于首题，同时计入 stage exposure。相同管理 id 与同一既成结果可幂等返回；不同前态或企图换终态应拒绝。
3. 保存管理回执，经独立合成反例验收后，由主控另行显式使用**原** `resume/run`，继续同一个 v18s1 至原 end（约 `2026-09-07T10:16:47.524738Z`，以冻结原字节值为准），不增窗、不重试首题、不重新创建四题。每题仍仅接收自己的普通包、完整自身历史和真实剩余额度，不传入首题候选、结论或管理审查材料。

静态兼容性明确：v18 `read_continuation_projection:238–255` 按 applied observation 核 round/query/candidate/final 数，task 状态独立返回；它没有要求 `budget_stopped` 必须有 WB final。因此只改治理终态而保持 14/12/0 可被投影表达。phase `_phase_binding:149–177` 核完整审计链和旧许可原件；`_write_state` 支持这类治理变化并保持 phase paused。显示层已存在“预算停止”标签，`:409–415` 只对 ready 状态用当前 call 覆盖显示，不会把该终态改写为 awaiting_action。原 `run_remaining` 开头读取已 applied correction 会直接返回其保存 observation，不再次执行工作台；随后按新的 current_task 开始 case_02。以上是**静态接口可行性**，没有对实际库试写或调用这些方法。

必要定向反例仅需覆盖：同一管理许可并发/重复最多一次；精确前态变化、任何新 unknown、未应用收费回答或 pending 槽均拒绝；债务归属及总账不变；治理审计写失败整事务回滚；case_01 不再调度而 case_02 仍受原额度/原窗口和上下文隔离。不要调用 v15 的 `task_terminal()` 来改旧 run，那会破坏原不可变来源绑定。

## 证据和结论边界

原始账务/控制字段和逐行哈希保存在 `experiment_traces/meta_ashare_revision18/supplemental_stop_semantics_audit/001_saved_only/evidence.json`，SHA `ee9eccd63acbded07c90868f8921076f34625a65cccfb8b586acf1f9a6e54dd3`。它含所有本次引用规则/源码及原 worker 文件的精确 SHA；stdout 只哈希，未解析其中回答。进程快照 SHA `3a1e82b8be0749df011798fb8f7f6ae6aceb2b0ff0728de3d234548bdd2d6924`；工作台 SQL 计数快照 SHA `f54c85bffb21ece547a436dd33e1f16adaa3135fd00dbed80cfee320e62cea0b`。全部连接使用 mode=ro 和 SELECT 白名单，没有构造工作台或读取其数据文件。

两问自检：**是否将控制停止混成研究失败或成功？** 没有。首题现无 final，不能挑中间内容替代最终报告；其证据质量本审计未评价。**是否建议靠扩预算或重试继续？** 没有。推荐仅在原已允许的管理出口内保留首题预算终态、原四题分母及全部债务，显式推进余题。若出现未结动作/新 unknown、来源漂移、无法审计的治理变化或已到窗口末端，就推翻当前可推进判断并继续停止。`execution_valid=false`、正式策略成功与独立样本增量均为 0。
