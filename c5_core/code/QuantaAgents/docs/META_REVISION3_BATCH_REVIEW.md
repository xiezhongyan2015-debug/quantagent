# 第三版批次安全修正与边界

本次只修改 `experiment_traces/meta_ashare_revision3` 隔离副本。运行中的 8769 服务、`dev_20260906_01` 计划与主源码未更改。测试只使用生成的数据、内存 HTTP 夹具或本地 HTTP 加假 gateway，没有真实模型和真实行情研究调用。

## 数据身份绑定

旧批次仅在 POST 创建后轮询 `_verify_run`。POST 会立即启动 worker，因此事后发现数据不一致，不能保证首个模型调用尚未发生。仅修改 dispatcher 无法关闭这个时间窗口。

本版与运行器协作使用以下明确协议：

1. 服务健康接口必须声明 `data_snapshot_gate_version=1`；缺少能力时批次禁止派发。
2. 计划从同一账本中已完成的源运行冻结三值 `expected_data_snapshot={snapshot_id,development_datahash,loaded_through}`。每一成员的 POST 都携带同样三值，服务冻结在 `config.expected_data_snapshot`。计划版本改为 2，不为无门的旧计划静默补写字段。
3. 运行器在创建运行文件及启动 worker 前核对 `case.manifest().datahash == snapshot_id`。这部分检查只需要元数据。
4. 开发数据实际加载后、起点回测与首次模型调用前，运行器核对开发投影内容哈希与截止日，写入顶层 `data_snapshot_gate`。通过时必须包括三值、`status='passed'`、`stage='prepare_before_model'` 和检查时间。恢复实例会重新加载并复核；模型入口也拒绝未通过的门。
5. 批次持续核对源运行身份、成员清单身份、成员冻结期望、门证据和 `steps.prepare` 的实际证据。准备未完成且没有模型调用时允许 pending；存在调用、准备结果或 completed 状态却没有 passed 门时，锁定为 `frozen_mismatch`，后续成员不能派发。

源运行 `meta-20260906-001003-9b47f3` 已从 `meta_ashare_v2/ledger.sqlite3` 以只读方式核对：

| 字段 | 已记录值 |
|---|---|
| `config.case.datahash` / `snapshot_id` | `dac2319409e2b2860014e773a091a5955837b7483e45ae09177e5e1db994146f` |
| `steps.prepare.packet.datahash` / `development_datahash` | `0e9a0455bd8019859baed6037d374e67515f143fceba4faf0cea6bfe7e7fd6e1` |
| `loaded_through` | `2021-12-31` |

起点 evidence 的 `snapshot_id`、`datahash`、`loaded_through` 与上述记录一致。可用 `create_plan(..., expected_source_snapshot_id=...)` 或计划命令的 `--expected-source-snapshot-id` 再固定操作者预期，避免误选另一份自洽的源数据。

清单哈希和开发内容哈希是两个不同标识。前者使用文件大小/修改时间清单以及成分、日历内容哈希；后者在限定读取时间后包含投影行情内容。因此只有清单一致不足以代替加载后检查。此门保证研究看到的约定数据身份一致，不证明价格调整、单位、成交模拟等经济含义已经正确，也不证明收益有效。

## 原子写入与恢复

`state.json`、`report.json`、`report.md` 都通过同一原子写入边界保存：独有临时文件 → flush/fsync → `os.replace`。没有退回直接截断目标文件。

- 仅对本地文件操作的 `PermissionError` 进行最多 5 次尝试，等待间隔为 0.05、0.1、0.2、0.4 秒。单个操作等待合计 0.75 秒；不同写入、替换、清理操作分别受同一有限上限约束。
- 磁盘满等其他 `OSError` 不重试。最终失败使用 `BatchPersistenceError`，保留目标文件和错误原因；清理只处理本次私有临时文件。临时文件也被永久占用时，错误明确保留其路径。
- 写入永久失败不再被误标为“只读 API 暂不可用”，不会在异常处理里再次保存并形成重复恢复。上层可以停止或显式重新读取原账本，但不得重放创建请求。
- 创建意图依旧先落盘、再执行唯一一次 POST。若 report 在意图落盘后、POST 前永久失败，下次读取会保守保留“结果待核对”，即使这可能搁置一次实际尚未发送的成员。若在创建回执后 report 保存失败，已落盘的 `run_id` 可直接用于 GET 对账。两种路径均不重复 POST。

## attention 的可恢复边界

`run()` 遇到 attention 会结束当前自动派发循环。状态报告新增 `attention_recovery`，不把所有 attention 都解释为可以重试付费操作。

| 情形 | 允许的后续行为 |
|---|---|
| 创建回执丢失、已记录创建意图但尚无 run ID | 显式 tick 仅以 request_key 做 GET 对账。一直找不到时继续停止；不能再次 POST。 |
| 已知运行的未知调用或未结清用量 | 显式 tick 读取原运行。只有权威账本补齐后才可解除等待；dispatcher 不发送 retry/resume。 |
| 无已付费意图的只读预检失败 | 显式 tick 可重新预检；重新核对全部冻结条件后才可能派发首次请求。 |
| 成员暂停 | 仅监控；恢复要通过原运行已有控制完成，dispatcher 不代为恢复。 |
| 数据/源码/指导不一致、留出期被打开 | `frozen_mismatch` 且 `locked_stop=true`，恢复字段后也不会自动重新开闸。保留这批证据，另行审查。 |
| 取消、事故上限、取消回执未知 | 保留锁定停止。后续只收集已有运行的最终用量，不启动后续成员。 |
| 永久文件写入失败 | 抛出明确文件错误；有限重试已耗尽，不转换成 API 重试。 |

attention 的解除依赖可验证的外部账本变化或成功预检，不依赖经过了多长时间。全部计划成员仍在分母中；不生成晋升或正式目标达标结论。

## 验证和当前限制

针对性验证覆盖：瞬时 report 占用保持旧 JSON 完整；永久占用限定尝试次数并清理临时文件；磁盘满不重试；保存故障发生在创建意图/回执前后仍只派发一次；源及后续成员的清单/投影哈希错配；能力缺失；pending 门后擅自调用；未知用量仅靠旧账本恢复。

最终合并验证：`test_meta_batch.py`、`test_meta_batch_data_gate.py`、运行器配套的 `test_meta_data_snapshot_gate.py` 共 **69 项通过（13.43 秒）**。测试进程明确以本隔离副本 `src` 为导入路径。

追加独立审查覆盖 `data_identity.py`、`runtime.py`、`server.py`、`ashare_research.py`。标准图执行路径在跳过 prepare 检查点时仍重核当前实例；请求幂等哈希包含数据三值；清单身份和实际开发投影哈希没有混用。审查发现私有 `_model` 入口原来只信任已保存 passed 门，直接绕开 `_step` 时不能绑定当前实例。运行器负责人已增加“先确认已通过 → 确认当前实例存在 → 重核该实例当前 packet → 再确认通过”的顺序，避免新建 pending 状态因直接调用而被自动提升。

另加一项恢复反例：首个假模型回执已落盘、研究 record 尚未形成时暂停；重启后保持相同清单、改变实际内容哈希。恢复保留唯一旧调用，研究记录仍空，门转为 failed，第二 gateway 零调用。该反例与既有 HTTP/运行器门测试合跑 **17 项通过（9.09 秒）**。后续入口加固与 raw ledger 的最终联合回归由主任务统一执行，不把较早测试结果冒充最终整版回归。

另外使用真实本地 HTTP 服务、假市场和假 gateway 验证两种时点：清单错配在创建成员前拒绝，投影内容错配在准备阶段拒绝；两者均为零 gateway 调用、零调用账本，并且 dispatcher 没有再次 POST。

批次只负责要求并核查门协议；准确的零付费阻断依赖运行器和模型入口共同执行该协议，不能从单纯轮询推导出来。服务仍必须通过持有 `StateLock` 的标准入口启动；dispatcher 只访问 HTTP，不另建 Engine。

## 两问自检

**这一步做得怎么样？** 已关闭“不同数据继续研究”的受控调用路径时间窗口，并把临时文件占用恢复限制在本地文件边界。出现永久错误时宁可留下需要核对的创建意图，也不以重复付费换取自动恢复。

**下一步该做什么、如何改进？** 本批运行结束后合并第三版，重启服务并核对新的 source hash 和 gate version；使用新的计划目录冻结原始三值，再执行假数据联调。若新研究需要升级数据转换或执行规则，应声明一份新数据协议并重跑所有比较成员，不放宽旧计划的数据门。实际收益判断继续依赖执行审计和冻结样本外验证。
