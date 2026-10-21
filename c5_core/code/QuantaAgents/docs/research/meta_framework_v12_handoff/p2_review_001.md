# v12 P2 独立有界审查 001

2026-09-07，Astra/xhigh。**通用共同期限动作接线与纯同日匹配核在本次代码审查、9 项独立合成反例范围内通过；正式联合验收另以 root 冻结回执为准。** 未发现尚待修复的本范围实质阻断。这个结论不授予付费派发权限，不证明模型会选择这些工具、架构改进、策略盈利或真实执行有效。`event_conditioning` 尚未接入 researcher harness，不能登记成模型已可用的匹配功能。

审查依据包括 checkpoint [007](../meta_framework_v8_handoff/checkpoint_20260907_007.json)、两位作者的最终源码及专属测试、[期限接线说明](horizon_wiring_001.md)、[同日匹配说明](event_conditioning_001.md)。本审查只新增专属测试和本文，没有修改作者源码、其他测试、旧 campaign、市场数据或原失败回答；不复审已经关闭的 GUI。新增项目研究 Gateway 调用为 0，真实市场读取、联网及真实回测均为 0；工程实现与评审自身的模型用量不能写成零。

## 实际达到的边界

| 项目 | 只读检查与反例结果 | 不应外推的结论 |
|---|---|---|
| 通用动作可达性 | `action_schema` / `run_step` 在 case 同时具有摘要与完整报告接口时开放 `diagnose_horizons`。baseline/candidate 使用同一合同；既有 Q 冻结槽和终态提交限制不变。 | 不等于旧 Q 已开放新能力，也不等于真实模型已使用此动作。 |
| 三期限与机会 | 新动作固定 `[1,5,10]`，一次原研究轮、三个预登记子尝试。旧 scalar 字段不能选择期限。重复或已知失败仍占各自轮次并保留三个 ID。 | 三个期限不是三个独立研究样本。方法调用次数与底层物理 kernel 计算次数不同，后者明确未计量。 |
| 持久化及恢复 | 原子登记先于工具调用；完整 wrapper 独占创建并 fsync；保存 response、binding、完整 report、摘要和账务。完整文件可补原条目结算；只有意图、部分文件或证据冲突均在下一模型前停止。 | 恢复没有重新计算授权；没有把未知结果释放成新的机会。纯函数的任意外部重复调用不受此研究动作账本自动覆盖。 |
| 下一轮证据 | 摘要从完整 report 重新推导并核对，绑定本 run/arm/round、策略、表达式、case、数据、截止及源码；公共历史有 request/diagnostic hash 与完整 artifact 身份。32,768 字节上限失败时停止。 | 完整事件仍为 controller artifact；本改动没有增加通用研究器的完整事件分页入口。 |
| 匹配第一阶段 | 无 outcome 参数；只接受事前声明的有限离散域、共同三期限和固定同日无放回规则。所有同日行共享同一带时区 decision instant；分类及特征可用时间不得在其后。 | 合成时间声明一致不认证历史 PIT，也不证明调用者没有先看结果再提交另一份合法规格。 |
| 匹配第二阶段 | 读取结果前，从保存的原决策字段重建整个确定性计划并逐项比较；再校验完整身份、日历、候选覆盖、事件哈希及端点。预选 control 缺任一期端点时，原 pair 从全部三个期限共同样本排除，不换股。 | 不能借更完整或更有利的备用 control 修补样本；这不是交易执行或因果效应估计。 |
| 分母、相关性与失败 | 保留原候选、未配对、缺特征、空组和已选却缺结果的 pair。每个声明组×两种观察单位×三期限保留子尝试。事件/配对等权与日期等权分别给出；共享股票、重叠区间及日期簇保留。 | 不把行数、配对数或日期数认证为独立 n；不产生显著性、净组合收益或新 alpha 结论。 |

预算接线核查位于 [ashare_research.py](../../../experiment_traces/meta_ashare_revision12/src/quanta_agents/meta/ashare_research.py:226) 和 phase 入口恢复检查。新动作仍由现有轮次上限、模型调用前后 gate 及提交 schema 约束，没有新建独立付费循环。`runtime.py` 与 `store.py` 的实际 SHA256 分别与 v11 完全一致。暂停是在派发前与下一轮边界检查；本审查未声称同步 kernel 可在任意计算指令中途取消。

## 审查中收口的两项匹配边界

1. 初读第二阶段只验证 plan 自身内容哈希，无法据此排除“修改 pair 并重新封 hash”的结构变更。我向作者指出后，作者增加 [确定性重建](../../../experiment_traces/meta_ashare_revision12/src/quanta_agents/meta/event_conditioning.py:287)，且该步骤先于读取 outcome。独立反例交换两对的 control、重算 pair 与 plan 哈希，最终版本仍拒绝。这里的“修前缺口”来自代码审查；没有捏造修前测试执行回执。
2. 作者同时补充同日所有行共享一个决策时点，阻止控制股以自身更晚决策时间接纳信号当时尚未知的特征。独立反例中 signal 在 15:01 决策，control 在 15:06 决策并使用 15:05 才到的特征，最终版本拒绝。

重建可验证计划的算法和结构，不能认证时间先后。最终 plan/result 均明确 `chronological_commitment_verified=false`、`controller_commitment_required=true`。输入语义 hash 对日期/股票排序规范化；原文件字节来源及事前承诺必须由未来 controller 另行绑定。重建比较次数单列为 revalidation，不能拿它增加搜索机会或把计算说成从未重复。

## 独立验证

专属文件：[test_meta_p2_independent_v12.py](../../../experiment_traces/meta_ashare_revision12/tests/test_meta_p2_independent_v12.py)。只复用作者 fake Controller 的临时账本、PublicCase 合成数据装载器；不创建真实 Engine worker。匹配测试另造明确标记的合成 16 会话价格表，预期数字由本文件 Decimal 独立计算，不使用被测匹配摘要求 expected。

定向实际命令在项目根执行，使用 `.venv/Scripts/python.exe`、v12 `src` 的 `PYTHONPATH`，只运行该单一测试模块。结果：**9 passed in 3.64s**。九项为：

| 独立反例 | 核对结果 |
|---|---|
| 工具已知失败与随后重复 | 3 研究轮、9 个不同子尝试 ID 均保留；仅 1 次工具请求；失败及账务进入下一轮提示。 |
| 原子预登记已经提交、工具尚未开始即崩溃 | `tool_invocations_started=0` 仍不得自动重算；重开前拒绝，模型槽保持原 1 个。 |
| 完整文件已写、尚未保存 artifact SHA，修改公共摘要 | 即使不能靠已结算字节 SHA 发现，也会因摘要不等于完整报告而拒绝；不派发新模型。 |
| scalar 19→1→10 | 三次都返回固定共同 `[1,5,10]`；同 report 复用且三轮机会各保留。 |
| 同日 2 pair、另一日 1 pair | Decimal 独立核对事件等权与日期等权，二者数值确实不同；共同样本 hash、2 日期、重叠与空组 6 个子尝试均保留。 |
| 已选 control 缺 h10、同日其他候选仍完整 | 原 3 pair 中 1 个共同剔除，三个 h 都使用余下 2 pair；短期已知差值仍保留，不借备用股。 |
| 交换 control 并重封所有相关内容 hash | 违反确定性决策计划，拒绝。 |
| 同日 control 使用较晚到达特征 | 拒绝。 |
| 原输入行顺序倒置 | 完整规范计划及匹配语义相同。 |

作者报告的 19 项期限专组及 27 项匹配专组属于各自验证；不能与本 9 项机械相加冒充一次联合运行。root 将使用冻结清单运行受影响的联合组并单独追加实际 receipt。本文的验收标准不随该结果修改：若共同期限漏记、恢复新增模型/计算、摘要/完整证据分离、未来特征进入、按结果换 control 或失败样本被删除，相关边界即不通过。

## 最终文件身份

| 文件（v12 下） | SHA256 |
|---|---|
| `src/quanta_agents/meta/ashare_research.py` | `94360c1f7f4f42aac71f20b9a2f491c700a40bda20853769a988178ba2243827` |
| `src/quanta_agents/meta/ashare_case.py`（公开说明） | `7f61ebe7785343b06b0b3ec8cba8515133c8736d2dd2b05f305319dc3aacf600` |
| `src/quanta_agents/meta/event_conditioning.py` | `f06a0038fe09cd7a5a31158a08100febc7d00e0a7f8401f8b01dcc60e07ebaef` |
| `tests/test_meta_research_horizons_v12.py` | `3535711dbb18c86cab9b910d7238bbc60bd913783db1c0eb865ed9b261705d09` |
| `tests/test_meta_event_conditioning_v12.py` | `09f7b47792c2a2f723fe3da842c6a05053f750aa83d03b4555b2b44ef3520e60` |
| `tests/test_meta_p2_independent_v12.py` | `05927d9e36a77476291d41526fbb5ba67bd8a129fef0a069c0ec108ccfde83b0` |
| `src/quanta_agents/meta/runtime.py`（同 v11） | `767cedf3f6bd010c836b00d95b684ad29a1695a90b9cb333a1484bf3c328510e` |
| `src/quanta_agents/meta/store.py`（同 v11） | `a722ca5256125f3eb10cedce51e3324f9eea8740672a6152597c795c3e2e9929` |

## 两问自检及下一准入边界

**这一步做得怎么样？** 已完成对两位作者代码的独立审查及有界反例，指出并验证了计划自封 hash 不足的修复，并独立检验同日特征时点、日期权重、共同分母与恢复不重算。保留两题原失败身份、工程合成数据身份和未知结果，不以新增测试替代实际研究证据。当前通过范围是这些冻结接口的工程行为，联合结果仍须引用其原回执。

**下一步该做什么，如何改进？** root 完成同版受影响联合验收后，可记录本 P2 工程增量。若将匹配开放给 researcher，必须另行实现并验收 controller 的事前登记、唯一 plan/原输入/规格绑定、所有失败与替代规格预算、崩溃恢复和完整结果到公共摘要的接线；尚不能用纯函数的计数替代这些职责。通用 horizon 的实际模型使用、真实长窗口资源开销和公共事件查询也尚未由本测试证明。以上工作不自动授权付费、隐藏题扩充或真实市场回测，`execution_valid` 与正式目标成功均继续为 false。
