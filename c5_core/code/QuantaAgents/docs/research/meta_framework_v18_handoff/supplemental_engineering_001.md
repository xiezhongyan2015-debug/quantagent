# 一次补充时间窗口：工程交接 001

2026-09-07，Astra/xhigh。本文只涉及工程；没有为实际 campaign 创建新 scope、许可或阶段，没有模型派发、实际工作台操作、市场数据或私有机制读取。采用 [事前设计](post_deadline_research_design_001.md) 与 root 的 [一次补充研究政策](supplementary_research_policy_001.json)；旧六小时到期、不完整的结果不得回写。

新增 `meta/casebank_supplemental.py` 与 `scripts/run_casebank_supplemental.py`，在 v18 的继承 `casebank_continuation.py` 中仅加入明确的资源/来源/审计 hooks。默认类仍核原 paid 权限和原时钟，未复制研究循环、费用计算、模型验证或原工作台业务核。v17 原源 `6cc0dc07cb60ac3f8f8b507b911c028a85a82305283132be4432a9b401b19b86` 及原入口 `718120916a846cfe7ff9955b326d6148303f9ce5486746887dcf819540e98c2b` 不改。

## 同域与历史边界

唯一新表 `casebank_supplemental_time_v18` 保存一次新时间许可及原 v17 完整暂停后态；`casebank_supplemental_state_audit_v18` 保存逐次完整 after_state、before/after hash 链。原 registry 文件锁覆盖派发，同一 `BEGIN IMMEDIATE` 核预算并写意图/预留/治理审计。当前 v17 进度行可依授权续写；其中 scope、scope_hash、旧 admission 原 bytes/hash 和 id 永不改。原 v15 run/call/plan、v16 correction、v17 已有 call/action 与事件前缀哈希保留。

首版资格严格限于已知起点：原四个 baseline、两个传输、v17 新调用 0、唯一保存 inspect 已 applied、query 1、候选/final 0、v17 paused。不能以本模块泛化恢复其他未知中断。原 11,235 已知与 80,000 billing-unknown 仍计 exposure 91,235；下一次预留后为 171,235。首题 2/17、全域 2/68；所有后续传输、16/12/1 工具机会和 600,000/2,400,000 名义额度沿用原计数，未知不记零。没有新的独立样本、seed 或 task。

新窗口需独立 scope/admission 绑定绝对 start/end，最多 21,600 秒、最多一份登记；关闭后不可换 id 或再建第三窗口。所有暂停和等待消耗该窗口。旧 `real_dispatch_authorized=false` 仍是原历史许可，新调用 request identity 独立携带新 phase、scope、source、admission 及准入时间，不把旧 false 改为 true。源漂移、窗口不符、预算不足、新 unknown 或已用 semantic round 均阻止下一模型请求。

原 v17 `allow_saved_application_after_deadline=true` 不沿用到新阶段。新增 scope 必须另明确同名布尔值。若明确允许正常在途回答在新窗截止后做本地收尾，动作必须来自本 phase 截止前准入和调度、已完整保存且 source/admission/receipt 全绑定的 call；不是新模型机会或第三窗口。已正常准入的模型仍可按原 3,600 秒完成；没有用短超时强断正常推理。

治理审计最多 512 次、32,000,000 UTF-8 字节；scope/phase 单条上界 4,000,000 字节。资源门在写状态前检查，超限事务回滚，不留没有审计的进度变化。继承模型事件及文件边界不扩大。只读 API 不启动 worker，不隐式执行/恢复工作台；SQLite 辅助 WAL/SHM 行为不等于 OS 文件系统零副作用。

## 接口与源身份

`build_supplemental_scope(campaign_root, *, phase_id, start_epoch, end_epoch, original_source_root, provenance_pins, allow_saved_application_after_deadline=False)` 只读生成草案，不能生成 ready/paid 准入。`SupplementalStage(campaign_root, *, scope, admission:bytes, expected_admission_sha256)` 构造只读；`prepare()` 才登记阶段并审计当前进度切换；`run_remaining(gateway=...)` 必須显式注入网关；`apply_saved_action(origin_id)` 是显式本地应用；`inspect_saved()` 只核保存证据；`close(reason=...)` 永久关闭本窗口。

CLI `prepare/status/inspect/prompt/apply-saved/run/pause/resume/close` 都要求 campaign-root、scope 路径/原字节 SHA 和 admission 路径/原字节 SHA，没有实际路径默认值。`run` 还必须显式给 executable 并通过新增 paid 许可；`apply-saved` 必须给 origin-id。这里仅说明接口，未执行实际命令。原工作台继续由原 v15 源和原 task scope 加载；不会重建题包。

`source_pins()` 覆盖七个产品模块：supplemental、continuation、transport_correction、codex_gateway、diagnostic_campaign、store、structured_output_profile，CLI 自身另以 entrypoint SHA 绑定。逐项 import 核对未发现其他未绑定的项目内新执行模块。旧 v17 六源/旧入口按其原 scope pins 和原物理 source root 复验；旧 v15 工作台及导入依赖通过 plan.source_files 和独立命名空间逐文件检查。Python、stdlib、原生 Codex executable 不在七模块哈希集合内，具体准入仍需主控绑定所用解释器/预期可执行文件和安全 preflight；不能把 Python 源哈希称为完整 OS 环境认证。

CLI 没有模型覆盖参数，Gateway 常量和实际 command/receipt 均核 `gpt-6-astra/xhigh`；请求配置不等于供应商独立身份认证。完整 prompt 保留全部本题真实回答与已公开页，另公开新增时间资源及实际累计剩余额度，不放入评审、关闭证明内容、其他题、私有机制或未公开全量表；超过 1,048,576 字节即停，不截断。

`read_supplemental_projection(db, *, run, plan)` 仅 SELECT，无文件/Store/WB/网关操作，返回原 continuation projection 加 `supplemental_phase`。其中原阶段明确 `expired_incomplete`，原 paid=false/原 deadline 与新 paid/start/end 分列；`worker_liveness=not_verified`，文件证据不在 GUI 读取中重验。新调用附 `supplemental_phase_id`，公开 action overlays 保持原已返回 observation。旧无阶段 reader 遇新 source 身份严格拒绝，GUI 必须显式选择新 reader，不得宽松忽略来源。

截止修复新增 `timing_violation`、`timing_admission_blocked` 和 `timing_violations`。列表每项只含 call_id、原 admitted_epoch、原 dispatched_at、解析后的 dispatch_epoch 及 `dispatch_outside_window`/`dispatch_precedes_admission` 原因。违规仍保留原调用状态、时间与账务；读取不伪造暂停。新模型和首次本地动作均经过违规门，即使墙钟回到窗口内也不能绕过。正常的窗内完整调用可按明确许可做窗后本地收尾；迟到派发的已收费完整调用可展示费用，但不因此取得本地应用资格。

## 已有证据与当前待收口项

| 记录 | 实际结果与边界 |
|---|---|
| `supplemental_validation/author_001` | 首作者 14 passed / pytest 157.18 秒，全部输入前后稳定；回执 `c7e295e8dae1b731e8620d69469632b027e7fceb47c2f74e06a4f034f09b07e6`。对应 kernel `9b5fba7c…` / phase `d63ec0e0…` 初版，不作为后续时间绑定修订的最终通过。 |
| `supplemental_validation/root_joint_001` | 实际 20 passed / 272.172 秒，作者 16 加独立 4，输入前后稳定；回执 `a9a2d76961012df8c7816df04d1062486b6ecbf32db505fa6e7fd8ea6e0f78b4`。含真实冻结 v17 源/fixture 迁移及在途窗后本 call 收尾，对应 kernel `6e38e602…` / phase `30517106…`；修后不把这 20 项冒称重跑。 |
| `supplemental_independent_validation/001_deadline_before` | 独立 1 failed / pytest 23.39 秒；回执 `842da9ad445c322f8092f8d27643e6aa993633049af8eeb489984bf832398d97`。窗内 reserve 后，在 preflight 与 dispatch_started 写入间跨过 end，模拟进程启动 0、费用 11,235+160,000 保留，但 summary 因晚时间无法读取，治理仍 ready。原源码/测试/账务观察保留，输入前后稳定。 |
| `supplemental_independent_validation/002_deadline_after` | 修后窄组实际 2 passed / 1 failed，外层 67.25 秒；回执 `552bbd13e9ee233794dc503da6e2437cc05288ebe3f7e6ef9e03c73a0d12eadb`。原截止反例通过：0 模拟进程、账务可读且治理 paused；正常窗内在途回答的窗后本地收尾也通过。新增已完整迟到调用反例在时间精度断言失败：保存 ISO 为微秒，初始 expected 使用了更细的 float；未到后续准入断言，原失败完整保留。 |
| `supplemental_independent_validation/004_timing_precision_after` | 仅修独立新测试的 expected 为原保存 ISO 解析值，产品源码不动；单项 1 passed / 外层 26.50 秒，回执 `daa40c0c38501caaa10aef02a0be61a8f3d96f4d1a9de51cdc5f4ddbf55efd3b`。完整迟到回答的 11,248 已知与 80,000 旧未知仍可读，窗内/窗后 local 与新 model 门都拒绝。 |
| `supplemental_monitor_validation/003_after` | 另一作者显示接线 4 Python / 4 JS 全通过，回执 `d574e67e419d84f5ffcf79465c64ecf0f33378dc21319175af7cc0531eb26143`；其真实合成 phase fixture 覆盖同域保存回答/动作、None-only 旧 reader 回退、坏 phase 不回退和迟到费用展示。本人只读复核回执 `c98ae4e25b90e873b0627afe30f68f019a2a9d1c5540fc557a7333f25ef5535f`，没有复跑，也不把对显示层的独立审查当本人控制器的独立验收。 |

上述竞态不是越窗模型已经执行的证据，属于账务读取与准入判断混在一起的工程缺口。联合 20 项封存后，最小补丁仅改 phase 模块：账务保留可读；违规停止未来准入，不能取得窗后本地动作资格；来源/schema/receipt/hash 校验不放宽。`supplemental_validation/deadline_repair_001` 保留修前 `30517106…` 与修后完整源码和 unified diff；AST 证明只改 `_call_window`、公开 projection、`_open_phase`、`_authorize_local_start` 四函数，并新增 `_timing_violations`，其余 AST 相等。证明 SHA `9a3286bcf587841668610383b0fcdbada0aede06ed4523e2dbabdbb70b6a1d08`。原反例与必要关联边界现已分别通过；所有窄组均核输入前后稳定，作者没有重复 20 项。结论为有界工程准备通过，不是实际准入、模型身份认证、部署确认或研究成功。

## 最终源码与具体准入需绑定的内容

| 文件 | SHA256 |
|---|---|
| `src/quanta_agents/meta/casebank_supplemental.py` | `83e04e8875fe4ebf85ec5ab6ad931545cac8934e2f6cd90ae91d6993475aa392` |
| `src/quanta_agents/meta/casebank_continuation.py` | `6e38e602a1850930cd5bb2fe3d43f7f6a7b35e4c4fe62953506e759de04f9919` |
| `scripts/run_casebank_supplemental.py` | `429e0d7d3b191028f32545aaeabc6007e6972583d3a3049b13fcf949d5c44da9` |
| 作者 16 项文件 | `3b7add88f7cb80940942b7f798b2a9f0449e80fcb594f6a77f24ab7f941174e3` |
| 独立 4 项文件 | `a5e0a429314b04b68e57530c564dd20bb599fde351301529b53e34a64a6aa476` |
| 原截止反例文件 | `ca8b112a6cfa62d9033ac9f96e2df7e0b542d9c18e1fdbb55f986cebc3a14fc9` |

实际 scope 应绑定原政策、旧阶段关闭/原六小时已到期记录、唯一保存 inspect 应用回执、原 v17 完整治理后态和工作台备份、原 v15/v16 所有既有证据、七模块及新入口、独立工程/GUI 验收，再冻结绝对新 start/end 和唯一 worker 的启动控制。`provenance_pins` 逐项读取并核字节 SHA；不能只把批准文件名写在说明里。新 paid 许可必须是独立具体 admission；本报告和政策文件没有派发权限。

命令接口为 `python -B <已核验新入口绝对路径> <command> --campaign-root <原campaign> --scope <新scope文件> --scope-sha256 <原字节SHA> --admission <新admission文件> --admission-sha256 <原字节SHA>`。`status/inspect/prompt` 是读取；`prepare` 登记一次时间窗口；`apply-saved --origin-id <原已保存call>` 才显式本地应用；`run --executable <已核验原可执行文件>` 才可能派发。`pause/close --reason <明确原因>` 不创建后续窗口。这里保留占位符，没有写入或运行任何实际命令。

两问自检：**是否通过新增时间洗掉原失败、费用或研究分母？** 没有；旧阶段到期不完整和旧许可原样保留，另明示时间资源增加，同域计数和原任务保持。**下一步是否应再扩功能？** 不应；只关掉已实证的临界时点显示缺口，完成新组与来源迁移核验，再由主控审具体 scope/窗口/唯一 worker。最终有无正确调查结论、弃权或 final 需实际研究和独立评价，工程测试不能代替。
