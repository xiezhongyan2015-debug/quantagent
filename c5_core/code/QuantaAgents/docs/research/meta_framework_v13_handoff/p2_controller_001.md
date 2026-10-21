# v13 条件分组与匹配的持久控制器 001

2026-09-07，Asia/Hong_Kong，Astra/xhigh 工程实现与定向检查。**已把 v12 纯匹配核接到一个新的、可持久恢复的合成开发控制器：完整决策计划先落盘，控制器随后才调用结果读取器。** 本控制器及专组源码已冻结，28 项定向检查通过，等待 root 的独立审查和联合验收。它没有接入 live harness、现有 campaign 或通用 `ashare_research` 模型循环；不应把本次工具可调用性写成模型已经使用了匹配工具。

依据：[checkpoint 008](../meta_framework_v8_handoff/checkpoint_20260907_008.json)、完整[实施提示词](../../META_FRAMEWORK_IMPLEMENTATION_PROMPT.md)、v12 [独立 P2 审查](../meta_framework_v12_handoff/p2_review_001.md)、[期限接线](../meta_framework_v12_handoff/horizon_wiring_001.md)和[匹配核说明](../meta_framework_v12_handoff/event_conditioning_001.md)。提示词要求的真实时间可得性、所有内部方案/失败记账、两个架构的共同工具和恢复不新增研究机会，分别落实为以下边界；真实市场数据到达认证仍未实现。

## 文件与隔离

独占建立 `experiment_traces/meta_ashare_revision13`，只从 v12 复制 `src/tests/scripts` 的 216 个文件，排除全部缓存和运行、fixture、validation、live 数据。逐文件来源保存在 [copy_source_manifest.json](../../../experiment_traces/meta_ashare_revision13/copy_source_manifest.json)，SHA256 `87187275720e5c0ee36bebca8819dc492ccdde3af59069cd88249a397f33165f`。完成时重新核对这 216 个父文件，v12 全部字节未变；v13 复制文件仅 root 同时负责的 `diagnostic_monitor.py` 有另行授权修改。本子任务没有修改该文件。

本子任务只新增：

- [conditioning_controller.py](../../../experiment_traces/meta_ashare_revision13/src/quanta_agents/meta/conditioning_controller.py)：控制器、公开动作合同、结果来源冻结、预算与恢复。
- [test_meta_conditioning_controller_v13.py](../../../experiment_traces/meta_ashare_revision13/tests/test_meta_conditioning_controller_v13.py)：独立临时目录、真实 Store 和已暴露合成小路径。
- 本说明及上述复制来源清单。

`event_conditioning.py`、`runtime.py`、`store.py` 保持 v12 字节。没有打开旧 Engine/账本、修改既有付费回执、访问 root 正在执行的 v12 校准，或启动任何 campaign。本实施子任务新增项目 CodexGateway 调用 0、新组合回测 0、真实行情读取 0、封存 2024–2025 行情读取 0。工程与审查使用的模型用量非零，项目 Gateway 账本不计量这些平台用量。

## 具体接口

```python
ConditioningController.create(root, *, frozen_policy, decision_inputs_by_arm)
ConditioningController.load(root, *, expected_policy_hash)
controller.public_contract(arm)
controller.execute(arm, slot, action, *, outcome_reader)
controller.recover_saved(arm, slot)
controller.public_history(arm)
controller.summary()
controller.export_for_audit()
controller.close()
content_hash(value)
outcome_values_hash(payload)
```

`create` 只接受不存在的新目录；失败不会改为复用旧目录。`load` 要求该目录已有 `conditioning.sqlite3` 和相同冻结 hash；不调和状态、不派发，也不创建 Engine。源码导入没有创建控制器或文件的顶层动作。`expected_policy_hash` 实际绑定整个冻结包，包含政策、两侧决策输入和 controller/kernel/store 三份源码的 SHA，不只是预算字典。

`frozen_policy` 精确字段如下，示例只描述合同，不是新的运行计划：

```python
{
    "model": "gpt-6-astra", "effort": "xhigh",
    "arms": ["baseline", "candidate"],
    "max_requests_per_arm": 4,               # 接口允许 1..16；创建后不可变
    "max_sub_attempt_slots_per_arm": 384,   # 接口允许 1..1536；创建后不可变
    "outcome_sources_by_arm": {
        "baseline": {"source_id": "exposed_baseline", "content_sha256": "<完整包规范hash>"},
        "candidate": {"source_id": "exposed_candidate", "content_sha256": "<完整包规范hash>"}
    },
    "common_outcome_values_hash": "<两侧共同端点内容规范hash>"
}
```

两侧 `decision_inputs_by_arm[arm]` 均仅包含 `identity/decision_rows/feature_manifest`。identity 沿用纯核的完整合同，仅允许显式暴露的 synthetic development；两个包除 `architecture/observation_id` 外必须完全相同。创建过程不接收 outcome 包或路径，也不读取结果。特征时点等语义错误由后续原计划构造核判定，使失败请求仍保留自己的槽和原规格。

公开 `action` 只有：

```json
{"action":"condition_horizons","specification":{
  "group_features":["<公开已声明离散特征>"],
  "match_features":["<公开已声明离散特征>"],
  "pairing":"same_day_exact_without_replacement",
  "horizons":[1,5,10]
}}
```

`public_contract` 返回两侧相同的 schema、各自普通决策输入、相同预算和规则，不包含私有参考解、结果数值、其他架构历史、任意代码或文件入口。schema 使用既有小型 runtime validator 支持的 type/enum 词汇；固定期限以完整数组枚举声明，不暗中忽略一个可变期限。slot、身份、结果读取器和预算由可信调用者提供，不由 action 改写。`public_history(arm)` 每次先核对该侧完整保存证据与摘要，然后仅返回该侧历史。

## 计划、结果读取与持久状态

每一新槽先通过真实 Store 的 `BEGIN IMMEDIATE` 登记原 action、冻结 binding、方案数 1、全部子项及预算。相同槽不能换请求；已存在的相同请求只走保存恢复。任何未结清槽会挡住该控制器两侧的新槽，避免未知状态被下一次研究跳过。预算拒绝保留原 action 和拒绝理由，不调用工具或结果读取器。

正常顺序为：

1. 原子保存 `request_committed` 和预算子项。
2. 只用冻结决策包构造计划，包含全部声明组、原决策、同日候选比较、选择/排除和配对。
3. 先把计划文件的预期字节 SHA 注册到 Store，再独占创建文件、flush/fsync，保存 `plan_committed`。已存在文件不覆盖。
4. 重新核对冻结源、输入和计划文件，登记 `outcome_read_started`，才调用可信 `outcome_reader`。
5. 原始 outcome 包先保存为独立完整产物，再核对冻结源包 SHA、共同数值承诺；合格后调用原匹配汇总核。
6. 保存完整 result 和完成/失败 receipt，核对这些原文件后结算同一槽，导出有界公共摘要。

`outcome_reader` 收到 `{source, identity, commitment}`；commitment 包含计划 hash、计划文件身份及本控制器 binding。它须返回 `{horizon_report, control_outcomes}`。接口没有市场加载器、模型调用或回测入口，当前测试用预先存在的公开合成包作为 reader 数据。准备这个包的 fixture 作者已经看过数据，不能据本控制器的程序顺序称其为新盲题。

完整 outcome/result 也遵循“预期 SHA 先登记、再独占写文件”。因此即使完整文件尚未结算，恢复仍与原注册的预期字节核对，不能接受篡改后自行重封的内容 hash。公共输出由保存的 result 投影产生，保留完整产物身份、分母、曲线、局限和预算，最大 65,536 UTF-8 字节；完整事件、候选比较与逐日簇保存在原文件和 audit 导出中。

已知纯核输入/方案错误写失败 receipt，保留原规格及已有计划/outcome；新轮重复同一失败可复用其失败证据。未知 reader/系统异常保留阶段和已知错误，不能变成有用量为零的成功，也不会自动重试。尚无 receipt 的完整 result 仍然是未结清证据，恢复不会替它重新跑汇总来制造完成记录。

## 共同环境、PIT 与承诺的精确含义

两侧独立源包 hash 本身不足以证明数值环境相同。此次另冻结 `common_outcome_values_hash`，明确投影为固定 horizons、完整 signal event rows、control sessions/rows。投影保留端点、日期、状态、排除等全部行内容，省略顶层 arm/observation identity 及报告级 hash/摘要；汇总核从这些保存行重新验证端点并构造匹配结果。

创建时仅保存 preparer 的共同内容声明。每侧在计划持久化、结果读取和原包保存之后才计算并核对该共同 hash。两侧均实际核对后 `both_arms_common_outcome_values_verified=true`；未读取或有差异时仍为 false。它证明本地两个包的这份规范内容一致，不能证明市场真实性、字段到达时间或整个 P4 实验所有成本环境相同。两侧身份及原始完整包仍分别保留。

原核继续拒绝：同日不同决策瞬间、分类晚到、特征 observed_at/available_at 晚于决策、未知离散字段、结果字段与扫描参数。同日候选只从当时声明 eligible 且不是信号的事件选择；已配对控制缺 h10 时，原配对从全部三个期限共同集合排除，保留已知短期差值，不能读到结果后换控制。

原核的 `chronological_commitment_verified=false` 不改写。公共控制器证明只另列 `local_adapter_plan_saved_before_reader`：本次适配器确实先保存计划、后开始 reader。`historical_PIT_verified` 始终 false。新规格若在本侧已经读过结果后提出，`prior_outcome_exposure_in_arm=true`；新规格的局部保存顺序不能消除既有暴露。重复结果明确引用原槽。此适配器不是 OS 沙箱，不能保护任意 Python/文件权限的同用户进程，也不能认证 preparer、模型预训练或其他观察者此前没有接触结果。

## 预算与恢复

有效规格按全部声明 group×两种观察单位×固定三期限预登记，包括空组；最多 16×2×3=96 个子项。每个子项有 arm/slot 绑定 ID，并在结算后映射到原核子项 ID 和 computed/empty 状态。无效分组声明无法确定组数时，保守占用 96 个 `unresolved_reservation` 名义槽，不把它们称为已计算的 96 个结果，不退还预算。

新轮重复仍记方案请求 1 和本轮全部新子项 ID，实际 plan build/reader/summary 数为 0；原成功或失败产物继续复用。另一份规格占新的独立原槽并保留全部文件。每侧预算完全相同且独立，不冒称这里已经实现模型 token、供应商付费或全项目事故预算控制。

计数区分 `plan_builds_started`、`outcome_reads_started`、`summaries_started`、名义预留和参数方案请求。汇总核内部会确定性重建已冻结计划，其候选复核次数另列，研究机会新增为 0。纯核在抛错前完成了多少内部比较，若未返回完整计划则不能精确认证；保留原输入、规格、失败和 started 状态，不伪称该内部工作为零。

恢复没有 reader 参数，也不调用任一个匹配核：

| 保存边界 | 行为 |
| --- | --- |
| 完整 receipt 及其全部原文件存在 | 核对字节、binding、原结果与公共摘要，结算/复用同一槽；不增加任何机会。 |
| 仅请求、计划、outcomes 或 result，缺 receipt | 保留未知并阻断；不重算，不借新槽继续。 |
| 已注册文件缺失、部分文件或字节冲突 | 阻断且不覆盖；audit 返回缺失/损坏条目、预期与实际 SHA，保留可读证据。 |
| 同槽 action、冻结包、内存政策、源码或保存摘要改变 | 在后续读取/新动作前拒绝。 |

Store 的原子登记同时约束同一连接的线程及不同 Store 连接；同槽竞争的定向反例只让一方开始 reader。这里没有创建 project-wide campaign registry 或外部进程管理器，也不声称覆盖旧服务或账户外部调用。

## 实际验证与冻结身份

最终定向命令：项目 `.venv/Scripts/python.exe -m pytest experiment_traces/meta_ashare_revision13/tests/test_meta_conditioning_controller_v13.py -q`，该命令的 `PYTHONPATH` 仅指向 v13/src。结果 **28 passed in 7.11s**，未做全量验收。

主要证据包括：两侧 schema→dispatcher→真实纯核→真实 Store→本侧历史；独立 Decimal 配对数值；原计划确实在 reader 开始前存在且 SHA 已注册；新轮重复与真正重开不新增计算；四个不同崩溃边界；完整 receipt 后崩溃可结算；同槽线程/双连接竞争；计划/结果/receipt 自洽重封仍拒绝；部分文件不覆盖；预算耗尽保留拒绝规格；同日 PIT 错误和失败重复；两侧源包自洽但数值不同；内存政策和已完成摘要篡改；最大 16 组时 96 子项与 90 空子项保留且公共结果有界；缺 h10 控制不重配；新目录不能复用及旧 sentinel 账本不变。

首次专组出现 1 项 schema 兼容错误、23 项通过：原 schema 使用仅 const 字段，而现有 validator 要求 type。修为其支持的 type/enum 后中间 26 项通过；再针对明确的最大组输出和缺控制路径加入两项，最终如上。不能把这三次数量相加作为联合计数或三个独立研究样本。

| 文件 | SHA256 |
| --- | --- |
| v13 meta/conditioning_controller.py | `3d1d4b6de734ed672247dbf001a8dd94f646e12e4e28c246e2c0c2d7c1a326d9` |
| v13 tests/test_meta_conditioning_controller_v13.py | `127cb0d42fd999ee54bf5d395afa6d0c9fa11b7eb89665af4ee0662e41a49faa` |
| v13 meta/event_conditioning.py（继承，未改） | `f06a0038fe09cd7a5a31158a08100febc7d00e0a7f8401f8b01dcc60e07ebaef` |
| v13 meta/store.py（继承，未改） | `a722ca5256125f3eb10cedce51e3324f9eea8740672a6152597c795c3e2e9929` |

## 两问自检、下一步与推翻条件

**这一步做得怎么样？** 解决了“纯核返回一份自封 hash，却无法证明该适配器先保存计划再读取结果”的具体控制缺口；保存先后、全规格预算、共同结果内容、失败和 saved-only 恢复都有可触发风险的反例。没有把完整 result 无 receipt 的未知状态擅自完成。引入的保守代价是无效分组声明占满 96 个名义槽，且任何未知槽阻断两侧新动作；大输入的实际资源开销和其他进程任意改库/读文件的隔离仍未认证。

**下一步该做什么，如何改进？** root 应在这些源码冻结后安排独立审查和受影响联合验收，再决定是否把同一工具合同接进可信研究 harness。下一个最有信息价值的增量是验证公开动作—controller adapter—原研究槽预算/下一轮历史的完整调用链，先用相同 exposed fixture 和 fake 动作，不自动增加付费、真实市场或隐藏题范围。真实 PIT 需要独立来源到达证据与受限读取权限；纯内容 hash 不能填补该门。

若反例表明 outcome 在计划完成持久化前可被此适配器读到、两侧实际端点不同仍被认证一致、换方案/失败/空组漏记、复用借新研究机会、恢复再运行任一个核/reader、损坏文件被覆盖、其他架构历史进入普通输入，或任何 flags 被误解释为真实执行有效，即撤销对应通过结论并停止扩大。不得为通过改旧付费回答、旧收益、样本分母或真实市场范围。工具结果仍是相依事件的描述性毛差值，`execution_valid`、`formal_target_success` 和目标达成均未成立。
