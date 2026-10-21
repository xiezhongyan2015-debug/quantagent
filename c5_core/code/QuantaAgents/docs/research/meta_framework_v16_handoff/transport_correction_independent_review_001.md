# 同预算单次传输纠错独立复核 001

结论：**本次有界离线工程验收通过；具体付费准入仍须另行冻结。** 最终受影响组合实际为 **49 passed / 34.38s，0 failed、0 error、0 skipped**：controller 作者 30 项、独立 7 项、monitor 11 项、真实 CLI/controller 加假进程整合 1 项。scope_001 的原提示词、唯一 wire 变化、预算和原计时起点已独立核对；它明确 `paid_dispatch_permitted=false`，不能当作派发许可。

正式评审沿用 Astra / xhigh。新增研究 Gateway 调用 0，供应商 API 探测 0；只使用临时合成账本、公开算术型测试对象与假 Gateway。两个真实子进程仅争抢合成 SQLite 的 `_reserve`，不含 Gateway。未读取行情、运行真实四题候选发现或回测，未修改实际 ledger / 原调用。工程评审模型 token 计量未知，未写为零。

## 具体 scope 与设计

- 设计 `transport_rejection_accounting_design_001.md` SHA `760044c8aed03c1edc8cb5f1236c8c250bf31c1cab1b4bd40552426ee5fe8024`。
- scope `experiment_traces/meta_transport_correction_preparations/reference_v15_original4_001_scope_001/scope.json` SHA `349eddbd9771ccb2ebd860d3454ef35104a53f39582ec0aaddaacf618a42c970`。
- 原提示词与 scope 内副本 **9,919 字节逐字节相同**，SHA `08f715a27ab3c14c5dea62e80991e6215a83dba5ba57953be5e8227d74df73bb`。没有新增评审提示、私有答案、旧回复或其他题上下文。
- 新 wire SHA `c297833fc1e9d0b2a8fb15da4336982d9db44732946af1856db2c58a47a22d01`，独立重建比较确认仅删除 `horizons.enum=[[1,5,10]]` 并加 `minItems=maxItems=3`，其他 JSON 内容相同。原完整 schema 继续检查返回值，不对 horizons 排序、补齐或去重。
- scope budget 与原 campaign plan / run budget 完全相同。只读 SQLite 核到原 `created_epoch=1788728483.6826499`，六小时截止仍为 **2026-09-07 03:01:23.682649 UTC**；等待、修复和暂停不重置这个起点。单次超时仍为 3,600 秒，整体截止是下一调用准入边界，不是精确供应商账单帽或该时刻必杀在途进程。
- 原 campaign manifest 与七个调用原件 hash 均匹配。只读收口时实际账本仍一笔原 failed、四个 usage 字段为 null、80,000 预留、原 paused run body 不变，未出现 correction 表。四题描述性分母保留，纠错不增加正式策略成功分母。

以上证据在 `experiment_traces/meta_ashare_revision16/transport_correction_independent/001_scope_check/receipt.json`，SHA `c9e1be3ef974601d1541e9eb6e3da44ebe4b67064b34d663a2ebea2929ad8fe8`。本报告没有复查真实进程存活，也没有审阅或生成可执行的实际 paid admission。

## 同域账务、一次性机会与恢复

controller 使用原 `ledger.sqlite3` 的新增表，`mode=rw` 不创建缺失账本，保留原 runs / calls 文本记录；读接口使用 `mode=ro`，不构造 Store、Workbench 或 Gateway。原 registry `.dispatch.lock` 仍覆盖单次派发过程。许可消费、不可变请求意图和新预留在同一 `BEGIN IMMEDIATE` 中提交，唯一原 run / 原 call 约束拒绝第二笔纠错。没有另建预算域、覆盖原计划或清除原未知费用。

原一次失败加新一次传输计为 **2/17 任务次数、2/68 阶段次数**；原 80,000 加新 80,000，派发前至少 160,000 名义暴露。已知 input/output 额外计入，cached input / reasoning output 不重复加。完整且核验通过的新 usage 只能结算新预留；即使新返回值未通过原 schema，新传输仍按完整 usage 结算，原 80,000 保留。成功、错误、超时和未知均停止，不进入普通研究循环。

独立双进程反例让两个进程先同时完成构造，在屏障后直接争抢同一 SQLite reserve：精确一笔成功、一笔 consumed 拒绝，最终两次传输、160,000 预留，未创建请求目录。这个反例单独验证事务与唯一键；作者的线程 Held Gateway 反例验证正常入口的独占 dispatch lease。二者均不证明供应商端幂等。

一旦 reserve 提交，许可就已消耗；尚未启动进程而崩溃也不重新派发。独立新增“完整 receipt 文件已写、completion DB commit 尚未发生”反例：保留新旧两笔 reserve 和已知 13 tokens，`inspect_saved` 仍为 `unresolved_saved_only`，重复只读检查不结算、不交付未提交回答、不调用工具；重入仍拒绝。作者组另覆盖 reserve / dispatch / provider 返回各侧崩溃、第二 unknown、缺 usage 和文件被改。

旧普通 controller 不获得续行资格。独立测试在新纠错完成结算后，使用同一合成库原 calls 执行旧 `CasebankResearchCampaign.resume` 的真实未知费用门；即使前面的其他身份/控制检查已满足，该门仍拒绝，未到写入。它是对该门的聚焦验证，不冒称已运行完整旧 campaign 迁移。

## 派发前停止与返回状态边界

reserve 和 mark_dispatch_started 两处重核原 run / call / source / budget / clock / 原工作台状态。固定 Gateway 的两次启动前 cancellation 检查分别位于 safety preflight 前后；新 controller 在这两个检查点再核原 run hash 和 permit 到期，之后仅按单次 timeout 处理在途请求。独立使用真实 Gateway.run 加假 preflight，分别在第二检查前追加暂停状态和越过 permit 时钟，均未到 Popen，已消费的新机会和 160,000 预留保留。这一行为依赖已 pin 的 Gateway 调用次序；更换 Gateway 时必须重新审查该接口约定。

原错误资格严格限于这一个保存终态：固定请求/意图/退出绑定、单个 schema HTTP 400 / turn.failed、没有答案、工具、usage 完成或额外原件。作者反例证实，即使把合成输入重新封 hash，部分 agent_message 加 400、其他 400、未知退出、改变提示词、额外 wire 改动或原 usage 已知，仍不能套用例外。它没有把 400 解释成零账单，也未孤立供应商拒绝的唯一关键词原因。

本次只验证原 JSON Schema，**不进行完整 Workbench 跨字段/机会语义校验**。公开字段 `original_schema_status` 与 `workbench_validation=pending_not_performed` 已将两者分开；历史内部 `semantic_status` 仅保留 schema 意义。`public_action_applied=false`。独立用 schema 允许但未校验工作台 payload 对应关系的返回值验证此标签；monitor 另用 `submit_research_report` 配空 reports 验证它不被计成任务终稿。严格原 horizons 足以维持本次 wire 修复的返回约束，没有偷偷调用 Workbench._validate / execute。

`inspect_saved` 只重新核验已提交 receipt。独立重复检查时将 Gateway 构造、Workbench.execute、reserve 和 update 全部设为禁止，文件与账本记录均不变。完整文件但没有结算 commit 的情形保持未解决。任何首次应用动作或后续研究仍需另一次明确管理准入，本 controller 无此权限。

## 已保存的实质失败及修正

独立发现并先保存 **1 failed / 5.18s**：Held fakeGateway 中 `status=running`，projection 却恒给 `controller_stopped=true`。作者最小改为依据本地 `dispatch_state=='stopped'` 报告，另列 `loop_continuation_authorized=false` 与 `worker_liveness=not_verified`。运行中不再宣称已停止；记录终态也不冒充 OS 进程探测。

同轮还补强独立 projection 的 budget / created_epoch 交叉校验。真实 dispatch 的 `_check` 原本就严格检查，GUI 单独 SELECT projection 时也必须对齐 permit、plan、run，不能依赖“controller 曾经检查过”。新增作者反例覆盖此门。上述收口相对独立修前源码仅改变投影；`_check/_reserve/_update/execute_once/inspect_saved/summary` 的 AST 比较不变。

| 证据 | SHA-256 |
| --- | --- |
| `transport_correction_independent/002_initial/receipt.json`：最初独立 6 pass，旧 `0e210…` 源码 | `91f8f69d185f4b841e24425d70efbadce4a998c91fc258a3df6b447d9aafed7e` |
| `transport_correction_independent/003_projection_before/receipt.json`：运行中状态 1 fail，附原源码/JUnit/log | `21f89d2c85af276b37ae16f8e16f49a84fcd99940c2b7d5cf5f3500fd55bed6e` |
| `transport_correction_independent/004_final_joint/receipt.json`：最终 49 pass | `7c3267bd73a9b88c6e7acceace8677b8fe3e4762c025c36eff1359c062ecc3e1` |
| `transport_correction_independent/005_readonly_closeout/evidence.json`：原 run、历史收据、最终输入和 AST 核验 | `986c37cfb7ef4f0c04a172133472946369273fc62595620e917ce8cba9321624` |

上述路径以 `experiment_traces/meta_ashare_revision16/` 为根。最终 49 的 16 个源码/脚本/测试输入运行前后 SHA 全稳，JUnit 分组精确 30/7/11/1。作者旧 29、独立旧 6、修前失败、CLI 原 6 与旧整合 1 的各原始记录保留，不相加冒充同次运行。未重复 schema 51 或 JS 10；它们若被用于总体验收，应由根代理明确其原版本和覆盖范围。

## 薄 CLI 与最终身份

只读检查 CLI：双输入 pin 均要求非空 64 位 SHA，先于读文件与 controller 构造；admission 原字节交给 controller；入口自身 SHA 另受 admission 约束。只有 execute 在明确 native path 与 binary hash 匹配后构造 Gateway，单次 timeout 来自原预算。status / inspect 没有派发构造、retry 或 resume 入口。最终组合包含真实 CLI/controller 配假进程的 1 项整合；原 stub 6 项只证明参数与选择边界，不算 transport 实现验收。

| 最终文件 | SHA-256 |
| --- | --- |
| `src/quanta_agents/meta/transport_correction.py` | `5fa687acd947870d4b4fd2896a1a693e6f1e3de9c8a28614336e82ffbff0d3f1` |
| `src/quanta_agents/meta/diagnostic_monitor.py` | `1fb2d796a13305a7fb8ef86de9fc30f6aad41015ef5cbad065176af8eed4ea51` |
| `scripts/run_transport_correction.py` | `f19128e1476c67b74394c3dc549fa50f85f33b553bb39340c9140151a577908b` |
| `tests/test_meta_transport_correction_v16.py` | `95ff395ef5815be82b3a38e402f70e366a91b58b02e32f74d2ec8c2501118f0c` |
| `tests/test_meta_transport_correction_independent_v16.py` | `256e69e9a8104a52ebb8436955eab726438a4bb23e42e373fbdb38e8d24f4620` |
| `tests/test_meta_transport_monitor_v16.py` | `a2cb6f8d19d4ba224f9292405b63a286a005d7d9ec1d0164f1ce97facd13a797` |
| `tests/test_meta_transport_cli_integration_v16.py` | `bc1f42448b410a74d934add30e63f117c0042ac7d76bc8d55095b3ad7ba19cab` |

没有发现当前工程范围的剩余阻断。根代理仍须把实际 permit / admission / 当前控制权 / 进程检查 / 真实 GUI 验证作为具体派发前的独立门；本报告只验证已有 scope 和上述源码，未把这些待办虚写为完成。actual provider identity 和新 schema 的远端接受性仍未认证；一次纠错最多得到未应用的保存回答，不能据此认定架构、策略收益或四题研究完成。

## 两问自检

- 是否借纠错删掉旧 unknown、重置时钟、增加免费调用或淡化原失败？没有。原 80,000 / 原一次失败 / 原时间起点 / 四题分母保留，新传输另计机会和预留；单 permit 在 reserve 时消费。
- 是否把 schema-valid、保存成功或监控终态当作工作台合法、动作执行、供应商认证或真实进程存活？没有。费用、原 schema 校验、工作台待校验、是否应用和进程证据分别标示，原失败和未完成状态均保留。
