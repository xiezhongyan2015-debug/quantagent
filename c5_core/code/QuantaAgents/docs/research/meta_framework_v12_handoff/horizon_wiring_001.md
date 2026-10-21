# v12 通用共同样本期限动作接线 001

2026-09-07，Asia/Hong_Kong，Astra/xhigh 工程实现与检查。**已将固定 `[1,5,10]` 的 `diagnose_horizons` 接入通用非 Q 研究动作，并完成有界 fake-controller 端到端验证。** 本项仅修复 [题库准备审计](../meta_framework_v10_handoff/casebank_readiness_001.md) 中“工具说明存在，但通用研究动作无法调用”的缺口，不证明研究器行为更好、真实策略执行有效或架构获胜。源码已冻结交独立审查及 root 联合验收；本报告不是新的付费准入证。

仅改动 v12 `meta/ashare_research.py`、`ashare_case.py` 一处公开说明，新增专属测试与本报告。runtime/store、期限内核、预算、评分、费率、执行有效性、旧源码/回执和其他代理负责的 monitor 均未修改。本实施子任务新增项目 CodexGateway 调用 0、真实市场读取 0、真实回测 0；新专组的 `evaluate` 和旧 `diagnose` 路径一旦被调用即抛错。工程与审阅模型用量非零，精确用量由主控/平台另列，不冒称全项目调用为 0。

## 公开动作合同

[action_schema](../../../experiment_traces/meta_ashare_revision12/src/quanta_agents/meta/ashare_research.py:29) 的通用非终态动作新增 `diagnose_horizons`。run_step 仅在 case 同时提供 `diagnose_horizons` 与 `horizon_diagnostic_report` 时开放；baseline/candidate 使用同一判断和 schema。既有字段及终态提交规则保留：

- `strategy`：现有五字段策略合同，仍经现有校验；不允许更改执行器。
- `diagnostic_expression`：现有因果表达式白名单，禁止未来 lag 等越界表达式。
- `diagnostic_horizon`：schema 为兼容其他动作保留，**仅旧单期限 `diagnose` 使用**。新动作固定检验 `[1,5,10]`，不由该字段选择期限。每轮公开 prompt 与 case 工具说明均明确这个区别。
- 新动作占一轮原研究机会。每个请求登记三个 h 子尝试；它们不是三个独立研究样本，也不自动产生组合回测。
- Q 的已冻结六槽、各槽枚举与 `previously_evaluated` 提交合同保持原样。Q 没有开放新动作；不以共同工具升级名义回改窄消融实验。

现有 `AShareCase.diagnose_horizons` 负责同事件共同覆盖、下一开盘入场、t+h+1 开盘端点、截止、排除与依赖说明；接线只调用一次这个接口，不循环旧 diagnose 拼曲线。输出仍是 `eligible_signal_stock_days` 的毛端点诊断，不能改称入选股票、实际库存、净策略收益或因果匹配结果。

## 持久身份、子尝试与完整产物

[动作入口](../../../experiment_traces/meta_ashare_revision12/src/quanta_agents/meta/ashare_research.py:226) 在执行工具前，以现有 Store.update 的 `BEGIN IMMEDIATE` 原子登记 `run.horizon_actions[arm_round]`。每个条目包含 action 版本、run/arm/round、原模型 action 哈希、case、开发起止/实际加载截止、数据/快照、运行源及期限工具四个依赖文件的身份，并先登记三个确定的子尝试 ID。相同轮次的线程竞争只有一方可开始工具。

结果以 `arm_round_N_horizons.json` 独占创建并 fsync，保存完整 report、原 response、binding 和 observation。已有文件不能覆盖；发现已有完整或部分文件时先复核，不能先重算再等待写入冲突。完整 report 的 identity/request/diagnostic 哈希、固定 h 列表和子尝试、数据与代码、策略、表达式、开发范围及 execution_valid=false 均与本次意图核对。artifact 注册再保存实际文件 SHA256；公共摘要带 request/diagnostic hash 及 full_artifact 名称/绑定身份，文件 byte SHA 保存在控制器注册表。

下一轮模型仅收到有界摘要及计数，最大 32,768 UTF-8 字节；完整 events/pre-signal exclusions 不进入普通上下文。只保存完整 artifact 不等于开放任意文件读取；本项没有额外加入公共分页动作。所有原有分母、较短期限可用但因共同覆盖被排除的事件，以及 overlap 局限保留。

`horizon_action_accounting` 分开登记：三个 `requested_horizon_sub_attempts`、三个本轮 sub_attempt_ids、outcome、tool_invocations_started、repeated_request、duplicate_of；new_backtests 与 independent_research_samples 均为 0。`physical_kernel_computations` 明确未计量，因为 case 可使用冻结输入缓存，不能把一次方法调用伪称一次新物理计算或新金融样本。

同一 arm 后续轮重复相同策略/表达式/开发身份时，仍保留该轮请求和三个 requested 子尝试，标记 reused，复核并复用该 arm 已存产物，tool_invocations_started=0；不把重复请求退还成新机会。已知非法表达式/普通工具失败也保存三个预登记子尝试和失败摘要，重复同一失败请求可以复用失败证据。两个 arm 不共享研究历史或控制器的重复请求条目；底层纯工具缓存若复用同一输入，不被解释为研究上下文共享。

## 恢复和停止边界

[恢复检查](../../../experiment_traces/meta_ashare_revision12/src/quanta_agents/meta/ashare_research.py:211) 位于研究 phase 入口，在任何后续模型调用之前。已完成的研究记录还要与独立保存的 horizon action/完整文件相符，不能只凭普通 summary 自洽哈希放行。

| 保存状态 | 允许行为 | 不允许行为 |
|---|---|---|
| 完整产物及结算均在 | 核对原文件、身份、摘要与研究历史后复用 | 重新调用 case、增加子尝试或替换文件 |
| 完整文件已落盘，结算/研究行尚未写完 | 核对后结算同一意图；既有模型回答仍由原模型回执路径复用 | 为恢复另付一次模型，或重新计算期限 |
| 只有 pending 意图，无完整文件/只有部分文件 | 抛出 `HorizonActionBlocked`，保留意图、原文件与已消费模型槽，停止继续 | 自动重算、把未知写成零/失败完成、跳过该轮去开新模型 |
| source/data/case/strategy/范围/文件字节/研究历史不符 | 在下一模型前阻断，保留原证据 | 改哈希使旧回执过关，覆盖损坏文件或偷偷换输入 |

已知研究失败与未完整保存的未知结果分开。恢复本身不创建新的 horizon_actions 或 sub_attempt_ids；它可能补原条目的注册/结算，但不产生工具或研究机会。当前不存在“部分文件自动重建”功能，这是有意保留的停止边界，后续若需恢复政策必须另行冻结。

## 验证证据与版本

专属 [test_meta_research_horizons_v12.py](../../../experiment_traces/meta_ashare_revision12/tests/test_meta_research_horizons_v12.py) 使用已公开的 15 日期/两个中性符号小路径。case 避开真实初始化与数据文件，沿用真实因子校验和共同样本方法；controller 使用真实 Store 和产物函数，但不创建 Engine worker，模型接口是保存确定 JSON 的 fake。覆盖 schema→dispatcher→工具→完整产物/账本→下一轮 prompt，以及以下真实风险边界：

- 两侧能力一致、旧 scalar=19 也只返回固定三 h；共同样本 5 事件、完整候选 8 事件、端点小路径可核对，模型无完整事件表。
- 同 arm 重复、另一表达式的新机会、失败及失败重复、三个 ID/状态与每轮记账。
- 同轮线程竞争、完整 phase 重开、意图后崩溃、文件后/结算前崩溃。
- 外来 case/data/strategy/source、final/越截止身份、文件字节/历史/source 篡改、缺文件、部分文件不覆盖且不先重算。

第一次完整相关检查：新专组 19 项，加 `test_meta_ashare_runtime.py`、`test_meta_frozen_research.py`、`test_meta_q_research.py` 75 项，共 **94 passed，37.92 秒**。随后仅将 ashare_case 的公开说明改成固定三 h/Q 不开放/重复与恢复区别，最终重新运行专组：**19 passed，5.50 秒**。两次数量不能相加当作独立检查。最终同一完整源码清单的独立反例与联合验收由 root 汇总，本报告不事前捏造其结果。

| 文件 | 最终 SHA256 |
|---|---|
| v12 meta/ashare_research.py | 94360c1f7f4f42aac71f20b9a2f491c700a40bda20853769a988178ba2243827 |
| v12 meta/ashare_case.py（仅公开说明） | 7f61ebe7785343b06b0b3ec8cba8515133c8736d2dd2b05f305319dc3aacf600 |
| v12 tests/test_meta_research_horizons_v12.py | 3535711dbb18c86cab9b910d7238bbc60bd913783db1c0eb865ed9b261705d09 |
| v12 meta/runtime.py（与 v11 相同） | 767cedf3f6bd010c836b00d95b684ad29a1695a90b9cb333a1484bf3c328510e |
| v12 meta/store.py（与 v11 相同） | a722ca5256125f3eb10cedce51e3324f9eea8740672a6152597c795c3e2e9929 |

## 两问与下一边界

这一步做得怎么样：已把真实存在的期限内核接到通用动作与下一轮公开历史；用一次有界端到端路径检查了持久身份和重复/崩溃区别，未修改 Q、公用预算或评分。已知局限是大事件 artifact 的内存/I/O 尚未在真实长窗口评估、没有公共完整事件分页接线、未验证模型会主动选择该动作；工具返回不含匹配对照或可成交收益。公开 synthetic fixture、fake 模型路径及同一输入重放都不是新增独立研究样本。

下一步如何改进：等待独立审查和 root 的同版联合验收。若反例发现未授权范围可见、完整/摘要身份不一致、三 h 漏记、重复或恢复新增计算/机会、不同 arm 能力不同、或者旧回执必须改写才可继续，则判本接线未通过并停止扩大研究。只修明确反例后重冻相关文件，不扩大为八题大改。通过后是否开放新的付费研究，由主控结合 v11 提交合同和本共同工具版本另行冻结；不因这些测试通过自动启动运行或打开最终时期。
