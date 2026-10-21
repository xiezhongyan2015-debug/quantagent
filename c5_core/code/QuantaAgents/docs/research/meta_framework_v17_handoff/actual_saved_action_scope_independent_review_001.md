# v17c1 实际保存动作 scope 独立准入复核

结论：**可准入这份 scope 的限定本地流程，模型派发保持关闭。** 准入对象是 `reference_v15_original4_001` 原账本上的新治理记录，以及已保存纠错回答的单次应用；本报告不是付费 admission，也不表明工作台动作已经通过验证或已应用。

允许的顺序仅为：`prepare` 同库治理 → 应用 `reference_v15_case01_transport_correction_001` 原保存的 `inspect_inputs` 一次 → `inspect_saved` → `pause`。实际 admission 必须绑定本次 scope、源清单与入口，固定 `local_application_authorized=true`、`real_dispatch_authorized=false`。不得派发新模型、追加 transport、重开原失败调用或转入候选发现。原新模型截止时间 `2026-09-07T03:01:23.682649Z` 已过且不延期；`allow_saved_application_after_deadline=true` 只允许这一已保存动作的本地首次应用。

## 具体证据

项目根目录：`/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents`。下列路径均相对此根目录。

| 文件 | 已核 SHA256 |
| --- | --- |
| `experiment_traces/meta_casebank_continuation_preparations/reference_v15_original4_001_v17c1_scope_001/scope.json` | `63f93f90b7842fa39b3c0d037c661255ab496ce73c11c84099b4545d52552edb` |
| 同目录 `receipt.json` | `bde186caddbdbefa638b6d2191e0607a8b45788e0580f840847a1f6b9587077d` |
| `experiment_traces/meta_casebank_continuation_preparations/reference_v15_original4_001_v17c1_scope_001_independent_review/verification.json` | `cd08843367ca18e03b1312aa4342c6f878fe2dfa3fd97d1edde18f59806edc11` |
| 同独立目录 `receipt.json` | `8d8b3448d4ce4f6bc66b25192f5ecb025bf846bb24d9756a1a80c1baabba8f54` |
| 同独立目录 `verify_scope.py` | `3a3a7e914f4120619a942c43740034edef3ebc3f14ed8898ffe4179b8feae1da` |

本次独立脚本实际运行一次，退出码 0，观察耗时 6.59 秒。没有重跑工程测试。脚本预先阻断 Store/Gateway 构造、进程/网络调用、工作台加载与执行、prepare/apply/reserve/pause 等修改入口；所有 12 次 SQLite 连接强制 `mode=ro`，authorizer 仅允许 SELECT/READ/FUNCTION。12 个 provenance pin、6 个当前源 pin 和入口 pin 全部核对；scope 文件 SHA 与规范内容 hash 相同。

四个工作台的当前 `workbench` 状态逐一与 scope 历史快照、原保存备份 receipt 的状态 hash、原纠错 permit 的状态 pin 对齐：均为 `ready`，queries/candidates/final_attempts 全为 0，actions 为空，rejections 为空。该核验直接读取状态元数据，没有读取题目 CSV、private 或 key。原 `frozen_public.json` 仅按构造器要求读取字节核 hash，未解析或展示题目内容。独立核验时新 continuation 四张表均不存在，符合尚未注册、尚未应用的状态。

带内存临时 admission（paid=false）的只读 `CasebankContinuation` 构造器和 `summary()` 通过；临时 admission 未落盘，`summary()` 返回未注册状态。构造器完成原拒绝事件、非零进程退出、13 个原文件 pin、纠错 request/intent/prompt/schema/exit/完整 completion 及已提交 receipt 的验证。核验前后原 run/call/correction 的完整正文和四个工作台状态正文保持相同。

原 run hash `49cf9914f95cd353b430cb8eacfc25d07f44b62cd26a60bcbcc7344296c40f79`、原 call hash `7e7f62a97f07919c022fa58e303df9c889f39c1a52769a9f0db7e44faa0bef80`、纠错 row hash `c14d319d9a39604852b96cc13d3515eab58d682928134332198fb38891f5bd7e` 均吻合本次 scope。原 run 仍 paused、pause_requested=true，原失败调用仍 usage unknown。

纠错 receipt 文件 SHA 为 `9765bb22fb6cf1a2767a7a246a689835117f3af37035e4668282698fb5bc7f48`，回答准确为四键公开 action：`inspect_inputs`，queries 仅一项 `table=inputs / limit=50 / cursor="" / evidence_id=""`，candidates/reports 均为空。该回答通过原 schema；工作台跨字段校验与应用仍待后续显式动作，不能提前标为合法工作台结果。

## 费用、分母与后续核对边界

预算与时钟保持 scope=原 plan=原 run：4 个原任务、共 68 次上限、每题 17 次上限、原名义总额 2,400,000、每题 600,000、每调用预留 80,000 均未改变。当前账务仍为 2 次 transport：原失败未知调用保留 80,000；纠错完成的 11,079 input + 156 output = 11,235 已知 tokens，reasoning 113 已包含在 output 内。已知加未结预留合计 91,235；本次核验新增调用、预留和工作台动作均为 0。工程评审模型用量未计量，不能记为零。

若之后应用成功，预期仅 case 01 增加一个查询，candidates/finals 仍为 0；case 02–04 继续零动作。原失败、原纠错回执和 80,000 未知预留均保留，已知 11,235 不得再次收费或被释放抵扣新机会。应用失败或出现不完整恢复状态时，应保存失败/未知证据并停止，不能为得到结果重新发模型请求或新建等价机会。

`model_verified=false`、供应商请求绑定未独立验证。本结论不认证实际 provider 身份，不把本地哈希一致性当真实性认证，不把一次查询应用、工程测试或 transport 成功算作四题研究完成、架构稳定或真实市场执行合格。本报告未进行工作台应用或浏览器操作。

## 两问自检

1. 是否把既有工程通过扩大成付费授权、研究成功或已验证工作台动作？没有。这里只批准该具体 scope 的本地保存动作序列；实际派发 admission 仍必须为 false，工作台校验仍待进行。
2. 是否删除旧失败、未知费用、原任务分母，或重置原预算/时钟来取得新机会？没有。原失败与 80,000 预留保留，4 个任务仍在原预算域，原模型截止时间不变且已过。
