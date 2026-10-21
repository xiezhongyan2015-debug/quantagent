# v17 策略开发：独立有界评审 001

结论：所审合成策略计算与持久化恢复边界有界通过。发现的一项普通 I/O 异常将未知子任务错误结为失败的问题，已先保存真实失败，再由作者最小修复，并用同一反例关闭。该结论不开放实际策略研究或付费，不认证真实市场 PIT、完整执行资格、收益或架构稳定性。

评审为 Astra / xhigh。新增研究 Gateway 调用 0，实际市场读取 0，实际四题 / private 数据读取 0；工程模型 token 用量未单独核定，不能记成 0。独立预期记录先于新模块输出读取保存，六项数值预期为字面量手算，未调用被测模块生成 expected。

## 版本与证据

| 文件 / 回执 | SHA256 |
|---|---|
| `independent_predictions_001.md` | `d80690cb30876c91f8c1b7064c0ab82d66ae5da93bc1aa986c4e6998031937d7` |
| `strategy_development_contract_001.json` | `f98609afb80a6d28e9af7e63d779647c21a45fa013e292d7dded7618c1a9d991` |
| v17 `src/quanta_agents/meta/factor_strategy_program.py` | `0ce1ee91ade853a52755eb044b309f4a3d3e22d3275bd1b11c5f0fe0302a1da4` |
| v17 `src/quanta_agents/meta/strategy_development.py` | `0d578f7e15b7780025f32bcfaa637fb748142351c2a5ff674c821c950c2399ff` |
| v17 `src/quanta_agents/raw_saved_research.py`，继承核 | `fbebcfa6a1e0bd288e4d736d9c903ccba8a1bcede401e178a11a6b34c70bc5cb` |
| v17 `tests/test_meta_strategy_development_independent_v17.py` | `8ee0f7ef2474f23498bbb5c04cc156320cd4aa2091fb3c505f34a0580cbc688c` |
| `strategy_independent_validation/001_pure_first/receipt.json` | `8f9ffcdccd953973fa3361dbd03218fb1fe4b790f1e90325ff74e57aff85b29b` |
| `strategy_independent_validation/002_io_unknown_before/receipt.json` | `9c186cfa5b26ecd0847627fb84a79853ccd905df5ff70498823fec0ad6db89bc` |
| `strategy_independent_validation/003_controller_after/receipt.json` | `a2b1b790c152cdc79340b7c04dd968eed627e1c73203f9e42636ada3967a50a0` |

以上 validation 路径均相对 `experiment_traces/meta_ashare_revision17`；每次目录保存 pytest 输出、JUnit、输入和运行后 hash。002 另保留修前五个源 / 测试文件副本。001 当时测试文件只有六项；003 用 AST 比对确认这六项函数未变，且纯模块 SHA 与 001 相同，因此没有为补控制器测试再跑六项算术。

实际独立检查覆盖为 **6 项首次通过（2.87 秒）+ 3 项修后通过（4.10 秒）= 9 项**；不是同一次运行九项。原 **1 项失败（4.62 秒）**永久保留。作者另有 39 项通过记录，本评审没有重复运行作者整组，也没有把这些作者检查算作独立发现。

## 真实缺陷与修复

`execute()` 原通用 `except Exception` 会把 `_develop()` 抛出的任何普通异常结为 parent `failed`。独立 fixture 使用真实 SavedRawResearch 合成子任务，执行意图和检查点已保存；让 `terminal` 的成功写入、随后失败写入均抛 `OSError`。子任务只读复核给出 `run_count=1, status=interrupted_saved_only`，父状态却为 `pending_slots=[]`。这会撤掉“任何未知任务阻断新 action”的门，普通 Python 异常被误当作原价子任务已经结算的证据。

修复后，一旦 `raw_mechanical_execution` 阶段已登记，普通异常只增加 interruption 记录，父 action 保持 pending；没有失败终态或失败事实记忆。修后同一反例确认：原候选 / 变体 / 三项 requested subattempt / 一次 raw intent 保留；重新 load 后恢复不编译、不生成目标、不再调用 raw execute；缺 terminal 的子任务保持 interrupted，另一 slot 被拒绝且计数不变。

另一独立反例让 raw child 已有完整 terminal，而 parent settlement 抛普通 `OSError`。新 parent 保持 pending；重新 load 后，设置 compile、build_targets、raw create、raw execute、simulate、raw 输入加载六处陷阱，仍可通过保存证据恢复一次完成记录。重复恢复不改变 state hash，不新增事实记忆，raw_run_count 仍为 1。

## 手算与时点核对

- 两个不同 named-factor 程序分别组合 rolling mean / lag / where 与差分条件，产生预注册的不同十行目标；前向引用失败，不能由固定模板代替不同程序。
- `available_at` 恰好自身 `15:10:00+08:00` 可用，晚一秒永久屏蔽；后续 lag 不回填。无时区输入失败；修改末日未来值 / 资格不改变既往目标。
- `lag(cs_rank(x),1)` 的 rank 使用各自历史行的 PIT 资格，最终目标另受 signal 当日资格约束；迟到资格与当日不合资格的目标均为零。
- rolling 的缺格留在完整轴上，不能跳过缺失行拿更早观察补成完整窗口。重复坐标 / 缺格失败，分母不缩小。
- 两股 `.5` 合计恰为 1；两股 `.5000000000000001` 因严格 Decimal 字符串合计超过 1 而整体失败。无默认归一化；非有限权重转零留原因。未交易末行或最终强制清仓前行的非法有限值也不能被覆盖隐藏。
- 目标严格为全部符号 × 下一会话，第一日无目标，最后 trade session 强制零且保留 raw；最后 signal 行单列 untraded_terminal_signals。

机会登记另由独立 trap 直接检查：第一次编译入口前，SQLite 状态中已有一个 candidate、一个 internal variant、三个 requested subattempts 及 pending slot。已知编译失败不会生成目标或 raw child；同 slot 重放和新 slot 同请求复用均无新编译，后者仍计第二个候选与另外三个 requested subattempts。

## 边界

原价 fixture 是作者提供的字面量 gzip / 义务构造器，只用来生成隔离临时数据；独立检查不读取作者参考数值或实际题包。原价费用 / 库存全面验证来自既有内核及作者相关组，本评审没有重新扩大回测矩阵。强制零目标不是成交保证，源码和合同保留原价拒绝、持仓及估值失败的含义。

该模块尚不能据此称为实际原四题 researcher 可用的策略工具；是否接入新的 harness、公开数据权限、预算和实际范围，必须由对应冻结准入另行决定。固定的合成预算和来源 hash 证明本侧工程一致性，不证明外部记录真实性。当前所审范围无新增未关闭阻断。

## 两问自检

1. 是否改变预期、删掉失败、缩小股日、用迟到值补历史、忽略未结子任务来取得通过？没有；手算预期先存，真实 before 失败保留，未知门修复后用原反例复核。
2. 是否把合成算术 / 恢复检查通过当成收益、PIT 认证、execution_valid 或架构达标？没有；结论仅限本文列明的工程边界，所有研究资格仍为 false。
