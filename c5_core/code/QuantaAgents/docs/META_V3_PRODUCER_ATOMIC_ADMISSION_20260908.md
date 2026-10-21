# 生产器进程预约的原子准入修复

已复现并修复 035-C02 剩余的事务间隙。修复前的两个并发顺序反例失败，修复后 9 项定向检查通过，未启动原生 Job、账户或模型。独立关闭结论仍待父任务确认；036 的原始审查保持 changes_requested，C01 已关闭、C02 待本轮修复复核。

旧路径先在 `registered_producer.dispatch` 的事务中检查完整共享额度，释放事务后，才由 `process_envelope.reserve` 在另一笔事务中写 process intent；后者仅查 process headroom。反例让外层实际检查先通过，再在 canonical SQLite 中提交同研究另一个普通 trial 的生成 model/tool 超额，随后调用真实 reserve。旧版本仍各增加一条 process intent。测试派发器只预约，不调用 OS；新 `_active` 会拒绝后续候选，这不能撤销已经允许的进程启动机会。

现在，对于有 producer contract 的 trial，`pe.reserve` 在写入 process intent 的同一个 `BEGIN IMMEDIATE` 事务内调用完整 `study_allocation.admit_tool`。它依据预约当时已提交的 model/tool/process 状态判断，不沿用外层健康状态。外层检查和实际生成的 `_active` 门保留。普通研究仍保留原 process 检查及独立的收尾政策，没有把生产器的全项拒绝条件无条件套到普通研究。

| 新范围的可区分检查 | 修改前 | 修改后 |
|---|---|---|
| 外层健康后，peer 的 model 超额 1 已提交 | 新 process intent 1，未拒绝 | 拒绝，process/producer intent 均未增加 |
| 外层健康后，peer 的 tool wall 超额 1 毫秒已提交 | 新 process intent 1，未拒绝 | 拒绝，process/producer intent 均未增加 |
| 外层及预约时都健康 | 可预约，未派发 | 可预约，未派发 |
| producer 准入检查所在事务 | 本轮未用修前版本测此附加项 | in_transaction 为真，另一 SQLite 连接不能取得写事务；随后同一事务写预约 |
| 普通 trial 在 tool headroom 耗尽时的进程预约 | 本轮未重测修前对照 | 仍可预约；未启动或声称得到模型收尾终稿 |

验证范围 `validation/producer_atomic_admission_001` 已关闭，175 个清单文件。第一轮 3 次：2 真实失败、1 通过；第二轮 9 次通过，包括上述 5 项和之前 model/tool/process/健康四种固定状态的回归。共 12 次调用、9 个不同检查最新通过，没有更改反例断言来获得通过。

这是 canonical 交错顺序和数据库锁的验证。5 条生成 canonical call 记录、3 条生成工具计量记录用于构造可见额度变化，人工构造的 known_tokens 合计 4,800,002，只存在于隔离测试库，明确不是供应商调用、模型完成回执或实际用量。原始生成记录保留在 12 个测试 canonical 数据库中。另有 7 条测试中的原始未派发 process reservation 保持未观察状态，没有变成零成本完成记录。所有 producer work intent 均未开始，新增原生 Job、账户、付费模型、真实/封存行情读取、网页查询和直接 GET 均为 0。

036 的 17 项固定材料全部未变；036 的 163 项、035 的 340+9 项清单全部保持原 hash。`process_envelope.py` 修改前副本与 036 原执行源 pin 相符，差异限定在 reserve 的 producer 分支。8 个供应商 ledger 仍为 57 次调用、1,427,801 已知 tokens、V3 unknown reserve 为 0，旧 V2 账单与未知预留继续单列。测试数字、平台用量与供应商账不相加。

本次保证的是“实际预约时已经入账的耗尽不能通过”，不预测预约之后才到达的账单，不回收已有预约，也不声称消除了 OS 轮询超调。没有重复 036 的两个 Job 或 035 的账户，也没有再次宣称完整账户路径通过。正式成功分母仍为 0；full_stack、逐调用模型身份、真实来源/公司行动、执行容量、跨案例独立重复、冻结样本外和新增前瞻验证仍待解决。

原计划核对历史税务出处在读取旧查询记录后暂缓，本轮未发起新的来源检索，也未重新打开旧来源范围。下一步先由父任务核对本次原子准入反例和关闭条件，再继续未解决的真实研究准入工作。
