# 单次运输纠错控制器工程记录 001

实现依据为冻结的 [transport_rejection_accounting_design_001.md](transport_rejection_accounting_design_001.md)。本阶段只新增 v16 `meta/transport_correction.py` 和专属合成测试；未构造、读取或写入实际 campaign，未创建实际许可或执行 Gateway 研究。本模块不改 Store、原 campaign/live/Workbench/Gateway，也不启动完整循环。

公共接口为 `TransportCorrectionController(campaign_root, *, permit: dict, admission: bytes, expected_admission_sha256: str)`，构造只读验证；`.budget` 为调用方读取的拷贝，内部准入仍核对原绑定预算。`summary()` 只读同域聚合；`execute_once(gateway=...)` 要求显式注入并在原 registry 派发租约和原 SQLite `BEGIN IMMEDIATE` 内消费唯一许可及新预留，原 runs/calls body 不更新。`inspect_saved()` 只读取和核对已提交完整回执，不启动模型或工作台、不隐式结算缺失提交。

GUI 专用 `read_correction_projection(db, *, run, plan)` 只 SELECT 既有连接，缺表返回空列表，不读外部路径。输出原 plan/task 身份、ordinal=2 / semantic round=0、阶段时间、usage 和原 80,000 之外的新预留。`status=completed` 表示有完整供应商形状事件及费用回执的本地核验；`original_schema_status` 才表示原精确 JSON schema 校验结果，`workbench_validation=pending_not_performed`，始终 `public_action_applied=false`。兼容字段 `semantic_status=valid/rejected` 也仅指原 schema，不代表已通过工作台跨字段/机会规则。格式拒绝仍结算真实已报告的本次费用，但不显示可用研究动作；原未知 80,000 不释放。

许可模板只存在于纯合成 `make_fixture(tmp_path)`，没有生产 ready=true 生成器。controller 接收原 admission JSON 字节并核外部 SHA，permit 另核内容 hash，避免循环绑定。原 prompt 字节必须不变；wire 唯一差异是删除 horizons 的 `enum=[[1,5,10]]` 并加入长度三，其他内容严格相等；成功返回仍按原精确 schema 检查。原10模块来源与新 controller/Gateway/profile/锁/预算5模块来源分开绑定，不迁移原工作台。旧源码、全部原请求文件、原 plan/call/run、四个空工作台和关闭付费的 checkpoint 均进入核验链。

第一次专组为 25 passed / 17.20s，原始 stdout 和 JUnit 保存在 `transport_correction_validation/author_001`。随后增加完成回执/费用/请求投影互锁及内存许可冻结的四项反例；最终专组 **29 passed / 20.65s**，0 failures/errors/skipped，原始 plan、stdout、JUnit、receipt 保存在 `transport_correction_validation/author_002`。两次结果不相加；本组没有失败输出被覆盖。第二次源码和测试在执行前后哈希一致。

| 最终作者产物 | SHA256 |
|---|---|
| transport_correction.py | `0e21009f00f98e81d0105dd4a98583253dccf1f63115e469b855be1ad53bb657` |
| test_meta_transport_correction_v16.py | `0a9146eabf3e97d86c94fa0b125cdea26ea1b38f8d6fe7d0dc9ab6799eb9eb1c` |
| author_002/receipt.json | `7d8df8ec6796dfda6e04733e4733b7cafd6a26a7fa0d679db315e8f129438024` |

测试覆盖首次只读无表、同库旧 body 不变、成功后停机及只交付原回答、原 schema 拒绝但已知费用保留、四处崩溃后不重发、partial/returned usage、不足预算及原时钟、部分答案/错误400/未知退出/改 prompt/额外 wire 差异拒绝、双实例真实 SQLite 租约并发、暂停前派发拒绝、保存文件改动和自洽改表降费反例。全为临时合成复制账本和 fake process；没有市场输入或实际原 campaign。

边界：原 80,000 是名义保留，不是供应商费用上界；六小时是原时钟的下一次准入门。被 pin 的 Gateway 恰好在 Popen 前、safety preflight 前后调用 cancelled 两次，controller 在这两次核原控制状态和许可时间，其后的在途检查只限制单次超时，避免一般暂停打断正在进行的推理。更换 Gateway 时必须重新审查这一调用顺序，不能只改 hash 跳过验证。任何 reserve 后崩溃都消费纠错机会，哪怕尚未实际 Popen；缺回执账务提交时仍返回 `unresolved_saved_only`，不自动恢复派发。SQLite 哈希和 Python 接口不是防恶意重写或 OS 隔离。

两问自检：已补齐什么？一次明确协议修订的同域预算、不可复用许可、完整原件绑定与费用/回答格式分轴，保留原失败和新失败。尚未具备什么？独立反例评审、最终组合验收、根代理具体生产许可及 GUI/控制权门尚需完成；本工程记录不授权付费，不证明远端已接受新 schema，不把格式通过当工作台合法动作或策略成功。工程模型用量另列，`execution_valid=false`。
