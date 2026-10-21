# QuantaAgents V5：以异质性证据驱动研究迭代

用户于2026-09-08要求结合正在进行的V4开发制作V5。本版解决已确认的缺陷：当前窗口总分主导候选筛选，跨年/跨标的差异尚未成为观察、反思、修订和采纳的完整输入。工程完成不代表已发现盈利策略。

## 与V4的分工

保留V4完整资金与日历、真实股数/公司行动/税、T+1、交易限制、来源身份、失败恢复、成本预留、一次性正式托管以及条件风险工具。V5通过新包和新入口复用，不改旧冻结运行，不替换V4正在开发的执行内核，不释放正式留出。V4另一任务已收到协同消息。原c007六年研究仅作为已暴露的诊断接入示例，不能作为V5独立金融验收。

## 本版必须完成的改动

1. **统一稳定性证据。** 每个候选按原范围保留所有单元、完整日历和现金日，按连续净值拆年，显示同口径基准增量、Sharpe、回撤、仓位、费用、缺价与年度贡献。缺单元/缺年/缺基准明确不可评价，不缩分母；重叠股票/年份不当独立样本。
2. **分开研究资格与正式通过。** 保留覆盖、来源质量、异质性、相对增量和金融执行的独立状态；开发稳定性筛选不能写正式通过。配置门在研究前固定并哈希；本次针对已暴露c007的政策只作事后描述，不宣称原先预注册。
3. **强制差异反思。** 绑定候选程序及诊断哈希，引用具体年度/标的单元，提出竞争解释、未知、可否定预测及区分实验。允许机制未知的异常探索。不能以年份编号作为可交易解释，不能删亏损年份或股票来完成反思。
4. **结构化修订比较。** 原候选和修订必须使用同一完整范围、日期、基准及成本口径；逐格保存改善、退化和未知。没有逐项回应的年度退化不能被总分上升覆盖。科学解释由研究者提出，机器只校验绑定、完整性与声明，不能把文字齐全当机制已证明。
5. **研究状态与下一步调度。** 将缺证、需解释的差异、待检验预测、已知反证、停止理由保存为可追溯状态；优先补足必要证据，再安排已声明的区分实验。避免凭ready列表顺序或高总分自动反复优化。
6. **冻结范围与暴露谱系。** 仅消费控制器登记的development资料。反思过的年份记录为已暴露；确认/最终资料不可供模型逐轮优化。父候选、程序身份、诊断、反思和修订一并绑定。旧记录保留，新范围/新政策另登记。
7. **接入实际V4循环。** 新V5 runtime/tools在原预算、恢复和执行边界上增加稳定性动作，向模型提供可分页读取的完整诊断与有界状态摘要；提交开发候选须满足V5证据要求，缺证应申请扩展或弃权。不能只增加提示词或离线报表。
8. **可复现验收。** 用高总收益但跨年退化、单年驱动、跨股票集中、缺基准、篡改、隐藏资料泄露、修订损害旧年份等反例测试。真实c007保存路径接入年度诊断，核对原独立结果。模型/金融优势仍需之后的新冻结研究。

## 实现边界

新代码：src/quanta_agents/meta_v5；新命令：scripts/run_research_v5.py及V5示例工具；测试：tests/test_meta_v5*。本版不新增第三方依赖。默认只准备/检查/离线验证，付费模型执行使用显式run命令与V4既有身份、预算和工作租约。真实执行仍由V4被准入的后端负责。

年度稳定性不等于每年收益为正。需要共同基准增量、风险、样本量和适用条件。跨标的稳定性不要求所有股票均有效；适用范围必须在看该次结果前固定。时间分组和市场状态解释不能把未来标签变成交易特征。

## 模块接口（本轮开发前约定）

`analytics.evaluate_bundle(bundle, policy)`返回完整profile及其hash。bundle.version=`v5_stability_bundle_v1`，candidate_id/program_hash/scope_id/split必须绑定；split只允许development。expected_units为固定有序名单。pairs每项含unit_id、unit_kind(stock/portfolio/index)、calendar、initial_nav、candidate与benchmark两条序列。序列含nav，及可选exposure/fees/stale（等长）；nav允许None表示未知，不能插补。pair可含source_quality文字说明。bundle含provenance（包括accounting_mode、execution_certified布尔、exposed布尔、source_hashes）。policy为完整显式对象，不靠函数默认值隐藏门。

profile至少含version/candidate_id/program_hash/scope_id/bundle_hash/policy_hash/profile_hash、cells（cell_id=unit_id:year）、full_period、summary、issues、development_eligible、formal_target_success=False。cells包括unit_id/year/sessions/complete/candidate_metrics/benchmark_metrics/excess_return/quality_flags。指标至少return/sharpe/max_drawdown/average_exposure/fees。summary包括年度覆盖、标的覆盖、逐年配对超额分布、各单元全期相对收益；描述不能当独立显著性。issues含id/severity/code/cell_ids/message，允许反思精确引用。`compare_profiles(old,new)`核验同scope/policy、单元/日期/成本/基准身份；程序可改变，未配对不能声称修订改进。

`reflection.validate_reflection(declaration, profile)`验证证据、竞争解释与下一实验结构并返回有hash的记录；记录是研究者声明，causal_mechanism_identified=False。`reflection.next_actions(profile, reflection=None)`返回确定性优先序（补证、差异实验、确认准备或弃权），不是主控代写科学结论。

`register_stability_revision`把最新反思、具体区分实验与后续原生V4参数一起冻结；执行必须带同一登记的`v5_revision_evidence_id`。它不能覆盖V4原生参数/预算校验，已经停止或被补证阻塞的实验不能执行。首批全部执行候选都要先完成诊断和反思；初始基线按最早执行顺序确定，不能在看过结果后改成第一个被挑选来诊断的赢家。

根代理负责持久化注册/工具/runtime/示例/最终集成；子代理分别负责analytics、reflection及独立对抗审查。实际文件、验收及尚未完成的V4依赖见README.md。
