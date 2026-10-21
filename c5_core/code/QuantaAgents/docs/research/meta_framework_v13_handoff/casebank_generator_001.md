# v13 四个合成机制开发包：生成工程 001

已完成一次实际生成并冻结四个开发包，每题 192 个会话、8 个合成股票、两个各 96 会话的公开开发块。共 6,144 个股日记录全部保留，未按结果筛题、删行或换种子。它们仍是 **exposed synthetic development 工程题**；尚无独立同信息、同工具、同成本和同预算参考复现，因此 `hidden_control_qualified=false`，不占用“已合格隐藏正控”或真实策略成功分母。

实际产物目录：`experiment_traces/meta_casebank_development_v13/attempts/20260906T184928Z`。

- [公开清单](../../../experiment_traces/meta_casebank_development_v13/attempts/20260906T184928Z/public/manifest.json)及 `public/case_01` 至 `public/case_04`：每题只有普通 packet、固定 sessions 和原 observations.csv。
- [私有生成计划](../../../experiment_traces/meta_casebank_development_v13/attempts/20260906T184928Z/evaluator_private/generation_plan.json)：随机种子、角色映射、机制族、参数、抽样顺序、分块和机械验收政策。
- 每题 `evaluator_private/case_*/specification.json`、generation_draws.json、quality.json，以及独立保存的 generator_source.py；[生成回执](../../../experiment_traces/meta_casebank_development_v13/attempts/20260906T184928Z/evaluator_private/generation_receipt.json)绑定全部 20 份私有原件哈希。

机制设计不在公开题号/字段中标明。两类正控候选分别是单个离散状态的滞后响应、两个离散状态的交互响应；它们不是同一公式换 seed。负控是共同价格路径：同日期、同总风险敞口、同端点及同费用/换仓条件下，横截面选择没有相对同日等权基准的增量。这个范围不排除择时、杠杆、外部信息或任意反推 PRNG 的方法，不能概括成“任何策略都无优势”，也不依靠低功效的不显著结果下结论。

失效题在公开块 A 有固定状态响应，块 B 的决策不再带状态响应或股票特异创新。按决策 t 归属块，作用是 close t+1，即 open t+2/open t+1；跨块窗口没有删除，边界不能按结果移动。有限精度价格的预注册回报容差为 1e-24。此设计说明属于 evaluator 资料，不是给研究器的提示或参考解；生成计划没有替代独立发现证明。

本次生成先以一次 256-bit 随机熵确定四题实现和中性题号映射，将完整计划与源哈希写入并 fsync，再单独执行 generate。生成器不读生成质量以选择新 seed，也没有接受 seed 搜索的命令行参数。generation_started 一旦存在就拒绝再次生成；故障会保存开始记录、已生成文件、原种子和 failure.json，不能自动覆盖。此处证明本地执行顺序和内容绑定，`chronological_commitment_verified=false`；没有可信时间戳、WORM 或操作系统权限隔离的认证。

公开普通任务说明、四个中性状态字段的数据字典、开发块、允许动作与成本合同对四题完全相同。状态在同日 15:01 声明可用，决策在该时刻、入场最早在次会话开盘；这些时点只是合成声明，不认证真实历史 PIT。成本合同明确佣金与滑点每侧各 0.0003、按同日同敞口及相同收费换仓比较；本次没有模拟真实股份、费用执行、容量或策略收益。

接口分离：`casebank_generator.py` 仅供 controller/evaluator 生成；`casebank_public.py` 不导入它。`load_public_case(root, case_id, *, expected_public_manifest_sha256)` 只接受四个固定中性 ID，要求 controller 先固定公共 manifest 哈希，核对 packet/sessions/CSV 三文件白名单与哈希，返回公开 JSON/bytes，不返回私有路径、角色、seed、机制或生成器。实际公开包已全部通过该读取器；公开读取时触碰私有目录的反例会失败。但同用户文件系统和 Python 对象本身不是答案安全边界。未来若做隐藏评估，还需可信 controller 的实际能力/权限限制。

所有公开包的工具字段标明 `contract_only_not_connected_to_research_harness`：计划统一 inspect_inputs、共同 [1,5,10] 期限、离散条件检查和最终报告；最多 16 个公开查询、50 行/页、12 个候选规格及一个 final。当前未实现这四题的受控工具适配与提交 schema，也未注册进旧 DiagnosticWorkbench 或 live harness。这里冻结的是后续同预算参考工作的接口目标，不是已经能派发的功能，更不是新的付费许可。

机械验证为 13 项专属测试通过；实际生成 1 次、seed 替换 0、4/4 机械质量通过。每题 1/5/10 期限的尾部缺端点机会分别为 16/48/88 行，均仍在原数据；跨 A→B 的端点窗口也分别记录同样数量，不能静默删除。检查只核对完整唯一网格、正价格、前收盘到次开盘连续性、声明可用时点、公开合同一致和哈希。没有跑机制拟合、参考策略、模型研究或回测；本工程模型审阅本身仍由 Astra/xhigh 完成，其用量与研究账本分列，不能说成“人工无模型审核”。

这一步的结果与不足：生成与界面白名单已具备检查材料；原机会、失败路径、随机计划均可追踪。尚未证明两个正控在规定预算内可由独立研究器发现，也没有给出候选发现率、误报率、正确弃权率或执行有效率。本次全部开放为开发信息，不可随后改名为未见留出。

下一步应先独立检查普通包与时点、负控基准、失效边界及适用假设族，再实现同一公开接口的离线参考重放。参考角色只能拿普通包、允许工具与成本合同，不能读取 specification、seed、生成器或私有 draws。若公开信息不足、同预算无法复现、负控出现超出定义的增量、边界被错误归属、权限能读私有材料，保留原包并判该资格门未通过；不能追加私有解题提示后声称可达。修订必须另版本、记录额外搜索和暴露，不因结果不漂亮补抽随机实现。当前不推进付费或 8×2 的筛查。

冻结身份：generation_plan SHA256 `f656d8b06ca808602d16ac006e83de051c48a14dae196d4d2c33ea3fcd5844db`；generator 源 `d2a5990b2ec2906ba0010bba8c59fa48134fb9538625d4c629593037f5f7f0a7`；public manifest `e728fa67c052d8af1f4133a5f9cc29dfe9b18772dbd4f244d088d0b08396b797`；generation receipt `6e3ee10a4d378ea55c1eeec07d4bf78442511bcd2d1c65fe85f247061ff174a5`。这些是内容身份证据，不是收益或隐藏题资格证据。
