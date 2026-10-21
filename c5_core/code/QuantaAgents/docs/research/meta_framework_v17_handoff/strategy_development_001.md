# v17 受限策略开发与保存执行：作者交接 001

本次完成一个实际可走通的合成工程入口：普通 JSON 策略程序改变命名因子与目标表达式结构，控制器冻结程序和完整目标表，调用原有 SavedRawResearch / 原股数内核，保存失败、成本与持仓证据，并能追加失效记忆。**开发可运行不等于真实执行有效。** 本增量所有结果仍 `execution_valid=false / formal_target_success=false`；没有接原四题研究循环、Gateway、真实行情或原 192 pending 计划。

根代理先建立的隔离 v17 来源为 258 文件 manifest `f83ef895363a95dc13f7b8ed2eb767ee46b9eaa27b893bfb69bbf720e9148ccb`。本 owner 仅新增两模块、两作者测试和一个合成 demo 脚本；未修改既有 factor/kernel/raw/Gateway/monitor 或旧修订。工程与独立评审按 root 的 Astra/xhigh 要求进行。

## 最终接口与文件

- [factor_strategy_program.py](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision17/src/quanta_agents/meta/factor_strategy_program.py) 提供 `validate_program(spec, *, public_field_contract)`、`build_targets(validated_program, *, decision_fixture, frozen_policy)`。只解释既有 `causal_factor_algebra_v1`，最多四个有序命名因子加一个目标权重表达式；不执行研究者 Python，不做参数搜索，不声称 OS sandbox。
- [strategy_development.py](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision17/src/quanta_agents/meta/strategy_development.py) 提供 `freeze_scope(*, identity, decision_fixture, raw_source_bindings, initial_cash='10000.00')`；`StrategyDevelopmentWorkbench.create(root, *, frozen_plan, controller_cursor_key)` / `load(root, *, expected_plan_sha256, controller_cursor_key)`；`execute(slot_id, request, *, expected_state_sha256)`、`recover_saved(slot_id, *, expected_request_sha256)`、`query(四字段)`、`summary()`、`public_contract()`、`export_for_audit()`。
- query 四字段为 `observation_id / table / cursor / limit`；它登记一个 query action，返回外层 action 结果，其中 `result` 是含 binding/hash/分页的 evidence page。最多 50 行；table 包括 programs、decisions、targets、execution_events、failures、memories、invalidations。游标绑定本 scope/身份/表/数据集/offset；密钥不出页和审计导出。
- [run_strategy_development_fixture_v17.py](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision17/scripts/run_strategy_development_fixture_v17.py) 无 live 模式或可替换外部路径，仅新建固定工程目录下的 append-only 合成 attempt。它引用已冻结作者 fixture helper，显式记录 helper / script/source SHA；fake actions 不是模型行为。下一次调用会产生另一个工程 attempt，本次没有为了最终元数据 hash 变化再次执行。

最终 SHA256：

| 文件 | SHA256 |
|---|---|
| factor_strategy_program.py | `0ce1ee91ade853a52755eb044b309f4a3d3e22d3275bd1b11c5f0fe0302a1da4` |
| strategy_development.py | `4cf15ec03e2551b0acc3670127089b116fcb053a55bd8a1d82c2d10411de0543` |
| test_meta_factor_strategy_program_v17.py | `94a865724b8968c05480ba30cd27cdbec5b5cf13ab6aa8c7d83bcffc5bc512a1` |
| test_meta_strategy_development_v17.py | `b0d615de2eb5d6fd176ab13dcba92bd5bf5ea7f6b8a6afe81c9d0786ed5175c8` |
| run_strategy_development_fixture_v17.py | `196ee114aa0172532e3713d5f4f96751bedca3c232ad884a2d503d92b918c411` |

## 已落实的合同与预算

最先保存的 [strategy_development_contract_001.json](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v17_handoff/strategy_development_contract_001.json)，SHA `f98609afb80a6d28e9af7e63d779647c21a45fa013e292d7dded7618c1a9d991`，保留原件。

每 scope 最多 actions 24、develop candidates 4、internal variants 4（每候选一个）、query actions 16、manual memory actions 4、raw execution intents 4。公开合成输入最多两股票、16 session、8 个公开字段；命名因子最多四个，每个表达式继承既有 4096 字符/256 AST 节点/深度 24/窗口 120。单有限 JSON 请求 64 KiB。父保存 record 1 MiB、累计序列化 payload 16 MiB；子 raw 单候选继续独立计 128 MiB、4096 records、64 event intents 等已有上限，不并入父 16 MiB。这里是可序列化产物/记录预算，不能解释为 SQLite 页、索引和文件系统开销也严格不超过这些字节数。

scope 创建后 600 秒只控制下一 action/子阶段的准入，不是已开始计算的硬中断时长。矩阵、表达式和内核各有有限界限。summary 按 action 给出已知阻断原因，不把允许动作解释为 worker 活着；精确请求/字节/身份仍在事务内核验。

先持有该新 scope 的非阻塞进程/线程 lease，再在 BEGIN IMMEDIATE 内核对状态并登记请求与候选/变体。三个子阶段 `compile / target_generation / raw_mechanical_execution` 一次登记 requested；未运行、失败、完成与复用分别保存。有限超长请求保存原 hash/bytes 的有界 envelope，并消费已识别动作机会，不接受伪造 envelope 重放。

同 slot 核同原 hash/bytes，只读取保存证据；新 slot 重复已知成功或失败仍消费候选和三个 requested 子阶段，实际计算为零。新动作复用已完成 raw 结果时核验原保存子证据，并检查 source 文件 bytes 是否仍与原 pin 相符；不重新计算。未知 action 阻止任何新 slot，不能通过改 slot 或换参数获得机会。

每个字段/资格有 effective_at、available_at 与 evidence ID。信号日固定 15:10 截止，晚到值在原行保持未知、不历史回填；完整日期轴不删除缺值/停牌 session，嵌套 rank 使用对应历史行资格。有限非法权重或同日总和大于 1 拒绝整候选，不归一化。缺值/不合格输出显式零目标与原因。完整 targets 恰为股票×calendar[1:]，首日全现金、恰好下一 session 开盘执行；末个 trade session 强制零，并保留原建议，最后 calendar 行无后续交易的 raw 信号另存。

原价执行仍只经 SavedRawResearch，完整本金、费用、整手、T+1、容量、公司行动义务、拒单和缺持仓估值都由原内核处理。目标为零不保证卖出，原未知义务不会被新适配器补成完整。记忆区分 `engineering_fact` 和 `author_claim`；失效理由是带本侧证据的作者声明，不能把机械失败或低统计功效自动写成预测机制不存在。

## 原始失败与修复证据

所有下列 attempt 保留 stdout、stderr、JUnit、输入/结束 SHA 和来源副本，没有改写成通过。

| 回执相对于 `experiment_traces/meta_ashare_revision17/` | 实际结果 | receipt SHA256 |
|---|---|---|
| strategy_development_validation/001_author_first/receipt.json | 首轮作者 35 passed，7.14s | `2b0bc494acc4b60c318a12e54750214d3c5973dab94ca75f3ba6383ad17195d9` |
| strategy_independent_validation/001_pure_first/receipt.json | 独立手算纯函数 6 passed，2.87s | `8f9ffcdccd953973fa3361dbd03218fb1fe4b790f1e90325ff74e57aff85b29b` |
| strategy_independent_validation/002_io_unknown_before/receipt.json | 真实子账本 terminal 两次写 OSError，1 failed，4.62s | `9c186cfa5b26ecd0847627fb84a79853ccd905df5ff70498823fec0ad6db89bc` |
| strategy_development_validation/002_author_after/receipt.json | 作者受影响组 39 passed，7.94s | `c55eafa160acf21fdac27dca6e0d03a941a22b4a201a0e5758aa79f57a2757a3` |
| strategy_independent_validation/003_controller_after/receipt.json | 独立控制器 3 passed，4.10s | `a2b1b790c152cdc79340b7c04dd968eed627e1c73203f9e42636ada3967a50a0` |
| strategy_development_validation/003_contract_before/receipt.json | 已计费的 2001 字符拒绝但未公开 2000 上限，1 failed，4.27s | `e3bef1fe5bd08fdf40b4e8efd08d0bfbca58746656ccb7ee4a0c035eb54c90a6` |
| strategy_development_validation/004_contract_after/receipt.json | 公开规则窄检查 1 passed，4.00s | `60076467b2e5a48ca2dd7ab273666c4ba7b4da6fb95d491e7a38138ac8e9dccc` |

第一个缺陷是父 catch Exception 把“子执行意图已持久、尚无 terminal”的普通 I/O 异常结算为 known failed，意外解开新 slot。修复后 raw 阶段一旦登记，普通异常仅追加 interruption，parent 继续 pending；恢复只复核子保存材料，子 terminal 未齐不结算、不重算、不派生新动作。独立修后用真实 SavedRawResearch checkpoint，并把 compiler/build/create/execute/simulator/source loader 设为 traps，已验证未知停机和完整子结果的一次幂等父结算。

第二个缺陷是公开合同遗漏已执行的文本长度和 invalidation reason 枚举。最后补丁只扩充 `public_contract()` 返回元数据；AST 比较确认模块其余部分（包括控制器类、执行/恢复/计数/预算）完全相同。原 39 个作者测试函数 AST 未变，只追加一个合同反例；纯模块未改。按 root 要求，最后只跑该受影响检查，不重复 39、独立 6/3 或整个 demo。

完整当前公开合同和精确差异见 [strategy_development_contract_addendum_001.json](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v17_handoff/strategy_development_contract_addendum_001.json)，SHA `c624d031c1f195feefaedc694d579c0058eb5a2c9bee96a912aff5f34071cc5e`。原 controller `0d578f7e15b7780025f32bcfaa637fb748142351c2a5ff674c821c950c2399ff` 与最终 `4cf15e…` 的唯一顶层定义变化是 `public_contract`。源 pin 和公开合同 hash 因此自然改变；不能把旧 scope 升级后继续冒充原来源。独立补充评审已按该差异复核，没有再跑其 9 项。

## 唯一实际 demo 与原绑定保留

唯一工程 attempt 为 `experiment_traces/meta_strategy_development_engineering_v17/attempts/20260906T225634866493Z`；[receipt.json](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_strategy_development_engineering_v17/attempts/20260906T225634866493Z/receipt.json) SHA `7e354700aa463c7d8d64bca4161726720bce73b61b8c098ce69aa0271d4d0d70`。原始启动 stdout/stderr 已保存。

它实际完成 8 actions：四候选分别为第一程序、命名因子结构变更、第一程序的新 slot 重复、非法程序；另有两次 manual memory 动作、两页 targets 查询。记录 candidates=4、variants=4、requested subattempts=12、raw execution intents=2；新 slot 复用和关闭重开后的 same-slot saved-only 都增加 0 计算。父序列化 payload 为 127,677 bytes，两个 raw 子执行分别保留完整独立 archive；一个作者声明被追加失效。

两份结构不同的程序经真实 SavedRawResearch，在全部 10,000 元合成本金上，分别产生 400 股与 200 股的买卖路径。手算 oracle 对应末期现金 `9977.84` 和 `9983.92`，包含实际合成费用与滑点损失，没有把闲置现金从分母删除。这些数值只是暴露算术验算，不能称 alpha、策略收益发现、参考模型行为或正式执行成功。普通 fixture/请求与 literal expected 分目录保存，并明确 exposed；它们不是隐藏新题。

demo 使用原 `0d578f…` controller；最后元数据补丁没有重跑或改写它。只读核验其 frozen_intent、receipt 与 frozen_scope 指向同一旧 source pins，除公开 contract 元数据对应的 controller 文件外，执行依赖均与当前相同。没有加载/迁移旧 scope，没有读取新合成私钥或重新模拟。最终元数据检查是在新的临时测试 scope 中完成，不能把它写成旧 demo 重新执行通过。

## 两问、自知边界与下一步

**是否用预制权重优化、免费失败或更宽松执行规则包装成自主策略开发？** 没有。普通程序可以改变白名单内的表达式依赖/条件/组合结构；没有自动搜索器或任意 exec。内部变体及子阶段均先登记，失败和新 slot 重复不退还，未知不解闸。核心产物确实交给原股份内核，而不是把目标权重或事件收益当净 PnL。

**现在能否认定真实执行有效，或接入原任务继续付费？** 不能。此处只有有限 exposed 合成工程；真实发布时间、原 192 pending、真实市场覆盖和策略泛化都未解决。没有模型、旧研究协议/预算接线、OS 安全隔离认证或正式源码/阶段准入。成功始终是开发机械可运行，执行与正式成功 flags 均 false。

下一步由 root 对冻结模块及连续研究控制器做来源/合同/预算联合验收，再决定独立新 scope 的准入；本 owner 不再改源码或自行继续试跑。未来信息影响过去、存在任何未登记重算、对子未知错误结算解闸、来源/游标跨 scope 可用、任一公开约束未披露、覆盖现金/拒单/持仓/公司行动缺口，都足以推翻接线验收结论。若未来需要持仓状态程序、迟到历史修订、任意 Python 或真实收益证据，应另定权限与数据合同，不能从这次合成完成推导。

本子任务新增项目 Gateway / 供应商请求 0、真实市场读取 0、旧 scope/192 pending 修改 0。测试和唯一 demo 有实际合成内核执行，不能称所有计算为 0；工程/评审模型用量非零另列。没有读取原四题 private 答案或进行同预算参考发现。
