# 实际单次纠错结果独立复核 001

结论：**已保存的纠错完成、请求绑定与同预算账务核验通过；回答尚未应用。** 原第一次 HTTP 400 失败及 80,000 未知费用预留保留。当前原 campaign 合计 2 次传输、11,235 已报告 I/O tokens、80,000 未结预留，名义暴露为 91,235。唯一纠错许可已经消费，checkpoint 022 明确重新关闭付费；不得重发这个请求。

评审沿用 Astra / xhigh，仅检查保存原件及只读 SQLite。没有构造 Gateway、Store、纠错 Controller，没有调用 Workbench.execute，没有请求模型、读取行情或修改实际账本。独立检查在内存中把 Gateway / Store 构造、Workbench.execute、网络和子进程入口设为禁止；只调用 `verify_saved_completion` 及原 JSON Schema 输出校验器。本次评审新增研究请求 0，新增工作台动作 0；工程评审模型 token 计量未知，未写为零。

## 已核验的实际返回

目录为 `experiment_traces/meta_casebank_references/campaigns/reference_v15_original4_001/transport_corrections/reference_v15_case01_transport_correction_001/`。外部 pin 的 `receipt.json` SHA：

`9765bb22fb6cf1a2767a7a246a689835117f3af37035e4668282698fb5bc7f48`

保存回答只有以下查询请求，没有查询结果、诊断结果或研究终稿：

```json
{"action":"inspect_inputs","candidates":[],"queries":[{"cursor":"","evidence_id":"","limit":50,"table":"inputs"}],"reports":[]}
```

离线重放传入原 receipt 的 artifact hashes、冻结 prompt hash、wire schema 和 thread id，返回 receipt 与保存原件精确相等。再次用原严格 schema 检查该 response，通过。这里的 `original_schema_status=valid` 不等于完成 Workbench 的跨字段、状态和执行前校验：`workbench_validation=pending_not_performed`，`public_action_applied=false`。

请求、意图、退出和事件绑定如下：

- intent id：`60b91be7d21142d5a60d1499f9f91d50`；本地 invocation id：`bcfbaf7e-2e0c-4f6f-a524-1bf8583343df`。
- thread id：`01a078cd-7b97-7c82-9a4f-e8b605dd2f24`。request 的 intent id / intent 文件 SHA / schema 文件 SHA、intent 内 permit / admission / source / 原 plan / task 身份、退出记录 invocation id 均一致。
- 事件包含一个最终公开 agent_message 和一个 turn.completed，两者与 response、receipt usage 一致；没有 turn.failed、远端 error 终态或工具执行事件。原 code-mode 禁用警告仍出现，但不妨碍本次完成；不以该警告解释旧 400。
- 保存退出码 0，`exit_observed=true`，`process_started=true`，用时约 **12.922 秒**。这是该本地 invocation 的保存退出证据；本评审没有另做当前 OS worker 存活探测。
- requested model / effort 是 `gpt-6-astra` / `xhigh`，但 `model_verified=false`、`provider_request_binding_verified=false`。本地 thread / invocation id 不被提升为供应商身份或计费认证。

实际写出的 `output_schema.json` SHA 为 `53055352dc067c530d0187d26a892e24482d1b864e9fcad4563fb25d088e66e0`。它与准备目录 pretty JSON 的字节 hash 不同；解析后的 schema 精确相同，独立重建确认相对旧 schema 仍仅移除数组值 horizons enum 并增加长度恰为 3。实际 prompt 字节 SHA 仍为 `08f715a27ab3c14c5dea62e80991e6215a83dba5ba57953be5e8227d74df73bb`，与原提示词逐字节相同。

## 账务与原研究状态

| 项目 | 保存结果 |
| --- | ---: |
| 原失败传输 | 1，status=failed，四项 usage 均未报告 |
| 已完成纠错传输 | 1 |
| 合计任务 / 阶段传输次数 | 2/17；2/68 |
| 纠错 input tokens | 11,079 |
| 纠错 output tokens | 156 |
| 其中 reasoning output | 113，已含在 156 内，不再相加 |
| cached input | 0，属于 input 子项 |
| 新已报告 I/O 合计 | 11,235 |
| 原未知预留 | 80,000，未释放 |
| 新未结预留 | 0，仅新完成费用已结算 |
| 未结请求数 | 1，原请求 |
| 当前名义暴露 | 91,235 = 11,235 + 80,000 |

这里的 91,235 是已知小计加名义预留，不是两次调用的已确认总账单。旧请求不能因新请求成功而改成零费用或 completed。

原 run / call 的完整内容 hash 与事前 permit 中的 base_run_hash / base_call_hash 相同；原 run 保持 paused、pause_requested=true、actions 为空。原 budget 未变，任务 600,000 / 阶段 2,400,000、17 / 68 次仍在；created_epoch 仍为 `1788728483.6826499`，六小时截止仍为 **2026-09-07 03:01:23.682649 UTC**，未因纠错重新起算。

四个原 Workbench 的 frozen_public 文件及 state hash 均与 permit 对齐，scope 不变，状态均 ready，actions / queries / candidates / final_attempts 均为零。case_01 有两次传输、semantic_round_index 仍为 0；case_02–04 各零传输，因此是“尚未开始”，不是失败被删除。四题描述性分母保持，也没有新增独立研究样本或正式成功分母。

新增同库纠错表只有一条记录，status=completed、dispatch_state=stopped，receipt 与保存文件相同。事件账中许可消费、dispatch_started、completion_saved_stop 各恰好一次，没有 failed_stop。原 13 个固定文件全部匹配事前 SHA：原调用七件、campaign.json、原 root worker 四件及原 failure audit receipt。纠错目录 11 件全部核验，核验前后字节 hash 不变。物理 SQLite 文件因合法追加新表/记录发生变化，不把“原行未改”误说成整个数据库文件字节未改。

## 关闭门与结论范围

checkpoint `docs/research/meta_framework_v8_handoff/checkpoint_20260907_022.json` 的 SHA 为 `78fa21477b3bb9652fd7ec9661baa2659c6a0567802efbbc0d8456c8f3c30ba1`，`paid_dispatch_permitted=false`。本次保存结果不授权另一传输、重开许可、自动应用这条查询或继续原工作台。

本次新 wire 请求取得了一次完整返回。这增加了修复方向的实际证据，**不能倒推数组 enum 是旧拒绝的唯一已证原因**：旧远端错误未给出具体 schema 位置，也没有控制供应商后端的其他变化。一次传输成功不证明一般供应商兼容、实际供应商模型身份、框架稳定性、策略能力或四题研究完成。

独立审计产物位于 `experiment_traces/meta_transport_correction_outcome_reviews/reference_v15_case01_correction_001/`：

| 文件 | SHA-256 |
| --- | --- |
| `verification.json`，完整检查结果、11+13 文件身份与账务 | `f617e514dbf6819a4db7c6179042cb8a69d193200db3d4d1af52022fee72bdc5` |
| `receipt.json`，本次独立保存审计回执 | `b8044359d550b32d03502ee95af9569c503dc9c7cd0b17d1adbd8db8b50720c1` |

审计脚本及其 hash 一并保留。本次只是有界保存证据核验，没有重跑模型或研究测试。

## 两问自检

- 是否因纠错成功而抹去旧失败、未知费用、资源劣势或尚未开始的三题？没有；两次传输、旧 80,000、原时钟和四题分母完整保留。
- 是否把返回格式通过或保存完成当成工作台合法/已执行、供应商认证或研究成功？没有；回答仍待工作台校验且未应用，来源身份未认证，付费门关闭。
