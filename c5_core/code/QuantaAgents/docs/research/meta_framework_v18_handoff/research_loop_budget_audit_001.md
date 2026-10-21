# v18 保存研究循环预算审计 001

结论：**首题停止的直接原因是单题名义 token 准入不足，不是查询上限、最终报告额度或截止已到。** 公开提示已持续给出余额与工具余量，但控制器没有保护最后一次提交的独立额度，也未在最后可负担调用前进入仅可提交状态。末次模型返回查询；结算后任何下一调用（包括 final）都被正确拒绝。只据元数据不能判断模型为何选择该查询，更不能断言提醒或另一种提示一定令其提交成功。

本审计读取原 plan、只读 SQLite 的字段投影、保存 prompt/schema/event 的结构/字节数/usage/action 类型。未读取私有机制或参考解、未向审阅上下文导出候选条件值/结果值/模型论证；未构造控制器或 Store，未修改 v18/原登记册，未运行测试、回测或新增 Gateway。工程与本资料审计模型用量不为零，与“本子任务新增项目 Gateway 0”分开。

## 冻结范围及事实

原计划仍为 4 个 baseline task，Astra/xhigh；每 task 17 次传输、16 query、12 candidate、1 final、每步 attempt=1；阶段 68 次，单题 hard 600000、阶段 hard 2400000、名义预留每调用 80000、soft 300000、单调用 3600 秒、原总窗口 21600 秒、公开上下文上限 1 MiB。v18 只增加独立登记的时间窗口，旧未知账务、全部已用额度及失败分母继承。原题包、评分、预算无修改。

当前有 15 次保存传输：原 schema 失败未知 1、纠错完成 1、续行完成 13；14 个已应用 query，其中 12 个 distinct `diagnose_horizons`、2 个 `inspect_inputs`，全部已保存工具状态 completed，0 final。三个后续 task 未开始，全阶段 paused，原暂停理由为 `admission stopped: original nominal exposure cap exhausted`。

- 已报告 input 482538 + output 9416 = **491954**，已知 cache 0；原未知保留 **80000**，总名义暴露 **571954**。
- 单题剩余 **28046 < 80000**，下一 final 同样无法准入。传输仍余 2、query 仍余 2、final 仍余 1；阶段还有额度但不能转给首题突破冻结单题上限。
- 14 次完成 usage 均与保存 turn usage 的 I/O 相符；未把 reasoning_output 或 cached_input 重复加总，原未知用量没有当作已知零。
- 末次派发距新增窗口截止仍约 **19636 秒**；该次停止由预算门触发。没有把此前原窗口到期事实改写为完成。

| 保存传输 | 返回动作 | 发起前单题名义余量 | 当时 query/candidate/final 余量 | 输入 token | 本次 I+O |
| --- | --- | ---: | --- | ---: | ---: |
| 3 | diagnose_horizons | 508765 | 15 / 12 / 1 | 17664 | 18511 |
| 13 | diagnose_horizons | 185198 | 5 / 2 / 1 | 48685 | 49108 |
| 14 | diagnose_horizons | 136090 | 4 / 1 / 1 | 51809 | 52112 |
| 15 | inspect_inputs | **83978** | **3 / 0 / 1** | 54900 | **55932** |

末次 schema 只允许 `inspect_inputs` 或 `submit_research_report`，并未移除 final；模型返回了 inspect。83978−55932=28046，随后准入拒绝。不能将末次查询的实际费用当作反事实 final 的费用；也不能通过释放旧 80000 未知预留“修复”该失败。

## 每轮到底提示了什么

原失败调用与纠错复用相同 9919 字节 prompt，包含工具/最终余量及通用预算说明，不含具体单题 token 数值余额。13 次续行的保存 prompt 均含：`ordinary_workbench.remaining` 的 query/candidate/final 余量；`continuation_resources` 的 task/stage 传输余量、名义 token headroom、原未知预留、下一次 80000 预留；`supplemental_time_resources` 的新固定起止、原截止与原阶段已到期说明。最终提交始终在允许动作内。

缺口是没有动态 `now/seconds_remaining`、没有 soft300000 跨线专门提醒、没有可负担未来轮数或“本次为最后可负担提交机会”的显式状态，也没有 final 专属名义额度。task 工具余量与 token 余量是相互独立的最大值，不构成“还保证能执行这些轮数”。当前实现由模型选择动作后才知道它是否 final，准入之前没有关闭探索权限的预算状态。

对应源码：`experiment_traces/meta_ashare_revision18/src/quanta_agents/meta/casebank_continuation.py:556` 保存完整本侧历史，`:586` 构造资源提示，`:744` 原子名义预留，`:790` 捕获准入失败并暂停；`casebank_supplemental.py:396` 附加固定新窗口。实际 Workbench 从原 v15 冻结来源加载，`experiment_traces/meta_ashare_revision15/src/quanta_agents/meta/casebank_workbench.py:258` 动态 schema 与 `:283` 剩余额度；不能把 v18 的副本误称实际执行来源。计费函数为 v18 `store.py:28`。

## 成本为何持续增加

13 次续行 prompt 从 **27188 → 151689 UTF-8 字节**，输入从 **17664 → 54900 tokens**，相邻输入平均增加 3103 tokens。输入占全部已报告用量 **98.086%**。13 次 prompt 合计 1162693 字节，其中反复发送的 public_history 合计 1024075 字节。末次 public_history 139893 字节，占 prompt **92.224%**；先前模型公开 response 仅 2425 字节，占 **1.599%**。字段长度进一步表明历史 body 占 123148 字节，不能简单归因为 evidence ID 太长或“模型隐藏思维全部回传”。tokens 与字节不是一比一，供应商系统包装/分词也不能仅由本地字节精确分摊。

每个诊断的公开 observation 约 10.2 KiB；完整 artifact 约 4.8–8.3 MB，只以身份/摘要进入提示，本审计未打开这些全量结果。12 个候选分别登记 3 horizons、9 selected/benchmark/increment 子项，共 **36 horizons requested、108 子尝试**；它们不是 108 个独立研究样本。12 个请求 hash 不同，duplicate_of 均为空，费用增长不来自同槽重复重算。

第一次 inspect 取得 inputs 第 0 页 50/1536 行，约 17.7 KiB observation；最后 inspect 取得 comparison_events 第 0 页 50/770 行，约 33.6 KiB observation，cursor 都为空。末次返回页未进入任何后续付费 prompt，不能把它归入已报告输入膨胀。分页每页仍消费一个 query、一个模型调用机会；现有 schema `queries.maxItems=1`、`candidates.maxItems=1`，不能一轮免费批送 12 个候选。单候选已把三个期限放在同一次公开动作内，没有额外三个研究模型调用。

逐轮保留完整本侧上下文是已冻结公平合同。直接删历史、只传有利结果或凭人工结论压缩会改变研究条件。批处理可能减少多轮重复前缀，但会同时改变信息顺序和可适应性，必须新版本/新范围、双方同合同、逐内部候选计费；它不应夹带进本次最小修复实验。

## 最小可证伪后续工程实验（仅设计）

建议首项只增加持久 `explore / close_only / terminal_without_submission` 控制状态和公开收尾额度，不改候选内核、历史内容、评分或现有失败记录。另起新版本/新 scope，在看到结果前冻结关闭策略：`closing_reserve=80000` 先作为与原准入口径一致的名义实验值，同时保留一个传输槽和唯一 final 工具机会。它不是新硬账单帽，也不是建议恢复本次原 task。

1. 在原子派发事务内同时考虑单题与阶段 headroom。只有能覆盖本次探索名义预留 + 关闭预留，且至少两个传输槽、剩余时间满足事前固定的关闭余量，才允许探索 schema；否则若可负担关闭调用则持久进入 close_only，schema 仅允许模型亲自返回 `submit_research_report`（原完整 final 合同含合法弃权）。每轮公开 state、reserve、各余量、固定截止/当前剩余秒与触发原因。状态恢复不能再开放探索或免费补调用。
2. 若关闭调用也不可准入，写 `terminal_without_submission`/budget_stopped 的主控终态并保留 0 final；绝不自动生成 summary、证据主张、弃权报告或假造模型提交。调用失败、非法 final、未知费用都保留原分母，未知继续停，不能自动重试。
3. “可收尾”只能保证控制器预留一次名义提交机会或明确失败终态，不能保证模型输出合法/有质量，也不能在供应商未证明可控输入/输出上限时保证实际账单不挤占预留。80k 只是名义值；任何真实超出仍诚实记账并停止。后续若要账单意义的保证，需要另行验证有界完整 prompt 的计量及供应商可执行的输出限制，不能从本次均小于 80k 的样本推断绝对上界。

零模型的第一步验证可直接使用本次保存元数据做确定性 admission replay，再用公开小 fixture/FakeGateway 验证控制状态，不重跑候选内核或回答私有问题。本次序列中，双预留阈值 160000 会在传输 **14** 前首次进入 close_only（headroom136090、还有1candidate/4query/1final）；这仅证明控制门在何处改变，不能声称实际模型当时会提交成功，也不能把未发生的 final 用量填成已知。

最小离线反例应包括：headroom 恰等双预留及少1；阶段共享余额比单题先耗尽；只有一个调用槽；原未知80000不可释放；close_only 中非final被拒且机会收费；最后合法弃权需真实保存模型动作才计final；关闭动作语义失败/未知不重试；关闭意图后重开只saved-only；截止竞争费用仍可读。所有模型/工具调用替身和参考计数公开标成工程 fixture。预先失败标准：关闭模式仍可研究、恢复多一次调用、自动代写final、隐藏0final/失败分母、旧未知归零，任一出现即否定改进；实际输出质量提高与四题成功率仍未检验。

两问自检：这一步用保存元数据证明了什么？证明了 token 准入停止与完整工具历史增长，排除了“final schema 不可用”和“新增截止已到”这两个直接解释；没有证明模型的内在动机。下一步最有信息价值的工作是什么？只做上述离线原子关闭门实验，先证伪恢复/额度竞争，再决定新范围准入；不在此报告放行任何新调用。

## 可复核证据

`docs/research/meta_framework_v18_handoff/research_budget_audit_001/metadata.json` SHA256 `e2d95319bf146b04aca7118e812b0b65ed5dcfe2cf1f19e58ac46d74093537f5`，含15传输元数据、14动作类型/长度/计数、全部已读取 prompt/schema/events SHA、实际来源 SHA、ledger 读前后相同 SHA。`derived_summary.json` SHA256 `8542bf66670f3b1b273c38bff158b12e5d719a5f00204008aaa62c2cfeb01c74`。原 plan 文件 SHA `bc3ef9d9acdd918f0a7e28095654f890aeee4946f519a19ac7c4ac0ad6031448`、content plan hash `3cda519a83180b6b5b4fe96c447a406efee82b5df73b31cc450fd10555437173`。
