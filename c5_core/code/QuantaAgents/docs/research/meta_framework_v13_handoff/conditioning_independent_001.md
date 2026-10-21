# v13 条件分组控制器独立工程审查 001

2026-09-07，Asia/Hong_Kong。结论：**在当前“暴露合成数据、本地离线工程适配器”范围内通过，未发现新增阻断；不能据此宣称已接通模型研究工具、具有历史 PIT/OS 隔离或形成策略成功。** 独立审查者为 Astra/xhigh，与模块作者分工；这不是人工无模型审核，也不是同信息、同预算的研究参考复现。

审查依据为完整实施提示词、[v12 两阶段纯核合同](../meta_framework_v12_handoff/event_conditioning_001.md)和作者的 [v13 控制器说明](p2_controller_001.md)。审查只读作者模块、专组和继承 Store/纯核；新增本报告及 [13 项独立反例](../../../experiment_traces/meta_ashare_revision13/tests/test_meta_conditioning_independent_v13.py)。未修改任何产品源码、作者测试、旧运行或原始输出。该测试文件仅使用已有公开工程 toy 和临时目录，禁止进程与网络调用。

## 核验结论与证据

| 要求 | 独立证据与判断 |
| --- | --- |
| 计划先保存、后读取 outcome | reader 内通过另一只读 SQLite 连接看到 `outcome_read_started`，全部 12 个子项已预留；计划文件字节 SHA、大小、binding 和请求 commitment 一致，原配对固定为 T1/C1、T2/C2。reader 前自洽改写配对及 plan hash 仍因原注册字节不符被拒，读取次数保持 0。通过本适配器程序顺序核验；没有证明此前其他观察者未见结果。 |
| 共同数值环境及身份 | candidate 包自行冻结不同 h5 端点，完整源 hash 合法仍被共同数值 hash 拒绝，未进入汇总；只把 controls 身份换成 baseline 同样失败且不产生 result。两侧正常输入得到相同 curve，并保留各自 scope、binding 与本侧历史。 |
| 失败、重复与公平预算 | 无法定义的分组失败预留 96 个 unresolved 子项；新槽重复该失败仍占 96，实际 plan 构建总计 1、reader 0。第三请求被预算拒绝并保存原规格，candidate 的独立预算仍可用。相同旧槽只恢复，不重复占用。名义槽不是已算出的 96 个研究结果。 |
| 并发去重与未知阻断 | 第一连接停在 reader 时，另一连接竞争同槽、同侧新槽、另一侧新槽均被挡；只一次 reader、只一请求入账。结束后相同槽能按原保存证据恢复。覆盖的是此 controller ledger，不是全项目或供应商账户调用锁。 |
| 原始产物绑定与 saved-only | receipt 完整保存后、结算前注入崩溃，重新 load 后在两个 kernel 均被禁止的条件下恢复成功；重复恢复不增加请求、预留、构建、汇总或读取，原 JSON 字节不变。分别缺 plan/outcomes/result 时，完整 receipt 也不能授权恢复或另一侧继续；没有自动重算、补造或覆盖文件。 |
| 输出范围与相依性 | 两侧 history 不含另一侧 observation ID 或产物名字；独立样本量仍为 null，研究样本、项目 Gateway 调用、回测新增均为 0，`formal_target_success=false`。原作者缺 h10 不重配检查及 v12 固定共同 [1,5,10] 核仍保留，本轮没有重跑相关全套。 |

`_save_artifact` 先在 Store 的事务中登记预期字节身份，再独占写文件、flush/fsync。登记后写入中断会保留未知，不能凭后来文件自算的 hash 取得信任。Store 的 `BEGIN IMMEDIATE` 是当前并发准入依据；本轮没有另写锁实现或改 Store。

## 需要准确使用的边界

1. `common_outcome_values_verified=true` 仅说明指定数值投影一致。独立异身份反例中它可以为 true，而动作终态仍为 failed；它不能单独作为研究有效或身份合格的判据。
2. `summary()` 汇总已登记的状态和计数，核查冻结包/源码，不逐一重新读取全部 outcome/result 文件。已经完成的产物后来缺失时，`recover_saved`、`public_history` 和后续 `execute` 会拒绝，但摘要仍可能列原 completed 计数。这保留历史终态，并非当前完整证据的再认证。以后接 harness 的完成门应消费已核验的保存结果，不能只看 summary 的计数或共同数值标志。
3. 可信 controller 仍负责选择自身 arm、固定 policy hash 和 reader；Python 对象不是权限沙箱。本模块不能认证提前暴露、任意同用户进程改库、历史来源到达或真实市场 PIT。`chronological_commitment_verified` 与 `historical_PIT_verified` 保持 false。
4. 当前计数只覆盖本工程工具请求、名义子项和实际计算开始次数；不等价于模型 token/费用或全项目研究预算。不得用此本地适配器绕过未来原子全局预算和模型调用意图。

以上边界已与作者和 root 沟通，本次不要求修改冻结实现。未来 live 接线若把任一弱标志当成功门，应作为新阻断处理，而不能追认本次检查已覆盖该集成。

## 验证及冻结

仅运行 v13 专属独立文件，`PYTHONPATH` 指向 v13/src，禁用 pytest 插件自动加载与字节码写入：**13 passed in 6.32s**，退出码 0。作者另行提供的 28 项通过未在此重复执行，不能与本轮数量相加冒充一次正式联合验收。root 后续联合结果另行记录。

| 文件 | 本轮核对 SHA256 |
| --- | --- |
| `meta/conditioning_controller.py` | `3d1d4b6de734ed672247dbf001a8dd94f646e12e4e28c246e2c0c2d7c1a326d9` |
| `meta/event_conditioning.py` | `f06a0038fe09cd7a5a31158a08100febc7d00e0a7f8401f8b01dcc60e07ebaef` |
| `meta/store.py` | `a722ca5256125f3eb10cedce51e3324f9eea8740672a6152597c795c3e2e9929` |
| 作者 `test_meta_conditioning_controller_v13.py` | `127cb0d42fd999ee54bf5d395afa6d0c9fa11b7eb89665af4ee0662e41a49faa` |
| 独立 `test_meta_conditioning_independent_v13.py` | `ff9cb608c0dbefdc0640885665f128d20db0a89bf5160eac17f426342b146a41` |

两问自检：本轮通过可触发的独立反例检查了持久顺序、同数值及跨侧身份、未知阻断和恢复幂等，未发现需修改冻结模块的实质缺陷；仍没有把它接入真实研究循环或证明隐藏正控可达。下一步应在单独授权的集成中验证公开动作到此 controller、再回原研究预算/本侧历史的全链条，先使用相同暴露 toy 与 fake 动作。若出现 reader 提前读取、结果不同却通过共同值验证、重复漏记、跨侧历史泄漏、保存缺口自动重算或摘要被误当有效完成门，撤销相应通过结论并停止扩大。

本审查新增项目研究 Gateway 调用 0、真实行情读取 0、回测 0；Astra/xhigh 工程审查模型用量另列，项目 Gateway 账本没有计量该平台工程用量。总体目标、策略目标与架构赢家均未由本报告判定。
