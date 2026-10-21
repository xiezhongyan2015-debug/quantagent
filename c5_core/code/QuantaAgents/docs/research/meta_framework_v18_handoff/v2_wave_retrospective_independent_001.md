# V2 整波元框架：独立效果复核 001

2026-09-07，继承 gpt-6-astra/xhigh。**有限有效：运行与证据工程取得了可复核改善，两个暴露诊断题出现了内容正确的合法终稿；尚未证明普通输入研究链已经完成替换、架构稳定优胜或真实成本后冻结样本外 Sharpe > 1。** 最新四题参考研究未闭环，应保留未完成，不能把它解释为新架构的因果失败。

本次只读主框架说明、原 V2 源码、迭代日志及各阶段独立审查；没有重跑测试、研究、回测、查询工作台、读取大行情或修改运行。未读取本轮私有机制、候选答案来重新选优。工程收尾的其他离线验收由主控另记；本报告不等待它们，也不授权任何付费恢复。[目标及验收标准][goal]、[逐轮日志][journal]。

## 从原 V2 到现在，实际改变了什么

不能把 2026-08-10 的 `RESEARCH_LOOP_V2.md` 直接当作当前 `workflow_v2` 的能力说明：前者描述允许 diagnose_only 的旧角色流程，当前候选验证器仍明确拒绝 request_data、diagnose_only、stop；正式循环继续把既定 theory_book 与单一 accepted_strategy 交给候选规划。新元框架最初只允许修改研究指导，后来扩展出公共证据工具、预算与恢复控制器以及受限策略程序，但这些主要位于隔离 meta 修订。**并列模块已实现，不等于旧普通输入入口已整体接通。** [旧说明][legacy]、[候选实际拒绝门][planner]、[实际候选调用][workflow]、[META 主说明][master]。

| 层面 | 已有的直接证据 | 仍未证明 |
| --- | --- | --- |
| 完成识别与恢复 | Q 三份误判回答依据保存事件核验并导入一次，涉及历史 61,758 token；原失败不改，无付费重问。 | 导入不等于研究动作执行；旧退出码缺失、供应商请求/模型未认证仍不能补造。[P0][p0] |
| 诊断行为 | v12 两个新上下文均有唯一合法终稿；20 个注册数值正确，库存、现金、期限及关键解释有已返回证据支持。 | 仅两个已暴露固定题的实际实例；有结构化遗漏和日期精度遗漏，不是隐藏发现率、独立参考资格或架构因果增益。[独立内容评审][diagnostic] |
| 主动研究工具 | 期限/共同样本、条件匹配、公共分页和内部尝试登记已形成受控接口；最新首题确实完成 12 个 distinct diagnose_horizons 与 2 个 inspect_inputs。 | 该首题没有实际 condition_events 或最终报告；受控策略程序只在合成工程路径连通原价资金核，尚未接入四题研究或合格真实行情。[首题记录][outcome]、[策略开发边界][strategy] |
| 运行治理 | 真实纠错回答保存后只应用一次；原失败、80k 未知、同域调用上限和原过期结论继承；单题预算不足实际挡住下一调用。 | 不是绝对账单帽、OS 沙箱或 worker 存活证明；新时间窗口是明确增加的资源，不是原六小时完成，也不增加独立样本。[保存应用复核][saved]、[预算停机复核][stop] |
| 数据与执行 | 846 股 × 1,217 日期的保存网格有来源和拒绝分母，原股数/现金/费用/T+1/拒单的有限工程路径可复查。 | 历史成员中仍有 11,708 股日未接受；全部行 execution_valid=false。两股 16 会话的 192 项义务仍 pending、未执行；单次现金分红候选不能证明全权益完整。[盘点][raw]、[局部义务][obligations] |

原 V2 本来就有机制解释、研究/确认/最终隔离等研究约束；本波并非从完全无约束起步。真正新增的价值是把部分“应该调查/应该保存”落实为可查询证据、持久意图、幂等恢复与可拒绝的接口。其完整端到端效果仍需区别于组件验收。[旧流程][legacy]、[META 分层及限制][master]。

## 有效果的迹象，不等于完成了架构验证

早期冻结开发批次是 **8 个原计划 run，7 完成、1 失败**，三个完整配对开发 Sharpe 差为 +0.379014、+0.260181、+0.260181，最佳最终开发 Sharpe 0.453622。应该承认这个有限的正向描述性信号，不能把“样本少”说成没有任何改善迹象。不过它只覆盖两类任务、共享开发年份、含一个不完整配对，执行仍 false；两次量能启动收敛为同一经济路径，不产生新的市场证据。[全批审计][batch]

完整候选每次 0 诊断/11 回测，完整基线每次 4 诊断/7 回测。相同总资源规则下，工具分配可属于架构的总效果，不能仅因分配不同就宣布比较无效；但这些结果不能归因于“更好的期限诊断”，因为候选没有使用它。后来的固定动作 Q 未完成，也没有补成识别这一机制的公平实验证据。[全批行为表][batch]、[原研究方案对归因的修正][redesign]

最新四题只运行 baseline 轨道。首题 **15 次传输、14 完成，491,954 已知 token + 80,000 原未知预留，14 查询/12 候选/0 final**；后 3 题未开始。既没有最终主张可判正确/错误，也没有合法弃权，不能选择中间最好者补成终稿。它证实本次交付失败，不能单独证实模型无发现能力、某种组织方式更差，或四种机制均研究失败。[终态独立评审][outcome]

四包均为 exposed synthetic development，费用仅声明而未模拟，costs_applied=false；即使将来有好报告，本轮也不是净收益正控或正式策略成功率样本。当前不存在完成的强基线对候选架构公平筛查、足以估计成功概率的跨机制独立重复、合格真实净 OOS 或冻结后的前瞻纸面证据。不能写“正式策略成功率 0/4”；正确表述是正式资格未评定，原四题描述性分母完整保留。[事前评价][rubric]、[正式门][goal]

## 多轮工程投入与未闭环的根因

1. **局部测试的接受边界曾被过度外推。** v10 两份回答通过生成 schema，却被未充分公开的运行跨字段规则拒绝；v15 标准本地 schema 与 fake transport 通过后，真实链仍返回 invalid_json_schema。两者分别暴露“公开研究合同≠实际验收”和“本地校验≠供应商接受集”。后续公开合同、保留严格本地语义以及受控纠错是有针对性的修复；不能把原失败转成旧 live 成功，也不能由此宣称已消除全部外部故障。[v10 根因][contract]、[v15 独立根因][schema]
2. **预算控制保护了费用边界，却未保护交付机会。** 末次调用前名义余量 83,978，final 仍允许；返回查询并结算 55,932 后余 28,046，下一次 80k 预留必被拒绝。直接原因不是查询额满、final 被隐藏或新窗口到期。这是可定位的收尾设计缺口；模型为何选择查询、若换提示能否提交成功，均属未知。全阶段暂停符合原合同，而已知单题停止到下题的管理路径未接通，进一步使三个原任务停留未开始。[预算审计][budget]、[治理语义][stop]
3. **完整记录与每轮工作输入耦合，代价重复出现。** 旧开发批次输入约占 96.3%；本次占 98.086%，末 prompt 的 public_history 占字节 92.224%。12 个候选没有重复槽位/重复计算证据；主要可见成本来自逐轮再读公开工具历史，不是已证明的模型长篇自述或隐藏思维回传。由此推断，研究状态表示与调用粒度值得优先反思；但还没有证据证明压缩、批处理或短上下文能在不漏反证的条件下提高质量。[旧成本][batch]、[新成本][budget]
4. **工程优先级和迁移复杂度挤占研究闭环，这是工作方式层面的推断。** 完成识别、未知债务、同库原子预算、旧许可不可改、双库动作和新时间资源确有真实边界需要处理；同时，不断增加隔离修订、来源核验、监督器与显示修复，仍未把普通输入到可信执行再到最终报告连成已验收的主入口。原六小时仅两次传输/零终稿，新窗口最终仍无 final，支持“交付速度不足”的判断；不能据此证明某个更简单架构必然更好，或把工程审查本身全部归为浪费。[迭代记录][journal]、[实际保存应用][saved]、[策略仍未接线][strategy]

成本口径必须分开：旧 8 run 报告 3,262,406 研究 token；两轮诊断报告 553,230；当前四题参考域 491,954，后两域合计 **1,045,184 已报告 I/O token**，另留 80k 未知预留。P0 的 61,758 是原 Q 的历史引用，不再次计费。工程平台目标计数在已保存 04:00:14 UTC 快照为 **13,034,382**，日志后报 05:06:57 UTC 为 **13,754,340**；它包含主控/实施/协调等平台范围，不是逐工程任务净增量或供应商账单，也不能与研究数字直接相加。没有精确货币账单，不能据这组异口径数计算 ROI 或节省比例。[诊断用量][diagnostic]、[首题用量][outcome]、[工程快照][engineering]、[后续日志][journal]

## 对主控复盘稿的交叉核对

已核读 `docs/META_V2_WAVE_RETROSPECTIVE_20260907.md`，当时 SHA256 `f198af26954cd0eb22b28c5fe17700c4d8dc6f07c38c51e148dc05e6af0d5e59`。主要数值、旧批总效果/机制效果区别、最新 baseline 未完成和无正式成功结论与上述来源一致，未发现需要拦截的过度因果结论。末尾工程收尾当时仍待补充，本审查不提前认证。

保留两条精度要求：写“工具已实现”时同时保留实际使用/尚未接线范围；写“更多输入导致成本”限于保存 I/O 与字节结构，不推测模型动机或承诺反事实 final。纯 closing policy 的离线重放在 t14 首次 close_only，只说明拟议控制门何时改变；它尚未集成、没有产生 final，更未改变后三题原合同。[关闭策略工程报告][closing]

两问自检：**这一步确认了什么？** 可靠性、可追溯性与两个有限诊断实例有实证；主循环替换、稳定发现能力和真正策略目标尚无足够证据。**还应避免什么？** 不以版本/测试数量替代研究成效，不把模型完成、工具完成和有效终稿混为一谈，不把未完成合成参考外推为架构因果失败；本次收尾不追加功能或付费研究。

关键已读文件字节 SHA256：旧开发审计 `69ce18426ec4d08b5249a5e468b139181b8703a15a5576884826c0f660b3e094`；P0 报告 `26131c6d5f97543a6396eac510109cec58dba461e6046c92ece6ba7abba76ecf`；v12 内容审查 `4fd5c32160c6eb5aa581b2d2649db4deb5b7b92ddbe7aa887e8ffdd5f165f100`；首题终态 `624aab03b83a3d10be6ba9f83bf2a351e520c656be73308ecc036c1608eca7fe`；预算审计 `e932caf2f556d6ccc3e40c3b33a9c18bdb3cff33ae4112982df73ff3687f846e`。当前原 workflow/planner/YAML 分别 `566049946ab0fd090ffbc2bddda8aa1b6e607c008e0884c3a599fccc45271edf`、`85ef2a8ed74ce4bb28d75b217134f906c3ac417793f36efddf9868136923c326`、`214f31435f7f897ed975ada52dac8d58932149016f9a5000345154f845fc522d`。日志是继续追加的索引，不把旧快照里的动态状态当永久当前值。

[goal]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/META_FRAMEWORK_IMPLEMENTATION_PROMPT.md
[journal]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/META_ITERATION_JOURNAL.md
[legacy]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/RESEARCH_LOOP_V2.md
[planner]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/src/quanta_agents/agents/research_planner_v2.py:1222
[workflow]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/src/quanta_agents/workflow_v2.py:3049
[master]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/META_FRAMEWORK.md
[p0]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/META_REVISION7_P0_REPORT.md
[diagnostic]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v12_handoff/interface_recheck_content_review_001.md
[outcome]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v18_handoff/actual_case01_final_review_001.md
[strategy]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v17_handoff/strategy_development_001.md
[saved]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v17_handoff/actual_saved_action_outcome_independent_review_001.md
[stop]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v18_handoff/task_budget_stop_semantics_001.md
[raw]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v13_handoff/raw_union_independent_001.md
[obligations]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v15_handoff/raw_obligation_scope_review_001.md
[batch]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/META_ASHARE_DEV_BATCH_01_REPORT.md
[redesign]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_redesign_20260906/report-source.md
[rubric]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v15_handoff/reference_evaluation_prereg_001.md
[contract]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v10_handoff/submission_contract_audit_001.md
[schema]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v15_handoff/reference_schema_failure_review_001.md
[budget]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v18_handoff/research_loop_budget_audit_001.md
[engineering]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v18_handoff/engineering_usage_snapshot_001.json
[closing]: /Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_research_closing_policy_v19/closing_policy_report.md
