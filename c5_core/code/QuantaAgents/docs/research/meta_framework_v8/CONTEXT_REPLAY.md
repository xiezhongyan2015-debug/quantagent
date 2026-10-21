# V8 三阶段上下文独立重放

2026-09-09：直接调用当前 `protocol.build_context`，用旧真实调用保存的阶段状态、因子和训练账户证据重放；没有在旧研究路径创建研究内核，旧 SQLite 只读，所有新增 evidence 写入 `output/research/meta_v8_20260909/context_replay`。模型调用、账户执行和新市场数值读取均为0。证据、原 prompt、配置、SQLite 与 WAL 前后哈希一致。

| 真实阶段 | V8 最终整包 UTF-8 bytes | 附带因子/交互/相关性 | 均值与支持字段逐原件核对 |
|---|---:|---|---:|
| 第一轮选择 | 28,405 | 12 / 6 / 66 | 154条一致 |
| 第一轮四臂复盘 | 30,553 | 已用3 / 触及4 / 30 | 76条一致 |
| 已复盘后受限修订 | 31,289 | 12 / 6 / 16 | 104条一致 |

三阶段均低于冻结32,000 bytes。第三阶段的其余50条相关性由 `detail_in_full_evidence` 明确索引；12因子、全部6交互及24条件条目恢复，没有因边际IC低而删项。第一轮复盘只附已用F1/F2/F7和触及配对，是阶段范围缩小，不是宣布其余因子无效。所有附带support引用均可解析，observed/calendar/missing/paired/rank-deficient字段与旧报告逐值一致；覆盖支持并不等于独立样本数。

第三阶段显式注入了一个V8合法revision_plan，属于反事实接口重放，不能称原模型已提出该计划。计划绑定第一轮omit_2；实际 `freeze_revision_plan` 和 `derive_revision` 保留top_n=40、score和gate，仅改weighting=equal、risk_score=null，软件无需get_evidence查询，模型无需重新输出父规格。冻结计划实际附在 `paired_results[].review.revision_plan`。

本次重放先发现review 32,614、next 34,168超限；第一次去重后next仍32,099。原失败报告与原rejected_prompt保存于 `context_replay_before_context_dedup` 和 `context_replay_after_first_context_dedup`。最终通过来自共享控制说明、已复盘控制信息去重和V7/V8重复阶段合同合并，不应回写成从未中断。

人工核对最终V8合同：仍声明因子先于组合、账户后必须复盘、冻结一个已完成原规格、解锁后不得再造训练候选、validation复盘关闭、保留最终调用及closing_only限制；查询/注册/表达式/控制/扩展动作由相同BASE_CONTRACT提供。新增revision_plan和revise_batch的允许路径及父运行继承语义完整，未知IC与风险/仓位未识别边界仍明确。没有因为合并合同而取消阶段规则。

最低剩余空间711 bytes，仅证明这三个保存案例可运行；更多因子、更长原复盘或更多账户仍可能触及上限。此结果属于工程与旧真实证据接口验证，不证明一般token节省、发现能力提高或金融收益。

可复现入口：`.venv/Scripts/python.exe scripts/replay_meta_v8_context.py`。机器结果：`output/research/meta_v8_20260909/context_replay.json`，含当前实现哈希、所有来源、完整生成prompt和历史失败位置。
