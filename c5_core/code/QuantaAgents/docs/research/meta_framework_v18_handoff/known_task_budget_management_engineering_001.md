# 已知首题预算停止管理器：有界离线工程收尾 001

结论：本管理器的有界离线工程检查通过，**accepted / not applied**。实际 campaign 未应用管理步骤，未创建实际管理 scope/admission，未 resume，未启动后三题，也未延长原时间窗或增加预算。此结论不等于整个元框架、实际研究成功或付费准入通过。

实现只在 experiment_traces/meta_casebank_budget_management_v18。[budget_management.py](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_casebank_budget_management_v18/budget_management.py) 最终 SHA256 为 fad68b9f70fcc9584a4c993c51b324dad6252429c592cb75aa9acdc9a2a6c53c；作者测试为 94b804369a7ad9d5863822d5b4efd01f1c6b41afbd2f31bb438fdc90abe4727a。冻结 v18 的 core/phase/correction 源摘要逐项仍匹配原值；本次没有修改它们。

公开接口为 prepare(campaign_root, *, scope_id, provenance_pins)、execute(scope, *, admission: bytes, expected_admission_sha256)、read_committed(campaign_root, *, scope_id) 与 manager_sha256()。prepare 只读并冻结管理器源码、原 campaign/plan/phase/scope/admission、精确 pre-state、原顺序首题与第二题、全部旧表行与结构摘要、四个工作台数据库字节摘要和显式 provenance。execute 要求外部原始 admission 字节 SHA；该许可必须同时写明 local management true、dispatch false。不是通用任务跳过器。

首次管理须在原 supplemental 起止窗内，且当前状态严格为原预算原因 paused、首题 14 queries / 12 candidates / 0 final、13 新调用均有完整保存回执、14 动作已应用、后三题保持初始状态。原只读 inspect_saved/history 核验保存证据。逐次重算 own exposure + 80000 必须严格超过 600000，而全 stage 及调用数仍有准入空间；仅冻结原 HTTP400 unknown 例外保留 80000 名义预留，不把未知费用写为 0。所有阈值、调用次数、预算和期限均继承原范围。

管理沿原 registry .dispatch.lock，在同一 BEGIN IMMEDIATE 事务登记 intent/result 并调用原 v18 _write_state。只改首题 status 为 budget_stopped、current_task 为原第二题；stage/phase 继续 paused 且原 reason 保留，第二题仍 not_started。原 calls/actions/WB/scope/admission 不变，不生成 final、弃权或研究样本。scope_id 与 phase/from_task 双唯一；提交前异常回滚，提交后外部回执丢失只能读回原 record。

已提交同 key 先验证历史提交，再处理旧 pre-state；允许后来合法追加审计和窗口过期。恢复核原来源、许可、phase 审计链中的唯一 before/after 迁移，并反转两个允许字段重得冻结 pre-hash。最新修复补核 scope.old_tables 前 N 行及原 SQL schema，保留 calls/actions/events 与 phase audit 的旧前缀；只排除两张可变治理表和 sqlite_sequence。新增合法行允许存在，旧行被改、删或表结构变化即拒绝。修复相对保存的 9f2699 版本只新增 _verify_old_prefixes 并在 _verify_committed 调用；AST 与精确 diff 已保存。

| 已保存运行 | 实际结果 | 边界 |
| --- | --- | --- |
| 作者 001 | 2 failed，pytest 667.94 秒 | 37k 夹具在第 12 新调用后已预算停，错误期待 13；属于夹具算术缺陷，不声称目标产品反例。 |
| 作者 002 | 2 failed，pytest 546.55 秒 | 合法 35k 夹具首次完整建成；旧实现确实放行过期 prepare 与自洽伪造的管理 post-hash。 |
| 独立 prefix before | 1 failed，pytest 91.15 秒 | 已提交后把最后 inspect limit 50 改为 49，联动 call/action 自 hash，原 saved 文件和账务未改；旧管理器确实未拒绝。 |
| 作者 004 | 5 passed / 1 failed，pytest 350.96 秒 | 产品源为最终 fad68；唯一失败因测试写不存在的 events 表，尚未触产品门。 |
| 作者 005 | 1 passed，pytest 107.27 秒 | 只修该测试使用原 cc._journal；AST 证明其余五测试与夹具 helper 未变，未重跑它们。 |
| 独立 after | 8 passed，独立回执 elapsed 367.688 秒 | 原预测 6 个逻辑场景、8 项参数实例；含两真实短 Python 进程争用、提交后回执丢失与后来 pause/过期重放、未知/证据缺失、数学边界及 prefix 反例关闭。 |

作者最终覆盖是 **5 项复用 + 1 项新通过**，独立 8 项来自另一次运行，不能称一次联合 14 项。所有旧失败、原源副本、stdout/JUnit/receipt 保留。[作者证据索引](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_casebank_budget_management_v18/validation/006_author_evidence_index/receipt.json) SHA 27c5a6ade2ed0578626a692947f5fd7601dd55931a77b9963df2dfe433d05296 已逐项核对历史产物摘要；[独立 after 回执](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_casebank_budget_management_reviews/independent_after_001/receipt.json) SHA 5629f131e62ee3f41e604f1d2456bb5e379ff39348e96d8f2caf2c4ba19c0ad0，其全部 artifact hash 与前后源码身份已复核。

合法共享夹具冻结为 225 个文件，manifest SHA 547baadcbfe0d872687dbbf0850bc2c30748ec7ce5625c016ca3ccb36d3cb661。只在 TEMP 合成根目录构建一次合法 13-call 保存链；之后测试串行持测试专用 lease，在无在途 fake process 时按同绝对路径恢复并核所有文件，不重建这条链。该恢复能力只存在测试 helper，不进入管理器。12 个相同新 slot 候选仍各收费，避免重复核计算；这不是实际首题内容或独立研究样本。合成账务手算为 13 × 35000 + 11235 = 466235 已报告，另保留 80000，暴露 546235；下一 80000 超单题帽。本工程子任务新增真实项目 CodexGateway 调用 0；工程与评审模型用量非零，未单独计量。

局限：尚未对实际 campaign 应用或签发实际管理准入。原 source/scope/file pin 是本地身份约束，不证明市场真实性或建立隔离沙箱。首次准备/事务内核验完整 WB 保存链及字节身份；历史 read_committed 允许后来 WB 合法推进，不要求当前整库字节仍等于旧快照，也不将历史费用摘要伪装为当前费用。它核历史管理证据而不授权新的工作；后续任何真实操作仍需另行授权，本轮明确不执行。固定旧文件很大时只读核验有明显开销；本轮未为了速度削弱检查。

两问自检：**是否只把已有失败归类，而没有给首题新的研究机会？** 测试证据支持：唯一两字段治理差异、paused 保持、旧调用/费用/无 final 均保留，实际未应用。**什么会推翻当前工程通过结论？** 任一重入再次推进、同 purpose 换 key 成功、旧前缀篡改仍可读作有效提交、新 unknown 被豁免、共享预算误判、管理时改 WB/调用/时间窗或默认派发，都会推翻结论。当前收尾到冻结报告并停止；不新增功能、不做真实管理或续研究。
