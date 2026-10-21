# v15 Casebank 研究批次与恢复：独立审查 001

2026-09-07，Astra/xhigh。**新协议控制器在本次离线工程范围内有界通过，稳定版本未发现未关闭的实质阻断。六项独立反例全部通过，20.05 秒。** 这是新公开工作台与持久调用账务的控制链审查，不是实际四题发现、模型能力验收、架构比较或收益证明，也不构成付费派发授权。

审查只新增独立测试、独立运行凭证和本文。未修改作者源码/测试、v14/v13、原题包、旧两题协议或旧付费记录；未读取实际四题目录、私有种子/标签/参考、真实市场行或封存行情。新增真实研究 Gateway 调用 0、策略回测 0。假进程写出的事件和 token 均明确属于算术测试，不能混入真实调用计量；工程评审模型 token 未在这里独立计量，不能记成零。

## 新协议及普通输入路径

审查代码为 `meta/casebank_campaign.py` 与 `meta/casebank_live.py`，并核对了它们直接调用的 Store、公开工作台、Gateway 及旧模块中的通用锁/账务/保存回执函数。新 registry 必须是独立目录，marker、plan kind 和 source/protocol hash 都使用 `casebank_research`；没有采用旧两题的 case 数量、动作、预算常量或 journal。

计划事前固定 1–8 个 task-run。相同 case/arm 不可重复，成对两臂必须绑定相同公开 packet 与 manifest，各任务采用同一公开合同和相等的单任务限额；run/task/arm/scope/初始工作台身份各自绑定。`task_binding` 只注册新且未使用的工作台，`state` 会重新核对实际 binding、完整 artifact、机会计数、工作台语义状态及完整公开上下文。

每任务最多 17 次模型调用、16 次 query、12 个 candidate、1 次 final、每步一次尝试；全部任务共享事前冻结的 stage call/token 上限和在途预留。每任务限额相同不等于所有任务一定有足够阶段余额完成，更不等于已证明完整架构公平性；阶段预算停止和未完成任务必须保留，不能补抽替代任务。

`execute_task(..., gateway=...)` 要求调用者显式提供网关，没有默认构造。每轮使用当前公开工作台 schema，并提供本任务此前所有已验证模型响应 JSON 与所有返回的公开工具观测；没有自动摘要或截断，也没有另一个 task 的模型历史。原 `inspect_inputs/diagnose_horizons/condition_events/submit_research_report` 动作和工作台 v2 的字段及总请求字节合同继续适用。旧 `inspect_execution/submit_diagnostic` 协议没有被套入这条循环。

## 预读时收口的两点

源码尚在初稿时，审查者指出两处应先核对的边界；作者在首轮稳定回归前完成收口。这两点属于静态预读，不虚称有一个已经运行失败的生产请求或独立失败测试。

1. `state` 原拟对完整 Store body 求 hash，但现有工作台终态 `recover_saved` 即使只是核对，也会刷新顶层 `updated_at`。连续只读上下文可能因此不相等。稳定实现只排除这个顶层读取时钟，创建时间、全部请求、身份、计数、阶段和结果仍参与语义 hash；作者普通 query→下一轮测试及本独立“实质状态改变”反例共同检查这个边界。
2. live 直接调用 `diagnostic_live._save_local_receipt`，所以稳定 `sources()` 将 `diagnostic_live.py` 一并纳入固定来源；没有只固定调用方而遗漏这个直接恢复/持久化依赖。继承模块本身未因此修改。

## 账务、执行顺序和停止语义

Store 的 `reserve_call` 在同一 `BEGIN IMMEDIATE` 事务内执行 stage 总调用数/总 exposure 检查、任务单独准入检查和插入意图。`call_budget_usage` 对未完成/用量不全的调用保留全部名义预留，同时计入已知 token；完成用量不能降低已持久化的用量字段。暂停与 dispatch 使用跨进程文件锁，实际派发还要求当前线程拥有 registry 租约。

正常次序为：完整 prompt/schema/state 绑定并预留 → 保存请求 intent → 唯一 dispatch 标记 → 保存事件/回答经 `verify_saved_completion` 独立重放身份 → 本地 receipt → 调用账务 receipt → campaign action intent → 工作台动作 → campaign apply。保存结果必须匹配原响应、前状态、slot、scope 与工作台实际机会增量。

暂停阻止新派发，已完成模型结果可以保存计量而暂不应用。调用未知、用量缺失、来源/响应冲突、工作台未知结果或未核对历史均停止阶段；没有自动重试或新槽代替原请求。已知工具失败仍按原工作台合同消耗机会；非法 final 结束对应任务并保留 failed 身份。

`reconcile_saved_only` 没有 Gateway 参数或构造，也不调用工作台 `execute`、期限核或匹配核。已完成回答但工作台从未启动时，即使回答是 final，也停止为未应用；不会在恢复时补执行。已保存工作台完整结果但 campaign 尚未 apply 时，可以只核对保存文件并结算原动作。恢复不是新调用、新候选或独立研究样本。

## 六项实际独立反例

全部输入来自独立 16 会话 × 4 中性符号公开算术 fixture。复用测试用假进程运输器，让真实 Gateway 解析器和保存完成验证器核对明确的合成事件；没有 mock 掉核心保存完成验证。测试全局阻止网络、真实模型进程、私有路径和实际四题目录。只有第一项为验证跨进程租约，明确允许两个短 Python 子进程读取该临时 fake ledger 和锁；它们不构造网关或工作台。

| 反例 | 实际通过条件 |
| --- | --- |
| 跨进程 lease 与孤立预留分别检查 | 父进程持有租约时子进程不能取得；释放后子进程可取得锁，但仍读到 1 个未结调用。正常循环看到该原预留后拒绝重派，fake Gateway 调用数为 0，80,000 预留保留。取得空闲锁不等于原请求可重试。 |
| stage 剩余额度恰好等于下一预留 | 原阶段上限 80,013，首任务合成完成用量 13，剩余恰为 80,000；第二个事前固定任务可正常准入。两任务累计 26，不重置首任务用量，调用目录不同。与作者“少一个 token 则拒绝”测试构成边界两侧。 |
| 完成一次 query 后上下文超限 | 第一次调用与 50 行公开观测完整保留。第二轮完整上下文超过事前 ceiling 时，在新调用前停止；所有原响应/观测仍可读取，没有被截断。resume 后再次尝试仍停止，总 fake 调用保持 1、已知用量 13、预留 0。 |
| 排除读取时钟不能排除实质状态改变 | 模型在途期间注入一条工作台 rejections 状态记录，不改变 query/final 计数。保存模型完成后，前后语义 state 不一致被识别；工作台动作数 0，campaign 没有新 apply intent，13 用量保留。恢复仍不补执行那个 final。 |
| 较低 final usage 不得抹掉较高已保存 callback | 先持久化 input=25/output=0，再返回合成终态 input=10/output=3。控制器保留已知 28 并保留 80,000 未结预留，停止本任务和后续任务；保存重核也不能把 28 降为 13。这个故意矛盾的测试不代表真实供应商曾这样记账。 |
| condition 已完成后关闭重开，仅恢复原结果 | 在工作台匹配动作完成、campaign apply 前注入中断，关闭并重开两个 Store。为 Gateway 构造、WB.execute、期限核、配对 plan 和匹配汇总全部设置失败陷阱；两次 saved-only 恢复均通过，原调用不变，candidate=1、原核启动=1，未新增计算。 |

独立检查命令仅运行 `tests/test_meta_casebank_live_independent_v15.py`，使用 v15/src 作为 PYTHONPATH。结果 **6 passed in 20.05s**；JUnit 恰有 6 testcase、0 failure、0 error、0 skipped。源码、独立测试及使用的 fixture/运输器 helper 在运行前后 hash 相同。

独立凭证：[casebank_engineering_attempts/independent_001/receipt.json](../../../experiment_traces/meta_ashare_revision15/casebank_engineering_attempts/independent_001/receipt.json)，SHA256 `cc2cd378f519552793652a16746635636f739b3b33e18be6040f003993532847`。同目录保留 input_hashes、原始 pytest 输出、JUnit 及各文件 SHA。稳定版独立测试没有失败，因此没有制造一个不存在的修前失败批次。

作者另外报告并保存 **15 passed in 221.57s**，包含完整 17-call/16-query/12-candidate/1-final 链、1 与 8 task、完整本侧响应历史、两臂隔离、未知用量、冲突回执、暂停及超长合法字段 final 的一次失败终态。其 receipt SHA256 为 `cbad490d188871a7e543c9c21d6932874bb505d84d67a8f1b8ed2c0808b406ed`，明确来自完成的 exec session 49194/chunk d488b0，没有另捕 pytest log。本审查复读其测试与 receipt、核对来源，不把这 15 项称为自己重新执行或带独立日志的一次运行；也没有为重复该长链再付出一轮测试。15 与 6 是两个分开的定向结果，不写成同进程 21 项回归。

## 最终来源与资格边界

| 文件（均在 `experiment_traces/meta_ashare_revision15`） | SHA256 |
| --- | --- |
| `src/quanta_agents/meta/casebank_campaign.py` | `4e56a278715b672d422c4aebe7468217c58920640c8f0b820efcb11c84e5d02a` |
| `src/quanta_agents/meta/casebank_live.py` | `8bd36896ed1445cae29addf3f7decabad05c171367ee60240c4f9a0603074a60` |
| `tests/test_meta_casebank_live_independent_v15.py` | `4d354b9e506f36a958357ca7ffa3c5e92b9734033dc9fd01ad8b6afab207b56f` |
| 作者 `tests/test_meta_casebank_campaign_v15.py` | `fb31f80d920a923ceb45ba0a546d1914073c0d95d242be05d4155928eee67086` |
| 作者 `tests/test_meta_casebank_live_v15.py` | `8749f335cd69eb839a41e7d01c084fa68badcad3ec57dd9352d7cd52a4794fe2` |

此控制器只管新 registry 内固定 stage，不覆盖旧诊断 campaign、其他目录或外部账户；名义预留不是绝对供应商账单帽，runtime 与上下文准入也不代表研究必定成功。固定请求仍是 Astra/xhigh，保存事件没有独立认证实际供应商模型身份，合成 receipt 的 `model_verified=false` 正确保留。

真实付费启动仍需要 root 单独冻结该新 stage 的具体输入、来源和预算并明确派发授权。当前只证明新代码可以通过假网关与公开算术输入走保存控制链；没有运行原四题候选发现、参考解、策略回测、架构比较或真实执行。`execution_valid=false`、`hidden_control_qualified=false`、`architecture_comparison_completed=false` 必须保留。

**这一步做得怎么样？** 完成稳定版本的独立只读审查和六项实际反例，覆盖作者长链之外的跨进程、剩余预留边界、上下文增长、语义状态漂移、用量回退和重启恢复。本范围没有待修的实质阻断；没有把假事件、单次合法 final 或工程测试通过写成真实研究成功。

**下一步该做什么，如何改进？** root 按这些固定 hash 与其余分工产物做组合工程验收，保留分别完成的测试回执，无需机械重跑已确认长链。是否启动任何真实研究须另行确定，不能根据本报告自动派发或增加替代 stage。若后来出现未结预留被释放、跨任务上下文混入、超限后静默截断、已保存回答不匹配原 intent、recover 调用网关/execute/核，或旧失败被改写以绕过预算，应停止扩展并保存反证。
