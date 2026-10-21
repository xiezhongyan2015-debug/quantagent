# 原预算保存动作续研究：工程交接 001

2026-09-07，Astra/xhigh 工程实现。实际研究付费仍关闭。本代理只用新建的 literal 合成夹具和假进程，没有加载实际四题工作台、执行实际工具、读取私有答案或修改原研究库。当前代码是独立 v17 接线；原 v15/v16 源及原计划、原调用、纠错行均保留。

## 实现及身份

新增 `meta/casebank_continuation.py` 与 `scripts/run_casebank_continuation.py`。原 SQLite 仅追加四张带版本表：continuation、calls、actions、events；同一原 registry `.dispatch.lock` 和 `BEGIN IMMEDIATE` 管理新意图/预算。原失败 80,000 未知保留额、纠错 11,235 已知 input/output 始终归入同域；缓存预算必须与冻结 scope/原预算完全相同。先应用原 semantic round 0 的保存纠错，工具槽为 slot 1；仅在另获模型派发许可后，下一模型传输才是 transport 3 / semantic round 1，不把 transmission 当 query。

历史纠错按原事件/退出/请求/回执/精确 schema 和原件哈希重核；旧零动作工作台完整 pre-state body 保存于 scope，并核对 v16 permit 的原 state hash。后续工作台推进不要求旧 v16 controller 再通过其当前 ready/0 检查。原 v15 Workbench 及其依赖在独立 Python 命名空间按路径/源码哈希加载；不修改旧源码、不复制新 seed。这不是 OS 沙箱或供应商独立认证。

保存模型回答先有唯一 origin+slot 应用 intent，原 Workbench 执行/保存后再提交 continuation 后态。跨两个 SQLite 使用可恢复意图协议，不声称跨库原子提交。slot 尚未开始时，首次执行前重核暂停和原时钟；已有完整工具 receipt 可由显式 `apply-saved` 只做本地结算。工具 pending 且缺原件、模型未完整结算均停止；没有重问模型、换槽补算或自动 resume。完整模型调用后的本地错误保留 completed 和费用，标动作待应用。

只读 `inspect_saved` 不调用 Workbench `prompt/execute/recover_saved`；重核已公开 observation 与 artifact/scope/slot、完整后态。这里的只读指无 SQL 写/业务状态推进；不把 SQLite WAL/SHM 辅助行为宣称为文件系统零副作用。新模型上下文只含本题原 packet/overlay、完整真实回答与实际返回的公开页/摘要。不会额外发送保存但未公开的全量 bundle、其他题、key、准入审查或私有设计。外层预算说明重新计算真实剩余传输/机会和名义 headroom；不是原 prompt 字节重放。单次 3,600 秒，完整上下文 1,048,576 字节超限停机而不截断；原六小时是下一调用准入截止，已经准入的正常在途推理可按原单次时限结束，不能重置阶段时钟。

`read_continuation_projection(db, *, run, plan)` 仅 SQL SELECT，缺新表返回 `None`。返回新治理后态、同域 accounting、新 calls 及按 origin call id 索引的 `public_action_overlays`。overlay 的 observation 是原公开返回原值；原纠错不重复加一条费用记录。第一题准备后 `awaiting_action`，其他三题 `not_started`。回执仅公开 response/usage/model/effort/model_verified 和实际保存的 duration_seconds；不猜耗时。GUI 读取不重新打开文件，明确 `projection_file_evidence_reverified=false`、`worker_liveness=not_verified`。

## 已保存的测试证据

| 记录 | 实际结果 | 解释 |
|---|---|---|
| `continuation_validation/author_001` | 3 failed / 7.74 秒 | 原有 schema hash 按 JSON 插入顺序计；合成夹具/新 wire 持久化时排序不一致。保留初次 stdout、JUnit 与失败后源码/测试快照；后者不是执行前 manifest。 |
| `continuation_validation/author_002` | 16 passed / 129.93 秒 | 已覆盖保存应用、四题假网关闭环、两库崩溃、原费用/时钟、未知阻断及只读读取。对应旧源码 `714451c161a911fbbb8febe42715c1f9ebcb2a4c8770f9a891464709abe6bc02`，不能作为后续修订源码的最终通过凭据。 |
| 独立 `continuation_independent_validation/001_mutable_budget_before` | 1 failed / pytest 10.14 秒 | 内存 per-task cap 从 171,234 改为 171,235，真实抵达禁止 Gateway 钩子。没有调用模型。回执外层耗时 13.047 秒；四个输入前后哈希稳定。已修缓存/冻结预算等值门。 |
| `continuation_joint_validation/001_root` | 81 项，52 passed / 29 failed，49.497 秒 | 29 个 continuation fixture 均在过深 Windows 临时目录触发 WinError206；原失败与稳定源/测试 pins 保留。52 项监控通过不能解释成续研究内核通过。 |
| `continuation_validation/saved_origin_before_001` | 1 failed / 12.83 秒 | 保存纠错尚未应用时，直接 prompt 与内部 reserve 两门均未拒绝，第三传输只预留、没有 Gateway/工具执行。源码/入口/两测试文件执行前后哈希稳定。 |
| `continuation_joint_validation/002_short_temp` | 30 项，29 passed / 1 failed，375.727 秒 | 作者 21 + 独立 7 + 保存 origin 门 1 全部通过，源码前后稳定。唯一 GUI 失败因合成原 call 漏 `plan_hash/task_identity`，严格监控拒绝，非产品放行失败。 |
| `continuation_joint_validation/003_gui_integration` | 1 failed / JUnit 13.622 秒 | root 仅补合成原 call 的 `plan_hash/task_identity`。prepare、保存动作应用、预算与公开 observation 已通过，断在纠错回执的 duration 丢失；原 v16 只读投影未将保存耗时列入白名单。该失败保留。 |
| `continuation_validation/duration_projection_001` | 10 passed / pytest 4.56 秒（JUnit 4.499 秒） | v17 继承投影最小修复：原值保存耗时、缺失/null 仍未知；负数、bool、字符串及非有限 JSON 数拒绝。仅运行新增 10 项，没有重跑核心 29 或旧监控 11；输入前后哈希稳定。 |
| `continuation_joint_validation/004_final_monitor` | 53 项，52 passed / 1 failed，JUnit 16.838 秒 | 当前监控 52 项通过；GUI 已通过 duration，后在测试将默认 16,384 字符第一页直接按完整 JSON 解析处失败。生产分页正确，保留原测试与失败。 |
| `continuation_joint_validation/005_paged_gui` | 1 passed / JUnit 12.286 秒 | root 仅修测试按 next_offset、最多 8 页重组并逐页核 origin/kind/hash、全文长度，再核保存 observation hash；真实合成 prepare→apply→GUI 分页集成通过，产品源码未改。 |

JUnit/stdout SHA256：author_001 分别 `66ab94d178980ca63faa403a2873814abcfa69e35876d6e12d896b58b1f84cd6`、`dab8af19b1077fdbd0fd3a7768372eb6f44af8ab76151d1d95d68985b596d52d`；author_002 分别 `91fcea85aa3fc6ed5c78407205fc46e6656b79cd43cafe653f66ed8ed959d7dc`、`0cb4b7ff2c65ca28627fd1edb8de8c3b7de0de6f492b146b270e1caa60104de4`。独立 before receipt SHA `c5daf5b3337aa6a56c753752acdf15d1b6bedb01917b4ed24661f6da063491bc`。

已收口的其他门包括：origin 的 task identity 与 semantic round 直接绑定；slot 未开始不能用旧 intent 绕过新暂停/过期；schema 格式通过但 horizons 不精确时仍交原 Workbench 保存失败/扣机会；已 completed 调用后的本地异常不降级成未知模型调用。跨题投影来源门属于静态发现后修复，未制造或宣称有修前运行失败。

续行核心最后的最小修复在 `_history`：首题必须已有 correction origin 的 applied action，才可构造下一 prompt 或预留新传输。此前公开 `run_remaining` 的先应用顺序已安全，但下层门不能独立保证这一点；现已统一拒绝且同一反例在核心 29 中通过。原 before receipt SHA `950bc8def00598dca7409606b79a1ab51033252db72a6574b8e42707793d120e`；联合 001 receipt SHA `3eb93088b15c2f3e9af1dbdf99975969d56eeedbe9e25926d25e4440398db5ae`；002 receipt SHA `c77977b66936db2678073b707053c6ca0af25dc6d025ab7e12997c71a92ec6be`。

核心 29 已验证的源码 SHA `6cc0dc07cb60ac3f8f8b507b911c028a85a82305283132be4432a9b401b19b86`；CLI `718120916a846cfe7ff9955b326d6148303f9ce5486746887dcf819540e98c2b`；该次作者测试输入 `1cd1f04bd7e4592da857b1d457e50e47f4df5db86a6709c1751b6b2f3af6a06a`；独立七项 `946e3f5b0c36b6a954c93d6915d0d474ec3805ee1f8c1c54b79716af27527b09`；保存 origin 单项 `601ef2efcac85ae2594c2dcebcb05a7b8b7d14b95b1a8d934b9ed3d312af1f13`。元数据补全后的 fixture 文件哈希应由后续 GUI 单项回执记录，不能回写为旧 29 的测试输入。

v17 `transport_correction.py` 最终 SHA `6344082f22c2fc4f517565ed14be5666f36f40d6cc7e26d3a6d95c1a50f96663`。`duration_projection_001/ast_scope_proof.json` 证明只有 `read_correction_projection` 的 duration 白名单/有限非负校验及新增 math import 改变，其他顶层节点和函数 AST 全等；修前后源码、diff 均保留。原 v16 文件仍为 `5fa687acd947870d4b4fd2896a1a693e6f1e3de9c8a28614336e82ffbff0d3f1`，不改旧纠错源身份。新 duration 测试 SHA `2678592e5ff01659d26fd9b71a1495aa10e75e45cd90544c5c7c49bc1b273da7`，回执 SHA `9037debe9fcc33b9541456b068681cb0a82e10c94bebefad4cb911eb11a239bf`，JUnit `341a157a3275013b1382ed0575854cac00409d36e6521b14b21874e55e9312b0`，stdout `c799798b7a2e1161273ca3b714590691d7828e0ccdf565dfb2a00b8d2e557c5b`。003 GUI 原失败回执 SHA `8592341bc2d53731d8752ffa3b9beacf3e18d472161e6507fd4da5926ddd6397`。

root 补全 metadata 后的作者夹具 SHA 为 `bab55a823d581c90b49cb1e05d10248f126a9d7e9d2d8c29fdba89724bf7f044`。同目录 `fixture_metadata.diff` 只有两行注释及一行 `call.update`；删除该块后精确重建出 002 原 `1cd1…` 字节哈希。`author_test_002_reconstructed.py` 明确是事后哈希核验重建，不冒称原执行前快照。不同轮次的通过项目不拼成一轮联合结果。

004 回执 SHA `3c560b171768303136aeb6180229b5a3c25f234dece94a883c386b7543562ce7`；分页误用诊断 SHA `537abcfb2a1f9e274e1212bbc9c442b46f4c17ea079d7ff1193aec09b1b22b73`。005 回执 SHA `6f465159b7091af4b981bd896f7c4aea8319a70bc5ed233dec2dd5eed895d233`，JUnit SHA `51dd483635dabfec06b2b8ef853930cdd53f643290bb6a2718ec2ca53658ea2e`，stdout SHA `86c12daab73e51b1fe06ff96db1a08b3d31844690f3fefe52f27868de3df609a`，GUI 测试修订 SHA `0423f6b2bcc6cc87c6cc1ba72a0954a809fb6e762b1f1f30bfb6df7c7a7f2f36`。这两个回执的 source/test 输入均前后稳定；核心 29、duration 10、当前监控 52、最终 GUI 1 属于分别执行的有界证据，不能改写为一次 92 项通过。

## 接口和命令

Python：`build_scope(root, continuation_id=..., provenance_pins=[...], allow_saved_application_after_deadline=False)` 只生成只读草案，不生成 ready/付费许可；`CasebankContinuation(root, scope=..., admission=<原始 bytes>, expected_admission_sha256=...)` 构造只读。`prepare()` 才建新治理表，`apply_saved_action(origin_id=None)` 显式首次应用/同值结算，`run_remaining(gateway=...)` 需要外部注入 Gateway。`pause(reason)`、`resume()` 是显式管理操作；resume 不派发，也不豁免新 unknown。

CLI 每次必须提供以下参数，没有实际目录默认值：

```text
python -B scripts/run_casebank_continuation.py <command>
  --campaign-root <同一个原 campaign>
  --scope <冻结 scope.json> --scope-sha256 <文件原字节 SHA256>
  --admission <独立 admission.json> --admission-sha256 <文件原字节 SHA256>
```

这是参数说明，未执行。`status` / `inspect` 全只读；`prompt --task-id <原 task id>` 仅在该题已存回答完成应用后用于查看完整下一轮输入。`prepare` 仅追加治理；`apply-saved` 省略 origin 时指原纠错，其他完整保存调用必须显式 `--origin-id`。`pause --reason ...` / `resume` 均不启动 worker。只有 `run --executable <独立准入核定的原可执行文件>` 才构造 Gateway，且 admission 的 `real_dispatch_authorized` 必须为 true；本报告不提供此授权。

admission 必须含 `kind=casebank_continuation_admission`、`ready=true`、`scope_hash`、`base_plan_hash`、`source_hash`、`entrypoint_sha256`、`local_application_authorized=true`、明确布尔值 `real_dispatch_authorized`。来源和入口 hash 必须对应最终联合使用版本，不能重用旧 v16 纠错许可。

## 实际 scope 的必要准入证据建议

root 应把原 plan/rubric/budget/scope/公开输入接受回执、已关闭 checkpoint、原 HTTP400 文件和原 80k quarantine、已消费 correction permit/完整 receipt/request/events/exit、旧 v15 完整依赖和新 v17 controller/Gateway/profile/入口组成明确来源链。scope 自带原 run/call/correction 完整行的内容哈希、原 created_epoch/budget、四份历史 WB 完整 pre-state；provenance pins 要另外纳入 root 保存的独立零动作备份回执。

主控报告该备份为 `experiment_traces/meta_casebank_continuation_preparations/reference_v15_original4_001_preservation_001/receipt.json`，SHA `5087b3faf33fc8ce21d713635d29eb0af05c695339f9711a21a34cf8dee801f7`；这是主控提供的身份，本代理未打开实际备份/工作台。原下一模型准入截止 **2026-09-07 03:01:23.682649 UTC**；任何暂停、工程修复、余额展示恢复不退还时间。若只准本地应用过期保存动作，必须另明确 `allow_saved_application_after_deadline`，不能由 loop 自动推导。

Windows 路径静态检查采用已知原 campaign 路径（116 字符）、原 task id（45 字符）和短 continuation id `v17c1`：第 17 次传输的 `codex_request.json` 完整路径为 220 字符；按 task id 64 字符上限为 239。该固定实际范围无需另换 hash 文件夹；新的测试使用短 TEMP，保留原过长路径失败。这里仅计算已知字符串，未读取实际价格/工作台。

有界工程检查已按上表分别完成；仍需最终来源清单/组合验收及实际 scope/admission 独立审查。任何实际本地应用须明确授权；任何新增模型传输另需原 executable/唯一 registry/GUI 控制权/无在途 worker 的启动前确认并遵守原截止。本轮主控明确付费继续关闭，只准备已保存动作的本地应用，不构成付费续行许可。输入仍是暴露合成开发材料、`costs_applied=false`、`execution_valid=false`，结果不能计正式策略成功、P4 或净优势正控资格。

两问自检：**是否为了继续而清掉原失败、未知费用、机会或时钟？** 没有；原行不改，同域准入继续累计，两个传输已经消费。**工程通过能否代表真实续研究已获许可或成功？** 不能；当前只完成有界工程准备，组合验收与实际准入分开，本代理未新增真实模型/工作台动作。
