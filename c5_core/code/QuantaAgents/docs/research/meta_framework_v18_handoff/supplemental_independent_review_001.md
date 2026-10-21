# v18 补充时间阶段独立工程复核 001

结论：**在本次合成、单补充阶段、同预算、保存证据恢复范围内有界通过。** 截止竞争导致费用投影不可读的真实反例已关闭。这里没有签发实际 scope/admission，没有打开实际账本或市场行，没有新增真实模型或实际工作台动作；实际准入仍需另核具体冻结材料。评审沿用 Astra/xhigh，工程模型用量未单独测量，不能计为零。

本报告只覆盖 continuation / supplemental 的控制与账务边界。监控有另一套独立源、测试和报告，不能将 GUI、核心 20 项及本次定向复核称为同源同次全量通过。

## 已保存证据与版本

以下产物位于 `experiment_traces/meta_ashare_revision18/`。本次最终保存证据复核为 `supplemental_independent_validation/005_final_saved_review/receipt.json`，SHA256 **ca24ba57beb0311147fb665f3b2ea8dcd14c646ec4e7b9759eecf8cf57bb90d1**。它逐项核对原件、JUnit、源码前后 SHA、修复 AST 和两次观察记录，没有重跑测试。

| 证据 | 实际结果与边界 | receipt SHA256 |
| --- | --- | --- |
| `supplemental_validation/root_joint_001` | 作者 16 + 独立 4，20 passed，272.172 秒；phase 为修前 305171…，core 为 6e38… | `a9a2d76961012df8c7816df04d1062486b6ecbf32db505fa6e7fd8ea6e0f78b4` |
| `supplemental_independent_validation/003_joint_saved_review` | 只读核 20 项及默认 hook、旧源依赖差异；当时 deadline 反例仍开放 | `0d316696d36e3e978e4af82fd30a379f79986694377f851491a6d59f4637c9a2` |
| `supplemental_independent_validation/001_deadline_before` | 原竞态真实 1 failed；0 模拟进程，但 summary 抛错、治理未正常停下 | `842da9ad445c322f8092f8d27643e6aa993633049af8eeb489984bf832398d97` |
| `supplemental_independent_validation/002_deadline_after` | 修后 2 passed / 1 failed，67.25 秒。原竞态与作者正常窗后收尾通过；新增测试自身时间精度断言失败 | `552bbd13e9ee233794dc503da6e2437cc05288ebe3f7e6ef9e03c73a0d12eadb` |
| `supplemental_independent_validation/004_timing_precision_after` | 仅修测试的一处 expected，单项 1 passed，26.50 秒；产品源码完全不变 | `daa40c0c38501caaa10aef02a0be61a8f3d96f4d1a9de51cdc5f4ddbf55efd3b` |

所有运行均保留源副本、日志、JUnit、前后 SHA 和短临时目录。002 的失败没有删除或改成通过：已保存 ISO 时间为 `1788776184.852441`，原断言错误比较序列化前浮点 `1788776184.8524408`。004 仅将 expected 改为对实际保存 ISO 字符串作标准库解析，未改变时间窗、费用、产品实现或待检门。

最终固定核心源：`casebank_continuation.py` SHA256 `6e38e602a1850930cd5bb2fe3d43f7f6a7b35e4c4fe62953506e759de04f9919`；`casebank_supplemental.py` SHA256 `83e04e8875fe4ebf85ec5ab6ad931545cac8934e2f6cd90ae91d6993475aa392`；入口 `run_casebank_supplemental.py` SHA256 `429e0d7d3b191028f32545aaeabc6007e6972583d3a3049b13fcf949d5c44da9`。作者 16 项与独立 4 项测试未为本次修复而改写。

## 控制、预算和恢复判据

独立四项使用实际两个 Python 进程或临时 SQLite 合成故障，覆盖：并发 prepare 只登记一个补充阶段且不同第三窗口被拒；两个进程同时争用首题精确累计 171235 上限只成功一笔第三传输预留；重封的新 phase/source/admission/task/伪旧身份均被只读投影拒绝；模型完成及 WB 已提交而治理未结算时，恢复只导入已有结果，重复应用不增加工具执行、费用或审计序列，旧模型/动作前缀不变。

作者 16 项另覆盖原 paid=false、原默认到期门、真实冻结 v17 迁移、窗口前后、名义预算上限、新 unknown 全停、内存字段变更、旧进度逐事务审计、审计失败回滚和原保存观察幂等。原保存回答未应用前禁止下一 prompt/reserve 的 `_history` 保持原 AST；全套旧控制器测试没有机械重跑。

v17 原内核 SHA `6cc0dc07cb60ac3f8f8b507b911c028a85a82305283132be4432a9b401b19b86` 未改。v18 增加显式资源 hook，原 `_scope_base` 及累计传输上限、名义暴露上限、新 unknown 禁继续、同语义轮不重试的关键 `need` 表达式 AST 相同；Store、Gateway、transport correction 与 v17 对应依赖逐字一致。不是整个新旧内核完全等价：已有七个方法接入 hook，另外七个 hook 被新增；具体名单在 003 回执中冻结。

补充窗口增加的是明确的新时间资源。它不把原六小时延长为同一成功阶段，不重置原每题 17 / 全阶段 68 传输、每题 16 查询 / 12 候选 / 1 终稿、60 万 / 240 万名义预算及 80000 单次预留，不增加四任务分母；旧许可 paid=false 原字节保留。阶段登记许可与“此刻可派发”仍是不同事实。

## 截止修复实际结论

原竞态在窗口内登记 reserve，随后于持久 `dispatch_started` 边界将合成时钟推进至 end + 0.01 秒。修前预检阻止了进程，但投影又把保存的越界时间视为不可读，导致调用失败、80000 新未知已记录却 summary 抛错。修后同一原测试未改：**模拟进程 0，新调用 failed，两个未知预留合计 160000，已知 11235，累计暴露 171235；summary 可读，治理和补充阶段均 paused。** 记录的真实边界时间没有被归一化回窗口内。

新增单项先生成一份纯 fake-process 完整回答，再仅在临时账本重封 `dispatched_at` 为越界值。这不是对真实历史的修改，也不声称该构造是实际模型事件。费用仍为 **11248 已知 + 80000 原未知 = 91248**，完成状态与注入的时间原样保存；`timing_violation` 和 `timing_admission_blocked` 明确为 true。显式 metadata resume 后即便当前合成时钟仍在窗口内，新模型门和首次本地应用门都因既存违规拒绝；窗后也拒绝，工作台维持原一次查询、零终稿。记录 ready 本身不等于可派发。

作者原正例直接选测通过：在窗口内准入且派发、窗口后才返回完整回答，只有显式授权的本阶段绑定完成调用可作本地收尾；原 correction 不能冒用该豁免，下一模型仍停。该正例在原 20 中已经运行，这次仅因修复涉及其条件而定向重验。

独立 AST 核验确认修复只改变 `_call_window`、`read_supplemental_projection`、`SupplementalStage._open_phase`、`SupplementalStage._authorize_local_start`，新增 `_timing_violations`；移回这四个原节点并移除新增 helper 后，其余语法树完全等同修前。读取可保留越界记录；新模型/本地动作必须拒绝既存违规；窗后完整调用的豁免额外要求窗口内派发。原 20 项因此只按此精确差异衔接，不能声称它们都在最终 phase 源上重跑。

## 两问自检与准入限制

1. **是否通过改分母、消除未知或隐藏失败来得到通过？** 没有。原产品反例、独立测试精度失败、旧 80000 未知及各次版本全部保留。原竞态 171235 和完整回答 91248 两条账务路径均直接核验，独立新测试没有修改实现。
2. **这个结论是否足以直接恢复实际付费或认定框架稳定？** 不足。本报告只给明确范围的工程通过；实际 scope、原保存状态/备份、来源和新绝对窗口须另审，新授权不能回填旧 paid=false。模型真实供应商身份、策略能力、收益、跨题发现稳定性和实际浏览器运行均没有在这里得到认证。若实际冻结 SHA、预算域、旧前缀、未知状态或时间资格不符，应拒绝实际准入，不补派发解决。
