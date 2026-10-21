# v14 CasebankWorkbench 公共接线交接 001

本增量完成了原四个 exposed synthetic 普通包到持久工作台的离线调用链：公开动作 schema → 公共输入加载器 → 期限/匹配纯核 → 独立 Store → 完整产物和公开摘要 → 本侧下一轮历史 → 唯一合法 final。有效 final 仅指结构、作用域和证据引用合法；没有评定答案正确、发现策略或实现净收益。当前源码供独立审查，尚不是 root 的 v14 联合封板。

## 范围与身份

从已通过 115 项联合验证的 v13 复制 src/tests/scripts 共 228 文件至新 v14；未复制缓存、验证、fixture、campaign 或运行数据。复制清单 `experiment_traces/meta_ashare_revision14/copy_source_manifest.json` 的 SHA256 为 `c34324a5a0d51bf1fc40026a25bbd8c574b824247b2f53654f53f740b9ec0d31`。父验收 receipt 为 `experiment_traces/meta_ashare_revision13/validation_attempts/20260906T190816076109Z/receipt.json`，SHA256 `3ae19ceb797f90e0669429c356f9ae9f4a0dd1fe39a733fed073f7f4c5b518d7`。

本作者仅新增 v14 `src/quanta_agents/meta/casebank_workbench.py`、`tests/test_meta_casebank_workbench_v14.py` 和本文；Store、horizon_diagnostics、event_conditioning、公共加载器、main、v13 及旧付费证据未改。当前未接 live harness，没有创建 live campaign、打开旧 Engine 或研究网关。本子任务新增项目 CodexGateway 调用 0、新策略回测 0、独立研究样本 0、真实行情读取 0。工程/审查模型用量非零，与全项目其他已授权调用分别记账。

原公开包唯一位置为 `experiment_traces/meta_casebank_development_v13/attempts/20260906T184928Z`；公开 manifest SHA256 为 `e728fa67c052d8af1f4133a5f9cc29dfe9b18772dbd4f244d088d0b08396b797`。本工作台仅通过 `casebank_public.load_public_case` 取得 packet、CSV 和 calendar，不导入生成器、私有参考答案或种子。原包完全保留；新增规则作为 `casebank_workbench_v1` executable contract overlay 与原包、源码及本侧身份共同冻结，不能倒称它是原 v13 包本来已经提供的可执行接口。

## 可用接口

```python
from quanta_agents.meta.casebank_workbench import CasebankWorkbench, WorkbenchBlocked

workbench = CasebankWorkbench.create(
    new_root, package_root=public_package_root, case_id="case_01",
    expected_public_manifest_sha256=public_manifest_sha256,
    run_id="public_fixture", arm="baseline",  # 或 candidate
    controller_cursor_key=controller_owned_bytes,
)
schema = workbench.action_schema()
prompt = workbench.prompt()
observation = workbench.execute(1, action)
audit = workbench.audit()
export = workbench.export_for_audit()
workbench.close()

restored = CasebankWorkbench.load(
    new_root, expected_scope_hash=saved_scope_hash,
    controller_cursor_key=the_same_controller_owned_bytes,
)
same_observation = restored.recover_saved(1)
```

`create` 只接受新目录，重复创建拒绝；`load` 只打开该新工作台自身已存在的 ledger。每实例一 case、一 arm，不是 project-wide 或账户级预算控制器。key 必须是 32–128 bytes，控制器自行保存；公开结果、prompt 和日志不返回 key。持久冻结只保存它的 SHA，不是恢复密钥。

scope_hash 包含原始普通包全文、CSV、sessions、公开 manifest 身份、run、arm、overlay、五个运行模块的 SHA 和 key fingerprint。重建时核对冻结 scope、key、源码与保存字节。两侧用同一原包时，普通资料、规则和工具预算相同；作用域、证据 ID、游标及本侧历史有意隔离。本工作台没有安排两个角色的模型或 token 预算，不能由此宣称 P4 完整公平比较已经完成。

每动作外层必须恰有 `{action, queries, candidates, reports}` 四字段。对应数组恰一条，其他数组为空。slot 从 1 连续增加，最多 17；同槽只能重放完全相同普通 JSON。请求、当时 schema、预算登记、阶段与错误保存在真实 Store。

| action | 唯一 payload | 预算和结果 |
| --- | --- | --- |
| inspect_inputs | queries: `{table, evidence_id, cursor, limit}` | 1 query；每页 1–50 行。inputs 首页 evidence_id/cursor 均为空；诊断表引用此前已完成诊断的 evidence_id，table 须属于该结果的 available_tables。后续游标保持同源和同表。 |
| diagnose_horizons | candidates: `{conditions, signal_block, horizons}` | 1 query、1 candidate、3 个 requested horizon；selected、benchmark、common increment 共 9 个输出子项。 |
| condition_events | 同上 | 1 query、1 candidate、3 个 requested horizon；selected horizon kernel 3 子项，加全部声明组 × signal_events/matched_pairs × 3；单条件 15、双条件 27 子项，含空组。 |
| submit_research_report | reports: 下述完整 final | 唯一 final attempt，不占 query/candidate；合法或非法 final 均结束工作台。 |

候选条件为一或两个不同字段 `state_01` 至 `state_04`，每项 `{field, value}`，value 是整数 -1 或 1；多个条件作 AND，固定 horizons 恰为 `[1,5,10]`，禁止连续参数扫描。signal_block 为 all/A/B，仅限制信号日期，条件和块一起构成规格；更换块也是新候选槽。每个新槽的重复/失败仍占本轮 query、candidate 和 requested horizon；完整成功/已知失败可引用原证据，实际核调用为 0。它们不变成新的独立统计样本。

全部普通 action 都受公开字节、行数、数组和字段边界约束。16 query 或 12 candidate 耗尽后 schema 收缩至仍可用动作；超预算动作仍保存拒绝规格并关闭工作台，不提供免费修正。无效有限 JSON 动作在登记后写已知失败；不可序列化、超字节上限等入口边界拒绝不计算。未知动作槽始终阻止新槽。

## 唯一 final 的公开合同

reports 唯一对象的全部字段均为必填，禁止额外字段：

```json
{
  "case_id": "case_01",
  "scope_hash": "从本工作台公开结果原样引用",
  "conclusion": "abstain",
  "summary": "有界的普通文字结论",
  "claims": [],
  "limitations": [],
  "next_check": "一个明确的后续核验"
}
```

conclusion 允许 supported、mixed、not_supported、inconclusive、abstain。supported/mixed 至少一 claim；每 claim 为 `{statement, evidence_ids}`，statement 非空，evidence_ids 为 1–16 个不同的本侧已结算 query 证据 ID。失败 query 证据可用于局限或弃权；引用仅证明证据存在，不给陈述自动背书。summary 和 next_check 非空，分别最多 8,000 和 4,000 字符；最多 12 claims、12 limitations，每条 statement/limitation 最多 4,000 字符。not_supported/inconclusive/abstain 允许空 claims 和空 limitations，亦允许零查询直接弃权。没有隐藏单位词表、quantity 登记表、私有参考答案格式或强制获胜结论。

同 case/arm 外的证据、错误 scope、空关键文本、多 payload、或不符合公开 schema 的 final 都保留为 failed 并终止；没有自动第二次模型请求或免费修正。有效不同解释可以提交，工作台返回 `quality_assessment=not_performed`，不输出机械正确即策略成功的评分。

## 数值、分母与时间边界

期限核维持既有信号决策后、次交易 session 开盘入场、h+1 session 开盘出场。所有三期限来自同一共同覆盖事件集。完整 artifact 保存所有候选事件、各 h 已知数值、未覆盖原因及共同分母；公开摘要不截掉失败/空样本，完整表可受控分页。

diagnose_horizons 的基准是同信号日期、整个 eligible 股票集、相同入出场端点的等权毛回报。比较共同样本要求选中事件和该日整个 eligible 基准同时覆盖全部三期限。先按选中事件配对，再分别报告事件等权和日期等权均值；同日基准按每个选中事件重复用于配对，而不是混用两个不同样本均值。它没有现金/仓位、资金容量、成交、换手费用或净 PnL。packet 的成本假设原样公开，`costs_applied=false`。

condition_events 在本地独占保存 decision-only plan 并登记预期 SHA 后，才开始从已载入的公开 opening frame 计算端点。声明条件字段的所有二元组合均生成组，空组也保留；match_features 为空，按同日符号顺序选首次未使用的非信号 eligible 控制，不作因果匹配断言。控制在长 h 缺结果时不换控制；共同样本保留原配对与排除原因。每个子项登记原槽 ID、实际 computed/empty/failed 状态、核子项 ID 和结果行 hash；复用另有 reused 状态及原槽引用。

signal_block 仅选择信号日期，端点允许跨入下一块。摘要逐 h 报跨块窗口，尾部缺覆盖保留。24 日 toy 的 A 块在 h1/h5/h10 分别有 4/12/22 个跨块选中窗口，B 块 24 个候选中仅 2 个满足共同覆盖，22 个仍在完整表。不能将这些数字描述为块内可实现组合表现。

公开历史 CSV 在创建工作台时已完整读取，因此 plan 先保存仅是本适配器的本地执行顺序，不是 preparer 或研究角色此前未看结果的证明。state_available_at 仅作为普通包声明的 observation 上界，拒绝晚于 decision 的字段；这不是历史实际来源到达认证。`historical_PIT_verified=false`、`chronological_commitment_verified=false`。纯 hash 不提供真实时间戳证明。

## 持久化和恢复

Store 既有原子 update 先登记原动作、计数和全部子项，才计算。完整文件的预期 SHA/字节数先入 ledger，再以独占创建、flush/fsync 写 plan/bundle/receipt。公开 observation 从原保存 bundle 投影，绑定 scope、slot、request、artifact 和本轮 accounting；HMAC 游标绑定 scope/table/source evidence/offset，不能跨 case、arm、表或观测使用。

| 边界 | 行为 |
| --- | --- |
| receipt 与全部注册文件完整 | saved-only 核对字节及绑定后结算/返回原 observation；恢复不调用任一个核，不增加机会。 |
| 仅意图、plan、计算启动或 bundle，缺 receipt | 保留未知，禁止恢复重算及新槽；现阶段无人工造 receipt 或重新研究入口。 |
| 文件缺失、部分写入、内容改变 | 阻断且不覆盖；audit export 给出 unavailable 项。 |
| 同槽 action、源码、scope、key 或公共摘要变化 | 拒绝，不借新槽重派。 |
| 新槽相同请求 | 占用新预算和新子项 ID，引用原 bundle/失败；不再运行核。 |

这里只有一个工作台的原子槽控制，不是 OS 沙箱、外部进程防护、账户费用硬帽或工程累计用量控制器。具有同用户任意 Python/文件权限的进程不在隔离承诺内。后续可信 harness 还须把模型 call_id、动作 hash 和 Workbench 前状态绑定，并使用既有唯一派发与未知回执停止规则；当前没有声称已接通该付费链。

## 定向验证与可审源码

最终作者专组：项目 `.venv/Scripts/python.exe -m pytest experiment_traces/meta_ashare_revision14/tests/test_meta_casebank_workbench_v14.py -q`，该进程的 PYTHONPATH 仅指向 v14/src，结果 **25 passed in 55.49s**。未执行全量验收。首轮同组 25 passed in 53.31s，随后只收口公开失败规则与子项实际结果映射并重跑同组；两次数量不相加。

证据包括：公开 schema 校验 → fake action → 实际纯核/Store → 下一轮历史 → 合法 final；独立 Decimal 24 日四符号等差路径的三期限配对期望（不调用生产诊断来产生 expected）；16/12/1 边界；新槽重复计费和真正恢复无核调用；已知失败复用；意图、plan、kernel 和 receipt 后崩溃；同槽竞争只允许一次核启动；plan 已在核开始前保存且注册 SHA；缺失/篡改文件不可自封为新证据；输入/诊断分页、跨侧游标和证据拒绝；非法 final 保留停止、合法不同结论及早期弃权。

toy 仅复用公开模板，按测试中公开的固定算术规则构造 24 sessions × 4 中性符号，共 96 行；不是重新生成四个原包、隐藏新题或参考发现。四个实际原包每个仅创建工作台并 inspect 第一行，验证普通 loader 和 schema 可用，候选计算、参考求解均为 0。测试禁止网络与子进程，不读取 evaluator_private。没有从测试算术收益择优构造研究赢家。

| 本作者文件 | SHA256 |
| --- | --- |
| v14 meta/casebank_workbench.py | `f16bd56fb15e6f103cea59a780f2cf2857509e97762095a7793895bf723c616e` |
| v14 tests/test_meta_casebank_workbench_v14.py | `086af15b914a9de2cd0c03d04e331d8193f61ce03d9b612b12abca6a36401716` |

## 两问、下一步与停止条件

**这一步做得怎么样？** 原来四个普通包只有动作名称而没有可执行适配器，现在可从公共动作走到持久证据、分页、本侧历史和充分公布的唯一 final。共同三期限、分块端点、子项/失败/重复预算和 saved-only 恢复有可触发风险的反例。代价是任何未知槽保守停止；尚无付费 harness、工具费用统一整合、实际大包候选延迟验证或独立同预算发现结果。机械合法不能填补这些门。

**下一步该做什么，如何改进？** 先由另一 Astra/xhigh 审查者用独立反例检查预算、决策顺序、共同样本、跨块端点和公开合同，保留失败后做最小修复，再由 root 做受影响联合验收。之后才讨论把同一冻结工作台接入可信 harness；同预算参考发现、隐藏正控资格、P4 架构比较、真实 PIT/执行认证均未完成，也不在本次授权内。

若独立反例出现同槽重算、未知槽允许新动作、重复/失败/空组漏记、plan 尚未持久化已由本适配器派生结果、块尾/跨块事件被删、两侧公开规则不同、证据或游标跨侧接受、普通 prompt 包含私有机制/种子/参考、合法不同 final 被未公开规则拒绝，或毛事件统计被标为真实净 PnL，则撤销对应通过结论并停止扩大。不要改原包或旧付费回答、重种生成、缩小分母、删除失败来通过验收。
