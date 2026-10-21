# 已知单题预算停止本地管理器：独立事前审查与反例预期 001

**设计结论：原范围允许这项另行冻结的本地管理；所述最小状态迁移可由现有 v18 账务审计承载。实现、测试和实际管理准入仍未通过本文件获得授权。** 此审查不执行 actual DB，不读中间研究内容，不修改冻结 v18 源码、首题评审或旧记录。

依据是原 `reference_scope_001.json` 的明确条款：“Known per-task budget exhaustion may be managed only by a separately frozen saved-evidence step, never by this automatic run permission.” 该文件 SHA256 `e72076081931c38629e4d096111f1eb156fa6add815789f3d2bbc4ecf404d8c9`。原 prereg 同时要求保留无 final 的 `budget_stopped`，只在确定为该题预算耗尽、无未结新调用或 pending 时另冻管理步骤。唯一旧 HTTP400 的 80000 隔离例外必须保留，不能推广至任何新 unknown。

本次仅静态读 v18 core `6e38e602…4f9919`、phase `83e04e88…5aa392` 及既存量表。`TERMINAL` 已含 `budget_stopped`；投影要求计数能对应已应用证据，不要求该行政状态拥有 final。`_history` 按任务分别获取自身完整回答、观察及原工作台；无动作的 case02 应仍与原零动作快照一致。`_write_state` 能在同一事务追加 phase before/after 审计。以上说明设计可实现，不是该新管理器已实现、通过或获实际准入。

## 必须冻结的最小管理范围

管理 scope 应精确绑定管理模块/入口 SHA、既存 v18 源和阶段身份、原 plan/scope/admission、当前暂停治理完整 hash、第一题及下一题 ID、完整保存调用/动作/工作台结果清单、预算与费用、管理 key 和允许的极小字段差异。本次只允许从原第一题移至原顺序第二题，不是任意任务跳过器。行政许可应明确 local-only，不能把新阶段既存 paid=true 当成对任意治理改写的授权，更不能把原 v17 paid=false 改成 true。

允许变化限第一题治理 `status: ready → budget_stopped`、`current_task: case01 → case02` 和明确的行政原因记录；保持 stage/phase paused，case02 的原 `not_started`、零计数和原工作台不变。所有 calls、actions、原 WB、原 scope/admission、预算和两个时间窗不变。不补造 final、abstain、模型回答、工作台完成回执或研究样本；首题原已冻结评审仍为无 final、未完成。行政分类作为新记录追加，不回写原观察。

`_write_state` 所依赖的当前治理 hash 与 phase 审计根需要随迁移同步更新；这是允许的治理审计变化，不应错误宣称整个 phase 行字节完全不变。phase scope/admission、起止、原快照及固定字段必须原样保留，只有当前 state hash、审计序号/字节量及对应的暂停管理信息按冻结差异更新。

## 缺少以下门应阻断

1. **完整证据与精确原因。** 独占原 registry 的同一跨进程 lease 后，在事务内重核 paused、原暂停原因和预固定完整状态 hash。不能凭字符串“cap exhausted”直接推进。所有 13 笔新调用必须有完整、重新核验的 request/receipt/usage/source/task/phase 身份；14 个既有动作均 applied，WB 原产物与 post-state 一致，无未应用完成回答、未知工具结果或任何 pending。其他三题必须是冻结的原未开始状态；不能忽略全域中的新 unknown。
2. **真实是单题门，而非全域门。** 按原账域重算 `input + output + 未结预留`。首题 `own exposure + 80000 > 600000`，全域 `stage exposure + 80000 <= 2400000`，全域传输仍有空间，下一题自身也能容纳原一次预留。等于上限属于仍可准入，不能错标耗尽。唯一旧 unknown 用精确 call ID、原 hash 和 80000 匹配，不得释放或抵扣；cached/reasoning 仍不重复计入。当前意向的 491954 + 80000 = 571954、下一预留 651954 仅来自已冻结首题回执 `c152b7cb…ceab8f`，不能在管理时拿缓存数替代锁内核算。
3. **事务、幂等和恢复。** 管理事务记录、状态迁移、phase 审计必须同一次 SQLite commit。若外部 JSON 回执在 commit 后尚未写完就崩溃，再次调用只能核验已提交管理记录并导出原结果，不得再迁移、重算工具或自动 resume。提交前失败须回滚全部治理变化，或留下明确无副作用的 pending 意图并禁止盲重试；必须在合同中固定一种恢复方式。
4. **查幂等结果不能被旧 pre-state 门误伤。** 同 key 已提交时，应先验证其 scope/source/结果与审计链，再只读返回；不能因为当前已是 post-state 就要求调用方另换 key。另设 phase/from-task 管理目的的唯一性，或同等严格的不可二次消费门，防止改 key 再跳一题。同 key 不同 payload/source 直接拒绝。
5. **不扩大授权。** 管理器不构造 Gateway、不调用 WB.execute/recover、不运行原 run/resume，也不自动申请第三窗口。原窗口已关闭时不得把“下一题已选定”呈现为可派发；本次范围宜明确要求管理在原补充窗口内完成，或至少把窗后操作限定为只读取回已提交结果。随后 resume/run 必须是独立步骤，在原窗口、原预算、原未知门下重新准入。
6. **下题上下文仍只属于下题。** 管理原因使用固定行政代码，独立评语、私有机制和 case01 观察不能通过额外 state 字段或新 prompt 模板混入 case02。沿原 controller 生成 case02 prompt，公共 packet/overlay、scope/arm、零历史及剩余机会必须保持原样。允许既有公开全域资源计数变化，不要求把原未知预留或累计次数藏起来。

## 六项核心离线验收预期

只用合成临时账本、合法保存 fake-process 回执和公开算术 fixture。以下是先验判据，尚未运行；作者给出稳定接口后映射到独立测试，不因接口尚未落盘产生空失败。不重跑旧 20 或全部控制器测试，不把作者整组复跑算独立反例。

| 编号 | 干预 | 有判别力的预期 |
| --- | --- | --- |
| M1 精确成功与下题隔离 | 冻结单题耗尽、全域未耗尽的干净 fixture；case01 自身历史及管理元数据各放不同合成 canary。执行一次管理，之后只调用原公共 prompt 生成器读取 case02。 | 仅允许字段和审计发生变化；第一题 budget_stopped、第二题仍零动作/not_started、current_task 第二题、stage/phase paused。原调用/动作/WB与预算时钟全不变。case02 prompt 无 case01/管理评语 canary，自己的普通 packet/overlay、空 own history 和原机会数完整。Gateway/execute/resume 陷阱均未触发。 |
| M2 单题与全域数学边界 | 同规格有限参数：本题加预留恰等于 cap；本题超 cap 但全域也超 cap；合法本题超 cap、全域恰等于 cap。 | 前两种拒绝管理并零写入，后一种可过该数学门；不能把全域预算停伪装成可继续其他题，不能把 `>=` 写成误耗尽。传输门与下一题自身门保持原规则。 |
| M3 新 unknown / 未应用 / 工具 pending | 分别保留新 incomplete receipt、完整回答尚无 applied action、WB pending 或其原 artifact 缺失。 | 管理全部拒绝，原预留保留、所有治理和管理提交记录不变；唯一旧隔离 ID 不能让新 unknown 获豁免。可用同一测试函数的少量参数表达，不展开大矩阵。 |
| M4 两个真正进程并发与换 key | 两进程共享临时账本/同 lease、同 scope 同 key 同时管理；随后用另一 key 试图消费同一个 phase/from-task。 | 一次实际状态迁移、一次 phase 审计增量和一次管理提交；另一进程只读返回同结果。不同 key 不得再前进，不能跳到 case03；不是仅在线程内模拟互斥。 |
| M5 两个持久化崩溃边界 | 事务内 intent/更新尚未 commit 时故障；另在已 commit、外部回执未写时故障。 | 第一种按冻结协议回滚或明确 pending 不推进；第二种 load/管理重入只导出原提交结果，零新增治理序列/机会/费用，且在禁止 Gateway/WB.execute/resume 的陷阱下通过。原 pre-state 与 post-state 证据都保留。 |
| M6 陈旧身份与有界来源 | 改预固定 state/source/payload 或用别题同值 response/receipt 冒充本题完整性，保持部分表面 hash 自洽。 | 在任何状态迁移前拒绝；已提交同 key 的内容不能被新 payload 重命名覆盖，实际回执身份必须对应原 task/source/phase。证据不符不是“预算停”的可管理成功分支。 |

这些六个逻辑场景可落为约六个测试函数，少量必要参数覆盖仍应控制在 20 项以下。真实事件/协议反例先保存失败及源 hash，再让实现作者作最小修复；纯断言/fixture 错误另记，不冒称产品缺陷。接口确认前不编写实现、不运行实际账本，也不以这份预测替代测试通过。

两问自检：**该设计是在兑现原管理条款，还是重新给首题一次机会？** 只有在保持首题无 final、旧费用、旧时限及原次数并且零重派时，才是原条款允许的行政归类；否则应拒绝。**什么结果会推翻当前“可实现且最小”的判断？** 任何无法原子绑定审计、提交后重入再推进、全域/未知绕过、改写 WB/原回答、默认 resume 或下题上下文污染都会推翻，需要保存反例整改。本文件未给实际管理准入，工程评审计量未知，研究 Gateway 调用为 0。
