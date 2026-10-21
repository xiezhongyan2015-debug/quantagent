# v17c1 实际保存查询应用结果独立复核

结论：**原纠错回答已在原工作台完成一个公开查询，治理状态已暂停。结果与限定本地准入一致，未发现重复动作、重复收费或原记录改写。** 这只是一次 `inspect_inputs` 的完成，不是原四题研究完成，也不是模型或框架稳定性验收。

本次审计只读取保存文件与 SQLite 状态，实际运行一次，退出码 0，观察耗时 6.23 秒；没有重放动作或重跑工程测试。审计预先阻断 continuation/Store/WB/Gateway 构造与执行、进程/网络调用和文件写入；13 次数据库连接均强制 `mode=ro`，SQL authorizer 仅允许 SELECT/READ/FUNCTION。结束后只向独立审计目录追加凭证，实际账本与工作台状态未修改。

## 文件与身份绑定

项目根目录为 `/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents`，以下路径相对于此目录。

| 已核文件 | SHA256 |
| --- | --- |
| `experiment_traces/meta_casebank_references/campaigns/reference_v15_original4_001/v17_saved_application_001/receipt.json` | `4c0d25bc7b2cd819fbad3e4bdf5295c549ad63898df212d1d5baf16abfa0f868` |
| `docs/research/meta_framework_v17_handoff/saved_local_application_admission_001.json` | `f0477ce8d2fbc97e1944a4002ca42d2d6df9bdbb4739b90e44e994f9e565296f` |
| `docs/research/meta_framework_v17_handoff/apply_original_saved_action_001.py` | `ed0efaa11319a6872c52b5c0f641f88de37dfe16cfc639ae873848601a30ee46` |
| `experiment_traces/meta_casebank_continuation_outcome_reviews/reference_v15_original4_001_v17c1_001/verification.json` | `c75e78992dd6d532b7a336187fcb1f5a1722705b625016a08674a0a72a6d9e86` |
| 同独立目录 `receipt.json` | `8a0623ba2a902ae1457d4152c6abebd7cd3141784228548906dc7abc7389fa33` |
| 同独立目录 `verify_outcome.py` | `7e897596b03263915ddc4119e94e71a851537f7f7ba19e3e6a220b3d289bb441` |

实际 admission 的 scope、源、入口、两份独立准入材料及应用脚本 pin 均匹配；付费权限为 false，明确允许本地过期保存动作。scope 仍为 `63f93f90b7842fa39b3c0d037c661255ab496ce73c11c84099b4545d52552edb`。根结果 receipt 所列全部 7 个产物 hash 均通过，包括执行前全库备份。原 run、call、纠错表完整正文逐字与该备份相同，原 13 个拒绝文件 pin 未变。旧来源文件与四份 frozen_public 字节 hash 也匹配；未重新解析原题数据或读取 private/key/CSV。

原纠错 completion 使用纯 `verify_saved_completion` 复核，request、intent、prompt/schema、exit、完整 completion 和 receipt 身份均与原已提交记录一致。原纠错 receipt SHA 仍为 `9765bb22fb6cf1a2767a7a246a689835117f3af37035e4668282698fb5bc7f48`。实际动作来源是 `reference_v15_case01_transport_correction_001`，绑定原 case 01 reference 任务、semantic round 0、WB slot 1；请求保持原四键 JSON：`inspect_inputs`，唯一 query 为 `inputs / limit=50 / cursor="" / evidence_id=""`，candidates 与 reports 为空。

## 保存返回与执行状态

当前 v17 治理表只有 1 条 action，状态 `applied`；v17 call 表为 0 行。WB 只有 slot 1，状态 `completed / settled`，query_reserved=true，candidate_reserved=false，final_reserved=false。它的 bundle 与 receipt 文件名、字节数、SHA、scope/slot/request 绑定均通过。由保存 bundle 重建的 observation 与以下四处完整一致：WB 原记录、continuation action、根 `public_observation.json`、根保存的 inspection overlay。

完整 observation 内容 hash：`ab6bc701aca24846e718db589552df8d0796b7df8f6084a3c6c8ed57b8d58408`。WB bundle SHA 为 `f00f06c8875daafb1228f34fbd24d9003db70e9f7780b93f71c7c332f267e6f4`，17,161 字节；WB receipt SHA 为 `307b5f493d4f8129a9cf2c298bde98fe19316ce06d140347ce758c0e815eaf1c`，220 字节。

保存页为 inputs 首 50 行、offset=0，报告总行数 1,536，next_cursor 非空。页 hash `709185facc456196952e922d6f348ca3f8d377bdf38bb3566dad0ae879e7816c` 与全部保存字段重新计算一致。本审计没有请求下一页、读取游标密钥或把已返回 50 行当成全表研究；也没有重新计算这些公开输入的内容正确性。核验结论是保存返回及其身份一致。

四条治理事件严格按时间排序：

| 事件 | UTC |
| --- | --- |
| continuation_prepared | 2026-09-07 03:17:06.929972 |
| public_action_intent | 2026-09-07 03:17:07.441270 |
| public_action_applied | 2026-09-07 03:17:09.137365 |
| pause | 2026-09-07 03:17:11.306095 |

根保存的 prepared 仍是查询前 awaiting_action/0 query，inspection 为应用后 saved_evidence_verified/1 query；脚本按保存 inspection 后 pause 的顺序执行。`paused.json` 内容是 null，因为 pause 没有返回值；停止证据来自当前治理记录、pause 事件与保存 monitor 状态，不能把 null 文件本身当成停止证明。此处确认的是治理暂停，不独立探测操作系统进程存活。

case 01 的 WB 仍 ready，可供未来另行授权使用，但本次 continuation 已 paused、付费 false。后三个工作台完整状态与 scope 历史零状态一致。所有任务候选和终稿计数为 0，期限子项、kernel、匹配计算和新策略回测均为 0。

## 账务与结论边界

原预算与时钟未变，原模型截止时间仍为 `2026-09-07T03:01:23.682649Z`。本地应用发生在该时点之后，符合已明确批准的保存动作例外；例外没有开放新模型入口。

账务保持原 2 次 transport：原失败调用 usage unknown、80,000 预留保留；原纠错完成记录 11,079 input + 156 output = 11,235 已知 tokens，reasoning 113 已包含在 output 内，cached 0。已知加未结预留合计 91,235。本预算域新增 model call 为 0，原 11,235 未被重复记入新调用，80,000 未释放。本审计新增 Gateway、工作台动作和 SQL 写入均为 0；工程评审模型 token 未计量，不能记零。

原失败身份、四任务分母及未完成终稿状态继续保留。provider request binding 与模型身份仍未独立验证。一次本地查询通过不证明最初 HTTP 400 的唯一根因，不证明策略效果、真实执行、架构稳定或后续补充阶段已准入；v18 不在本报告范围。

## 两问自检

1. 是否把一个已保存输入页当成候选发现、正式终稿或框架成功？没有。本次只确认原保存回答的一次查询应用和准确停账状态。
2. 是否通过重放动作、重置时钟、抹除旧失败或释放未知费用取得新机会？没有。审计完全只读，原 80,000 未知预留、11,235 已知费用、2 次 transport 与原截止时间均保留。
