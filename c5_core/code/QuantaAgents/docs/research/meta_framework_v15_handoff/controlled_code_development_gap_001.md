# 受控因子/策略开发与迭代记忆：有界只读差距审计 001

结论：已有可复用的因子语言、策略生成检查、实验账本和本侧完整上下文；还没有把这些连接成“普通研究输入 → 冻结策略产物 → 可信执行 → 有来源与失效记录的记忆”的统一公开工具。最小下一步适合在隔离新修订中增加受限表达式策略的编译/核验适配器，先完成纯合成、零模型路径。不能直接把旧 Python 策略 agent 接到当前四题 worker。

本次只读主源码与 v15 继承/新增源码、测试定义和实施提示词第 48、50 行；未实例化 Engine/Workbench/Gateway，未运行测试、回测、模型、服务或读取市场源，未打开四题进行中回答。唯一写入为本文。根任务报告的 worker 86028 及已冻结四题协议不受本建议影响。本子任务新增项目 CodexGateway 调用 0；工程/评审模型用量非零另列。目录基准为 `/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents`，下述路径均相对于此根。

## 能复用什么

| 层 | 精确源码入口 | 已有行为及边界 | 可复用测试（本次未重跑） |
| --- | --- | --- | --- |
| 受控因子语言 | `src/quanta_agents/meta/factor_algebra.py:44` `_parse`；`:125` `validate_expression`；`:130` `evaluate_expression`；`:262` `expression_guide` | AST 白名单解释，不执行研究者 Python；有字符/节点/深度/窗口上限、共同日期轴、PIT eligible 横截面排名和缺值传播。语言因果性不认证输入字段实际发布时间。 | `tests/test_meta_factor_algebra.py`：未来行扰动、嵌套排名、缺值、轴对齐、资源限制、注入拒绝。 |
| 表达式到组合规则 | `experiment_traces/meta_ashare_revision15/src/quanta_agents/meta/ashare_case.py:80` `strategy_schema`；`:108` `validate_strategy`；`:342` `_weights`；`:382` `evaluate` | 五字段规格可执行为开发期目标权重，稳定排序、固定分配及截止退出；该 evaluate 是复权单位近似模拟，不能冒充原股数执行有效。对象初始化/数据加载不是本审计动作。 | 同修订 `tests/test_meta_factor_algebra.py`、`tests/test_meta_frozen_research.py`；实际接线还应对目标权重另给手算 oracle。 |
| 旧通用研究动作 | 同修订 `src/quanta_agents/meta/ashare_research.py:29` `action_schema`；`:226` `_run_horizon_action`；`:435` `_persist_record`；`:446` `_trace_for_model` | 非 Q 固定排程支持 diagnose、diagnose_horizons、backtest、submit；期限曲线三子尝试及保存后恢复已有接线。Q 固定排程并不提供新曲线动作。不能改当前合同放宽动作。 | 同修订 `tests/test_meta_research_horizons_v12.py`、`tests/test_meta_q_research.py`、`tests/test_meta_frozen_research.py`。 |
| 旧 Python 策略开发与检查 | `src/quanta_agents/agents/strategy_agent.py:54` 上版代码；`:87` diff；`:671` 代码轨迹；`:1302` `run`。`src/quanta_agents/strategy_code_policy.py:524` `validate_generated_strategy_code`；`:680` `compile_strategy_definitions` | 已有代码修改记录、AST 策略和输出验证；旧 agent 的 `:677` 在本进程 `exec(compile(...))`，命名空间含训练/验证数据包。名称 sandbox 不等于独立进程/文件/网络/资源隔离认证。其内部纠错与模型交互不能绕开新账本准入。 | `tests/test_strategy_agent.py`、`tests/test_strategy_code_policy.py`：导入/IO/结果字段/定义期执行等反例。 |
| 因果与交易计划检查 | `src/quanta_agents/strategy_future_data_check.py:286` `check_output_weights_truncation_consistency`、`:333` `check_generated_daily_strategy`；`strategy_mechanism_signal_check.py:482`；`strategy_position_plan_check.py:289` | 未来截断一致性、信号置假后的权重检查、持仓计划执行检查是可复用技术门；有限反例通过不证明任意代码安全或来源真实。 | `tests/test_strategy_future_data_check.py`、`tests/test_strategy_mechanism_signal_check.py`、`tests/test_strategy_position_plan_check.py`。 |
| 原价可信执行桥 | 同修订 `src/quanta_agents/raw_saved_research.py:73` `freeze_saved_research_plan`；`:257` `SavedRawResearch.create`；`:316` `execute_saved`；`:348` `reconcile_saved_only` | 可冻结显式 target、calendar、source artifact 与逐日六类义务；已派发只能读保存前缀，不重模拟。当前是已保存输入的原股数机械核验，unknown 义务会停止，`execution_valid/formal_target_success` 不因完成变真。 | 同修订 `tests/test_raw_saved_research_v15.py`：费用/T+1、缺持仓估值、未知公司行动、事件意图中断、预算/文件边界、saved-only。 |
| 持久研究/记忆基础 | 同修订 `src/quanta_agents/meta/store.py:144` `reserve_call`；`casebank_campaign.py:199` `reserve_call`；`casebank_live.py:71` `_history`、`:97` `public_model_prompt`、`:204` `reconcile_saved_only` | 原子调用预算、唯一控制器、完整本侧公开回答与工作台历史、身份绑定和未知停止已有基础。适用当前 casebank 协议，不能把新策略执行偷偷定义为其中某个诊断。 | 同修订 `tests/test_meta_casebank_campaign_v15.py`、`tests/test_meta_casebank_live_v15.py`、`tests/test_meta_casebank_live_independent_v15.py`：阶段共享预算、本侧隔离、完整上下文、崩溃边界、零重派。 |

上述主源码的 factor_algebra、strategy_code_policy、三个 strategy 检查模块、StrategyAgent、ResearchPlannerV2 与 v15 副本逐字相同，本次已核。其存在是复用起点，不自动继承新协议准入资格。

## 阻断普通输入成为可执行策略的具体缺口

1. **公开产物合同不连通。** `v15/src/quanta_agents/meta/casebank_workbench.py:125`、`:254` 只提供 inspect_inputs、diagnose_horizons、condition_events、submit_research_report，候选是单/双离散条件和 signal_block。它没有 score/filter 代码、完整仓位函数或原价执行请求。最终报告被接收仅表示结构和引用合法。原普通四包也不因此具备真实执行来源义务。
2. **决策代码与执行器缺少统一冻结桥。** AShareCase 能生成权重，SavedRawResearch 收固定 targets；尚未见这两者由同一 public spec、source/protocol/field availability、共同日期轴、预算子尝试和 code hash 连起来的公开工作台入口。尤其原价/复权单位、信号时点/下一开盘、持仓缺值、现金、公司行动/容量不能靠字段改名统一。
3. **代码权限及恢复契约不同。** 旧 Python 路径需要独立资源/IO/留出权限隔离证明，不能把 AST 策略误称完整安全边界。旧 `ashare_research.py:365` 的 `_save_raw.recover` 明确可以调用 `case.evaluate` 重建缺失产物，再要求观测一致；这属于显式相同策略重算，不是当前 casebank/raw 的 saved-only。新桥若要零重算，缺产物必须阻断，不能照搬该分支。
4. **迭代记忆已有记录，尚缺专用检索合同。** `src/quanta_agents/state.py:79` 保存 code_text、candidate_records、experiment_records、seen_periods、重试/研究次数和 history；`agents/research_planner_v2.py:1318` `compact_candidate_history` 去除 final-period 数据并压缩历史。新 casebank 则完整保留本侧模型回答及工具记录。所审入口尚未实现统一 `memory_id/source_evidence_ids/applicable_conditions/counterevidence/invalidation/supersedes` 与按 scope 检索 API。不能把有 history 字段等同于已完成 P3 可检索、可失效记忆，也不能为压缩调用未记账摘要模型。
5. **GUI 观察面仍不完整。** v15 监控已显示保存公开 action、类型/arm/task，但没有全部工具返回、代码版本 diff、冻结执行计划和记忆反证链的按需浏览。只读界面与研究器写接口应分离；不能通过导出整个 ledger 或计划补展示。

## 一个最小建议：先做受限表达式策略的纯工程冻结桥

仅作为下一修订建议，当前不实施、不增预算、不向四题发布新能力。新模块可提供 `freeze_factor_strategy(spec, *, binding, public_field_contract)`，先用既有五字段策略验证和因子语言冻结普通策略规格，返回 `strategy_artifact_id/spec_hash/source_hash/scope_hash`。随后由可信适配器在**另外冻结的合成资料**上生成并保存逐日 score/filter/eligibility/target 权重及原因，输出不可变 artifact 和有界摘要；研究者不能提供文件路径、修改执行器或读取留出。

复用当前 slot/request/result 身份及恢复模式，但使用独立版本合同。每个新规格、失败及新轮重复登记机会；内部因果扰动/参数子项公开计数；相同 slot 恢复只复核文件。本文不提议增加现有 16 query/12 candidate/1 final/17 call 或阶段 token 上限，也不把技术检查或三个期限当独立研究样本。若未来付费接入需要新的动作成本语义，须先单独冻结并审阅，不能借旧预算名称隐式放行。

第一步止于合成资料的合法策略产物、冻结目标权重和来源记忆条目，不跑真实收益。可复用 raw 桥保存/核验接口，但涉及实际 simulator 的后续演示应另有明确授权；当前四题活动、原包、代码、准入和预算全部不动。后续自由 Python 代码支持是另一增量，不能在这个适配器内开放 exec。

最小验收反例：未来行扰动不改变过去资格/权重；缺值不填补；同分排序与不足名额现金保留；未声明字段/负 lag/属性调用拒绝；源或 spec 改一字不能复用；意图后中断和缺 artifact 恢复零计算；新 slot 重复仍计机会；另一 arm 的 evidence/memory ID 不可检索；失败记录和失效说明进入下一轮本侧上下文。任何一项失败即推翻“桥已可接研究循环”，保持付费与真实执行关闭，而非改变成功口径。

## 证据指纹与两问

本次只读时 v15 核心 SHA256：factor_algebra `84783c6f907bcc9331602277605cdcd250d3a89680b51b9a2ecaf5bfd8021d30`；ashare_research `94360c1f7f4f42aac71f20b9a2f491c700a40bda20853769a988178ba2243827`；casebank_workbench `ea2941fc5776e72c32a47f67b01c169b94a754945b0ce85ee8a3af8b04fb9ff3`；casebank_live `8bd36896ed1445cae29addf3f7decabad05c171367ee60240c4f9a0603074a60`；raw_saved_research `fbebcfa6a1e0bd288e4d736d9c903ccba8a1bcede401e178a11a6b34c70bc5cb`。

**是否已有真实可执行策略研究闭环？** 在本次所审接口中，没有；现有受限语言、旧策略生成与可信原价桥各有实现，尚未通过统一公开控制器连接。不能把当前四题描述诊断成功当策略产出。

**哪些判断仍有限制？** 这是五分钟有界静态审计，未扫描全部历史修订、未重跑测试、未做安全攻防或真实来源核验。表列测试仅说明可复用反例位置，不宣称本轮通过。若另有已冻结公开入口能证明上述代码产物、执行与记忆身份已闭环，应以其源码/专组/保存回执推翻“接线缺失”的结论，再缩小下一步范围。
