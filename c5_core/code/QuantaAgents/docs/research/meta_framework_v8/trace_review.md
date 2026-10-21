# V7 原始研究轨迹独立复核

复核日期：2026-09-09。对象为 `output/research/meta_v7_acceptance_20260909/research_study` 的六个原始 prompt/response、训练因子与账户证据和只读账本。未调用模型、未重算账户、未加载市场原始数据。下文 JSON 路径中 `prompt#` 指 `RESEARCH_STATE_JSON` 后的对象，`response#/payload_json` 须先解码其字符串。

## 关键判断与优先级

1. **已确认缺陷：定向取证结果会被再次折叠；修改没有继承已复盘规格。** 第三次调用明确为“确保等权配对仅改变配置”请求第一轮纯动量 `omit_2` 的完整证据。程序确实读取了该证据，但第四次 prompt 将整个查询结果替换成 `narrow_query_required` 索引；此前所有已复盘 arm 的 `strategy` 又被移除。第四次调用实际没有收到所请求的父规格，随后把 `top_n` 从 40 改为 20、风险 epsilon 从 1e-6 改为 1e-8，score 也从 `0.5 * rank(F1)` 写成 `rank(F1)`。这支持“证据传递和修订接口存在可避免摩擦”，不能单凭轨迹证明模型的漂移完全由摘要导致，更不能归因原策略亏损。
2. **已确认表示缺陷，研究影响待验证：弱角色证据被不对称压缩。** 边际 IC 保留日数和缺失；风险只剩均值，交互只剩 raw/partial 均值。条件效应删除年度与离散度；首轮还因预算删了全部边际年度 IC。模型无法从附带证据区分相同均值对应的覆盖、秩亏、年度反转和有效样本差异。原始证据可检索，但存在第 1 项的取证回路问题。必须修复信息区分度；不能承诺增加字段就能提高发现能力。
3. **已知能力边界：轮内控制不识别风险信息与仓位缩放。** 第二轮逆波动与等权的平均仓位为 88.46% 和 98.78%；两者 Sharpe 增量同时区间含零。原模型已经明确拒绝把较小回撤归因于 F7 独立增益。需要清楚报告控制解释边界；没有证据支持为了“让模型更聪明”先改账户，或按实现收益事后匹配一个伪可交易控制。
4. **待验证假设：熟悉因子锚定限制发现。** 初始菜单只附已计算的 12 因子和显式 6 对交互，8 个日历定义有表达式及数值；程序允许查询其余库、创造合法表达式和选择其他因子。F1 的边际信号最强，F2 在 5 日有反转、与 F1 低相关，F7 的未来波动诊断显著且与总波动率重复。因此 F1/F2/F7 是有证据的首轮假设，不足以证明模型忽视已确立的日历 alpha。6 对局部诊断不覆盖多因子机制，也没有真实账户证明那些弱交互的增益。优先改善取证与可归因修订；暂不支持全库扩容、自动穷举或强制使用新因子。

## 六次调用实际所见与所做

调用目录均相对于 `research_study/model_calls`。序号按 `context_manifest.json#/revision` 排序。

| 序号 / 目录后缀 | 附带信息与动作 | 可核对位置 |
|---|---|---|
| 1 `e96113b40d75407a9d25044dae36c3ef` | 12 定义、12 边际诊断、66 相关、24 条条件 IC、6 交互；边际年度被删。提出 F1 + negate(F2)，F7 逆波动，40 股，删项与等权对照。 | prompt#/catalogue、factor_evidence/0、detail_in_full_evidence；response#/reason、payload_json/specs/0 |
| 2 `5bcffdce6546423594f0640ed0bc6c81` | 四臂年度、成本、仓位、配对区间和原假设；因子仅索引，控制臂规格未附。否定当前 F2 互补，拟在纯 F1 上识别配置作用。 | prompt#/paired_results/0；response#/payload_json/conclusion |
| 3 `a6db9c14b461465abb9ca64eb550dc48` | 恢复12因子，但删年度、只余16强相关；已复盘账户仅摘要，没有规格。付费查询已有 `omit_2` 完整 evidence。 | prompt#/paired_results/0/arms、detail_in_full_evidence；response#/payload_json/evidence_id |
| 4 `0e3f214db4ab4bf2a0c4067d41cd252e` | 查询结果被索引替代，所有旧 arm 仍无规格；提出20股、epsilon1e-8的纯F1配对。 | prompt#/recent_actions/0/data、paired_results/0/arms；response#/payload_json/specs/0 |
| 5 `75fec4e90ee24c3f873e62c2a10a4c5d` | 两臂训练结果及配对区间，因子仅索引。明确指出 F7 覆盖未附、仓位匹配缺失，按简约性冻结等权。 | response#/payload_json/conclusion、candidate_run_id |
| 6 `4472311ecc994e62b07a18dd1846c8da` | 固定候选后期诊断与曝光状态。否定跨期稳健性并关闭，无重选。 | response#/action、payload_json/conclusion |

初始模型看到的明确约束为最多 8 调用、16 账户、8 臂/批、32KB 上下文；目标要求预算/证据允许时完成两轮，不要求正收益。`propose_batch`、`evaluate_factors`、`register_factor`、`query_assets` 均可用。不能说模型被软件硬编码只能选 F1/F2/F7，也不能把库中研究关闭后补入的415条元数据算成这次可见候选。

## 已取到而未送达的规格

第三次请求指向 `evidence/ev_9acf19fe22519228ea72ab08b6ce3eb916f6f8e63152ec5d730dfc4c34dc5e47.json`。该文件 `#/run_id` 为 `run_c063bce991c0cc1ec4707d3a510e9843f01838843eee7ecb9d9785d9f1789cd4`，`#/strategy/allocation/top_n=40`，`#/strategy/risk_score/args/1/value=1e-6`。

`evidence/ev_1b59cd11c53831cae8234389e60f24800af6bc165e1406e66ae9e2fdec2e21f3.json#/data/result/value/strategy` 保存了完整返回。第四次 prompt 的 `#/recent_actions/0/data/full_evidence_id` 指回这个文件，`status=narrow_query_required`；没有 `result/value/strategy`。这是确认的“存储可读不等于模型已看见”。

代码位置：`src/quanta_agents/meta_v7/protocol.py` 的 `build_context` 中，初次遍历控制臂时删除非 proposal 的 strategy；已复盘批次循环再次删除所有 strategy；超预算分支对大于 1200 bytes 的 recent action 仅留下证据 ID。对应 V7 基线行 88–89、147–158、194–200。行号以本轮保存的 baseline source 为准，V8 编辑后可能移动。

第三次调用实际输入 16,858、输出 910 token，共17,768；这是一次可观察的取证开销，不能推算成 V8 一般节省率。其输出 reasoning token 777 包含在910内。

## 弱因子证据具体缺什么

完整训练报告为 `evidence/ev_0c23a7d7db8c0bd5b78f89303455c614d2992b66cc6e97f2761b225dd0d3b428.json`。

- `#/interactions/2` 是 CMF × VO。20日 partial IC 均值 -0.009850，五年均为负，但2016为 -0.032691，之后四年绝对值更小；5日年度正负交替。模型只看到两期限 raw/partial 均值，未看到年度、日波动、paired cells 或覆盖。它是可检验的弱交互候选，尚不是可交易优势。
- `#/interactions/3` 是 F1 × BIAS。20日 partial 均值0.009973，但2016/2018为负、2019为0.043743；5日较一致但2017略负。摘要隐藏了该期限差异。不能把“BIAS 与 F2 边际相关0.693”当作已否定所有 F1×BIAS 交互；原模型只是选择不重复叠加边际分量，没有在原回答中检验此交互。
- `#/interactions/5` 是隔夜收益 × VROC。partial 均值接近零；这支持保留负面候选，不能说明所有未选弱因子都有价值。
- `#/factors/F7/coverage/fraction=1.0`，但第五次复盘上下文只保留因子证据索引，原模型明确说“摘要未附F7覆盖率”。该缺失是展示缺失，不是原数值未计算。

初始 F1 的20日/5日边际 IC 分别0.059464/0.032738；F2为-0.013294/-0.029223。F2在F7高波动组的5日条件IC -0.033133，低波动组-0.009277。原模型引用了这项条件差异并担心逆波动削弱反转收益，说明它实际使用了一部分条件信息。不能把研究结果简化成“只按最高IC选因子”。

## 失败保存与结论边界

只读 SQLite 的六个 model_calls 均 applied、error为空，events 中无 fail/error 类型；这不代表整段流程未中断。`release_acceptance.json#/context_repairs` 保存两次付费调用前32KB阻断及修复，account_reruns均为0。付费失败调用0、已记录上下文准入阻断2，应分别计数。原上下文超限异常不在研究 events 表中，不能伪造旧失败事件来补齐。

第一轮删F2改善、第二轮仓位差异、后期亏损都是实际证据；分别只能支持该开发实验的结果、归因未识别与候选跨期失败。它们不能单独证明过拟合成因、所有反转因子无效或 V7 一般发现上限。原模型完成反证并关闭的行为应保留。

## 给有界 V8 比较的可重放问题

`scripts/audit_meta_v8_saved_trace.py` 生成带逐项来源路径/哈希/JSON pointer 的 `real_training_packet.json`，只含2016–2020旧数值。它包括原因子报告、第一轮四臂训练证据、第一轮原复盘、父omit_2规格、第二轮规格和训练摘要。问题与标准另在 packet 的 `evaluation`；向模型送题时不要发送 `answer_key`。

1. **受限等权修订。** 从第一轮omit_2出发，只改变allocation.weighting与risk_score，构造等权控制。客观标准：top_n仍40、全部其他allocation字段不变、score与gate原样、weighting=equal、risk_score=null；不把epsilon改成1e-8，不把top_n改成默认20。删除风险表达式时epsilon不再参与交易；不是重新估计epsilon。此题检验规格保留，不检验alpha发现。
2. **多项变更鉴别。** 对比父omit_2与第二轮inverse_volatility原规格，至少识别top_n 40→20、epsilon 1e-6→1e-8、score结构重写；后者在本次纯排名选股下可能排序等价，但不得未核实便将整个规范视为相同。明确跨轮不是只变配置的控制。解释第二轮等权/逆波动内部控制可以识别该配置政策总体差异，却不能把回撤差异认定为F7信息独立增益。

两种条件必须使用相同源数值、父规格、问题和可选操作。若一边提供受限修订动作、一边只提供完整spec，则要单列为接口能力变化；不能描述为纯摘要因果实验。可报告合法动作、字段保留、漂移发现、正确弃权及token；一个真实训练病例不构成泛化证明。
