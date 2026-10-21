# v15 普通题包研究循环接线 001

本增量把普通包工作台接到独立的多轮研究控制器：完整本侧公开上下文 → 原子预留模型槽 → 保存 intent/事件/回执并复验 → 持久动作意图 → 工作台工具 → 本侧下一轮 → 唯一 final。已完成离线 fake process 端到端，作者 15 项和独立 6 项定向检查分别通过。它没有发起真实研究、创建真实研究阶段、执行四题参考发现或完成架构比较；源码当前停止修改，等待 root 联合验收。

## 来源和文件责任

父版本 v14 组合验收 source hash 为 `64f72b6057ceb815cf706d05e52de2b276e659583aa41b68ccb84fa3dd8408e2`，receipt `experiment_traces/meta_ashare_revision14/validation_attempts/20260906T195723544594Z/receipt.json` SHA256 为 `7723a1c7f2e02da8d1b76d37459ca293c06a53a18c8cbc54b5fab46a51ba0b83`。先核该 receipt 及其中 93 个源码文件，再独占复制 src/tests/scripts 共 232 文件至 v15；不复制运行、campaign、验证、fixture 输出或缓存。`experiment_traces/meta_ashare_revision15/copy_source_manifest.json` SHA256 为 `1d52b078b1d1ac36346083e6979bd7e99b7aaf85e14704f61f3c67a0beaed27d`。

本作者只新增 `meta/casebank_campaign.py`、`meta/casebank_live.py` 和两个专组。冻结的 CasebankWorkbench v2、Store、Gateway、期限核、匹配核和两诊断旧协议未改；未读取真实四题的候选结果、evaluator_private、生成器种子或真实行情。

复用范围是 Store 的 `reserve_call(admission_check=...)`/`call_budget_usage`、Gateway 的真实文件/事件验证，以及旧诊断模块中不读取旧 BUDGET/case/action 协议的锁、用量和保存回执方法。新控制器没有继承旧类的两 case 计划校验、原动作分派、固定 34 call/120 万预算或旧 resume。`kind=casebank_research` 和新 registry marker 会拒绝旧诊断目录；没有打开旧 Engine。10 个执行依赖分别纳入计划 `source_files`，包括间接使用的 `diagnostic_live.py` receipt helper。

来源复核时 v14 的 232 个继承文件全部未变；v15 的继承差异仅另一个作者按 root 后续授权修改的 `raw_portfolio_backtest.py`。本作者未修改它，它也不属于研究循环的 10 个执行依赖。联合验收应分别核对这项执行桥改动，不能把整个 v15 宣称为继承文件完全未变。

## 最小可用接口

```python
from quanta_agents.meta.casebank_campaign import (
    CasebankResearchCampaign, TASK_LIMITS, content_hash, sources, protocol,
)
from quanta_agents.meta.casebank_live import (
    task_binding, public_model_prompt, execute_task, reconcile_saved_only,
)

# 每个 workbench 必须是普通 loader 创建的新 CasebankWorkbench，
# run_id 与它的 task_run_id 相等。先固定它们，再冻结 stage plan。
bindings = [task_binding(workbench, task_run_id)]
source_files = sources()
plan = {
    "kind": "casebank_research", "run_id": "a_new_fixed_stage",
    "model": "gpt-6-astra", "effort": "xhigh",
    "source_files": source_files, "source_hash": content_hash(source_files),
    "protocol_hash": content_hash(protocol()),
    "tasks": bindings,
    "budget": {
        **TASK_LIMITS,
        "max_calls": 17,
        "hard_tokens": 600000,
        "per_task_hard_tokens": 600000,
        "reserve_tokens_per_call": 80000,
        "soft_tokens": 300000,
        "max_call_seconds": 3600,
        "max_runtime_seconds": 14400,
        "max_public_context_bytes": 8000000,
    },
    "exposure": "explicitly declared by the controller",
}
campaign = CasebankResearchCampaign.create(new_isolated_registry, plan)
# 本文只说明接口，不是授权运行真实 Gateway；本轮测试传假进程 adapter。
result = execute_task(campaign, task_run_id, workbench, gateway=explicit_gateway)
restored = CasebankResearchCampaign.load(campaign.root, plan=plan)
saved_result = reconcile_saved_only(restored, task_run_id, workbench)
```

上述数值是一个 task 的接口示例，不能自动推广为真实研究预算。阶段预算全部是显式计划字段；真实使用必须另行冻结准入。`execute_task` 的 gateway 是必填 keyword 参数，没有 `gateway=None` 后默认构造真实 CodexGateway 的路径。`reconcile_saved_only` 无 gateway 参数，也不构造 Gateway、调用 Workbench.execute 或任一个数值核。

公开方法还包括 `reserve_call(task_run_id, round_index, *, prompt, schema, workbench_state)`、`mark_dispatch_started(call_id)`、`record_usage`、`save_receipt`、`save_failure`、`apply_intent`、`apply_completion`、`task_terminal`、`pause`、`resume`、`summary`、`dispatch_lease`。底层控制器接收的身份和 state 是受信 harness 提供的值，不是允许模型填写的公共动作。模型只拿工作台自己的 action schema。

## 新阶段合同与预算

每阶段固定 1–8 条唯一 task_run。每条绑定 task_run_id、case_id、baseline/candidate、普通 packet hash、public manifest SHA、工作台 scope、公开合同 hash 和初始工作台身份。同 case 两侧必须使用相同原 packet/manifest；所有任务的公开合同必须一致。重复 case-arm 不可作为补跑加入阶段；创建后不能替换计划或偷偷追加第九条任务。

| 固定每任务限制 | 值 |
| --- | --- |
| per_task_max_calls | 17 |
| max_queries_per_task | 16 |
| max_candidates_per_task | 12 |
| max_finals_per_task | 1 |
| max_attempts_per_step | 1 |

`max_calls <= 17 × task 数`，`hard_tokens` 是所有任务共享的 stage cap。per_task_hard_tokens、reserve、soft 和两个 runtime 限制均在同一计划冻结，不能每个任务重复获得整阶段 hard budget。每个任务 token cap 相同；固定顺序串行执行。若阶段总 cap 低于各任务 cap 总和，后序任务可能因阶段余额停止；这必须在比较分母中保留，不能据相同工具合同宣称所有任务已经获得相同实际探索量。

模型固定 Astra/xhigh。每条 call attempt 恒为 1，不设替代调用或自动 retry。已知 I/O 回调、返回值、异常携带用量逐项合并，不能降低；failed/uncertain 即使已知部分费用仍保留全部名义 reserve。只有真实保存文件/事件与 request intent 相符、最终 I/O 完整的 receipt 才结算。最终回执不能覆盖更高的已记录用量或另一个 completed receipt。此处的 nominal exposure 不是供应商绝对账单帽，也不替代工程用量统计。

Store 在同一 BEGIN IMMEDIATE 事务内重新检查阶段状态、当前 task/round、原工作台计数、阶段及每任务 call/token cap、运行时限、未知 call 与唯一原槽，再写入 call intent/reserve。跨进程和同线程对象的非阻塞 dispatch lease 限于新 registry；控制锁不跨模型等待持有，暂停可以在在途期间登记。暂停不杀正常在途请求，完整回执可保存，但下一公共动作/模型调用不会自动开始。

`summary.status=running` 只表示允许准入，明确 `worker_liveness=not_verified`。summary 给出当前已知阻断原因，仍须取得 lease 并通过原子 reserve；不存在用 UI 状态代替最终准入检查的许可。

## 完整公开上下文与恢复

每轮 prompt 包含当前任务的原工作台普通 packet/overlay、所有已经返回的公开工具 observation，以及此前每轮经过保存事件复验的完整模型 response JSON。工作台摘要、页面、失败、弃权和候选规格不被自动改写成摘要；其他任务/架构的回答、页和评语不进入本侧 prompt。内部 reasoning/event trace 留在原 Gateway 审计文件中，不伪装成额外公开工具证据。

完整 prompt 超过冻结的 max_public_context_bytes 时停止准入并保留原证据，不截断历史后继续。同样，超长 final 仍交给已修复工作台 v2 的有限 JSON 拒绝登记，消耗唯一 final；不能在外层把它丢弃为免费重试。

call request_identity 包含 task/arm/scope/packet/source/protocol、Gateway 口径 prompt/schema hash、完整 schema、前置工作台语义 state 和 hash。Gateway 哈希继续使用 `json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)` 的既有口径，不与 sorted/compact content_hash 混淆。call 文件夹为原槽独占新目录，保存 prompt、intent、完整 Gateway 请求/事件/输出/进程退出和本地 receipt。harness 再用 `verify_saved_completion` 重放原文件，不能仅信任 gateway 返回 dict。

工作台语义 state 保留全部身份、原创建时间、请求、计数、阶段、文件身份、结果和公开上下文 hash；只排除 Store 在终态只读验证时也会刷新的顶层 updated_at。这个排除不允许改变既有页、动作、成本或原来源。下一轮会核对最新完整 public state 与之前的 post_state。

| 保存边界 | 恢复行为 |
| --- | --- |
| 完整模型 receipt，尚无公共动作 intent | 只复验/保存费用，返回 saved_model_action_not_applied 并暂停；包括 final，也不补执行。 |
| 动作 intent 已保存，Workbench 尚无对应 slot | 保留 saved_public_action_not_started；不调用 execute。 |
| Workbench 已登记但缺完整 receipt/原文件 | 保留未知并停止，不重算工具或另发模型。 |
| Workbench 已完整结算，campaign 尚未 apply_completion | 核对原请求 hash、完整 artifact/receipt 后 saved-only apply，推进原槽；不增加模型或工具机会。 |
| 原模型/工具文件、返回回执或本侧上下文改变 | 停止且保留冲突，不覆盖旧证据。 |

合法 final 在工作台产生 terminal_legal_final 后将 task 标为 completed 或 abstained；failed final 保留为 failed。普通已知工具失败仍作为原预算内反馈进入本侧历史。未知模型/公共动作会暂停阶段，不能借后续 task 绕过；resume 仅解除明确控制意图，不主动调模型，未知 call/pending action 阻止解除。失败/弃权保留其原 task 身份和全部已付费或合成测试证据。

## 验证结果和冻结身份

作者定向命令为 `.venv/Scripts/python.exe -m pytest experiment_traces/meta_ashare_revision15/tests/test_meta_casebank_campaign_v15.py experiment_traces/meta_ashare_revision15/tests/test_meta_casebank_live_v15.py -q --maxfail=3`，单进程 PYTHONPATH 指向 v15/src。结果 **15 passed in 221.57s**，没有重跑无关全项目套件。

覆盖 1 与 8 个固定任务、不同侧上下文隔离、完整多轮响应/工具历史、真实纯核/Store、17 次调用/16 query/12 candidate/1 final、重复候选子尝试、失败页、阶段共享预算不足、同槽及跨连接 lease、重开保留未知 reserve、模型完成边界暂停、动作 intent 与 Workbench 前后崩溃、saved-only 零 execute、缺 I/O、完整回执与保存输出冲突、超长合法形状 final 收费终态。复用的 fake process 真实生成 Gateway 所需事件和文件，再由实际验证器读取；没有只返回手工拼好的“成功 receipt”绕过证据链。

算术 fixture 是独立公开测试中的 16 个合成 weekdays × 4 中性符号，无私有生成机制或真实市场。1–8 任务扩展仅复制这份公开工程算术输入到测试临时目录，不重新生成原四题、不换 seed，也不开展任何参考发现。fake provider 使用的 10 input/3 output 等用量仅是测试数字，`model_verified=false` 保留；它们不是实际模型账单。

`experiment_traces/meta_ashare_revision15/casebank_engineering_attempts/author_001/receipt.json` SHA 为 `cbad490d188871a7e543c9c21d6932874bb505d84d67a8f1b8ed2c0808b406ed`。该作者记录明确来自已完成 exec 的原输出，未另行捕获独立 pytest log；后续独立审查将保存其自己的原日志，不能把此记录冒称为全项目正式验收。

独立审查已另外完成 **6 passed in 20.05s**，没有重复作者 15 项：跨进程 lease 与孤立 reserve、阶段余额恰等一次 reserve、已有 query 后完整上下文超过预算仍不截断、模型期间语义 state 变化不能作为更新时间排除、较低最终用量不能覆盖已保存较高 callback、关闭重开后 condition 完成只作 saved-only，Gateway/execute/两核均设置禁止陷阱。`casebank_engineering_attempts/independent_001/receipt.json` SHA 为 `cc2cd378f519552793652a16746635636f739b3b33e18be6040f003993532847`，含原 pytest log/JUnit，前后源码与 helper hash 一致。独立测试 `test_meta_casebank_live_independent_v15.py` SHA 为 `4d354b9e506f36a958357ca7ffa3c5e92b9734033dc9fd01ad8b6afab207b56f`。本作者只读核对该回执；没有将 21 项伪称为一次全量运行或独立研究样本。

| 本作者文件 | SHA256 |
| --- | --- |
| meta/casebank_campaign.py | `4e56a278715b672d422c4aebe7468217c58920640c8f0b820efcb11c84e5d02a` |
| meta/casebank_live.py | `8bd36896ed1445cae29addf3f7decabad05c171367ee60240c4f9a0603074a60` |
| test_meta_casebank_campaign_v15.py | `fb31f80d920a923ceb45ba0a546d1914073c0d95d242be05d4155928eee67086` |
| test_meta_casebank_live_v15.py | `8749f335cd69eb839a41e7d01c084fa68badcad3ec57dd9352d7cd52a4794fe2` |

## 两问、未完成门和停止条件

**这一步做得怎么样？** 原来只有工作台工具接口，现在已有独立协议的持久主循环，并沿用经核验的模型事件和原子名义预留路径；旧两诊断协议没有被换名套用。完整上下文、三种回执/动作崩溃边界、未知停止和最长机会链有离线反例。保守代价是未执行的已保存 final 也停在原边界，需要明确后续人工决策；频繁验证完整证据的 17 call 路径约占专组主要时间，尚未做性能优化。

**下一步该做什么，如何改进？** 独立进程锁、边界 reserve、语义 state 和跨重开 saved-only 复验已通过，下一步由 root 做受影响联合验收。之后如需真实研究，应另行授权、冻结具体 task 顺序和阶段预算、提供允许的显式 gateway，并保留所有失败/弃权与实际暴露。当前 baseline/candidate 是隔离身份和相同公开合同；这份串行主研究循环本身没有证明候选架构内部协作更好，也没有执行 P4 或同预算参考发现。

本轮新增真实项目 CodexGateway 研究调用 0、新策略回测 0、真实行情读取 0。工程/审查模型用量非零，不能推广为整个接手期间或全项目零调用。source hash 只认证这组冻结依赖内容，不证明真实到达时间、供应商请求幂等、操作系统隔离或绝对费用上限。execution_valid、hidden_control_qualified、architecture_comparison_completed 均未成立。

若独立反例显示旧协议/旧账本被采用、当前未知可换 task 再派、共享预算被复制成每任务整份、完整上下文被删或混入另一侧、final 免费更正、同槽重算、恢复调用 Gateway/execute/核、保存事件与返回 dict 不一致仍结算、语义变化被误排除为时间戳，或冻结运行被替换/补跑，则停止扩大并撤销对应边界结论。不得通过改旧回答、删失败、改四题原包、缩小分母或调整已见结果的预算来通过。
