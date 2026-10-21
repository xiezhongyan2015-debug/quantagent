# v13 合成开发题生成工程：独立审查 001

2026-09-07，Astra/xhigh。**四个原始 exposed synthetic 开发包的来源、完整分母和本次机械时序反例通过；审查发现的读取上界、公开根目录重定向和一类失败回执遗漏已由作者修复，最终 10 项独立检查通过。** 这不是独立同预算参考发现，也没有资格把两个正控候选登记为合格 hidden 正控。工具适配、实际模型能力及策略执行有效性仍未证明。

本审查由 root 单独分配，与生成器作者分离。只新增 [test_meta_casebank_independent_v13.py](../../../experiment_traces/meta_ashare_revision13/tests/test_meta_casebank_independent_v13.py) 和本文；作者自行修改自己的模块和专组。没有修改任何生成记录、重抽真实生成 seed、生成第二份实际题库、调用研究 Gateway、读取真实行情或运行策略回测。工程与审阅自身使用模型，其平台用量非零，不能计成项目 Gateway 研究调用。

## 已核验的原始对象

唯一实际生成目录：[20260906T184928Z](../../../experiment_traces/meta_casebank_development_v13/attempts/20260906T184928Z)。审查读取了生成器源码、公开读取器、作者专组与脚本、[作者说明](casebank_generator_001.md)，并在原目录只读核对：

- 生成 receipt SHA256 `6e3ee10a4d378ea55c1eeec07d4bf78442511bcd2d1c65fe85f247061ff174a5`。
- 原 plan SHA256 `f656d8b06ca808602d16ac006e83de051c48a14dae196d4d2c33ea3fcd5844db`。
- public manifest SHA256 `e728fa67c052d8af1f4133a5f9cc29dfe9b18772dbd4f244d088d0b08396b797`。
- receipt 指向的 20 份 private 原件、13 份 public 文件（含清单），以及原生成源码归档均保持原 hash。
- 1 次实际生成、0 seed 替换、4 个包；每包 192 会话×8 中性符号=1,536 行，总计 6,144 行。原坐标网格无重复、删除或删尾，两个各 96 会话的公开开发块完整保留。

作者冻结一份新目录后只抽一次 256-bit entropy，派生四个实现的 seed 和角色顺序；源代码不按数值质量选择 seed，也无 seed 搜索命令参数。`generation_started` 独占写入后占用该目录的唯一生成槽，已完成或部分失败均不得自动重生。这个控制覆盖该目录和正常 API；它不认证任意用户从未在别处创建其他目录，也不提供外部时间戳或 OS 权限隔离。原 plan 的本地顺序声明继续为 `chronological_commitment_verified=false`。

## 实证发现与最小修复

root 首先指出读取上界和 Windows junction 语义的风险，我独立构造了反例；另发现源码归档失败不在错误处理范围内。初始选定四项实际运行结果为 **4 failed，6 deselected，4.08 秒**，作者随后也独立复现。没有把静态推测写成已经发生的生产事故。

| 反例 | 修前观测 | 作者修复与复核范围 |
| --- | --- | --- |
| stat 声称文件超过 4,000,000 字节；读取守卫禁止预先完整读取 | `read_bytes()` 先执行，随后才检查 len，守卫报错。 | 增加 stat 预检；超界文件在分配内容前拒绝。 |
| stat 后内容增长；模拟 stream 拒绝无 size 的 read | 原读取实际调用 `read(-1)`，因此仍是无界分配。 | 使用 `open('rb').read(MAX+1)` 并检查实际长度。只产生至多约 4 MB 的测试内存，不创建巨型真实文件。 |
| 模拟 Windows junction：is_symlink=false，public 的 resolve/open 被映射到临时 private/copied_public | 把 `public.resolve()` 自身视为允许根，原读取成功。 | 先要求解析后的公开根等于控制器指定的 `root/'public'`，再做现有子路径约束。反例没有创建真实 junction，只复制既有公开字节到临时目录；没有发现原实际包泄露了私有答案。 |
| generation_started 已写，归档 generator_source.py 注入 OSError | seed、开始记录和目录保留，但缺 generation_failure.json。 | 将归档写入移入 try，记录 completed_cases=0 的失败；原计划不变、entropy 仍仅一次、之后再生成被拒绝。 |

这些反例阻止了对通用 loader 的资源/静态路径边界及完整失败日志的过度声明。当前真实四包是小文件、正常目录且生成成功，初始发现并未证明这些原包发生过泄漏、重种或内容损坏。修复没有重写任何原生成文件。

原实际题库由旧 generator SHA `d2a5990b2ec2906ba0010bba8c59fa48134fb9538625d4c629593037f5f7f0a7` 生成，完整归档仍在原 private 目录并与 plan 相符。修后的当前 generator 是另一个源码身份；不得把旧 receipt 追溯改成由新源码生成。新修复改变错误处理范围，不重新计算四题数据。

## 普通输入、时点和分母

四个公开 packet 的 ordinary_input、四个中性状态字段字典、基准、工具合同、成本合同和公开块定义相同。公开加载器只接收固定 case ID 和可信 controller 提供的固定 manifest hash，白名单为 packet/sessions/CSV。AST 检查确认其没有导入 generator/evaluator；实际四包读取守卫逐次检查全部 16 次文件打开都在 public 目录中。公开返回中没有实际 seed、机制族名或题号到角色的映射。

公开说明允许研究者调查事前变量和后续相对表现、检查两开发块；不提供哪一列是答案、交互项、私有系数或该题是否为正负控。审查者因 evaluator 审查权限看过生成规则，不能随后充当独立 public-only 参考解。本文不公开实际题号与机制角色的对应关系。

时序另用完全披露的三会话固定 draws 验证，不执行发现或拟合：改动 decision t 的状态，只改变其后 close t+1，当前 close t 与下一入场 open t+1 保持原值；再往后的状态不能改变更早价格。该路径独立以 Decimal 字面值核对，未调用拟合/回测工具求 expected。真实四包的声明时间为同日价格 15:00 可用、状态与决策 15:01，可入场点为次会话开盘；这些仍是 synthetic 声明，不能认证真实历史 PIT。

按真实公开坐标逐项计数，1/5/10 期限各有 1,520/1,488/1,448 个可能完整端点机会、16/48/88 个缺尾端点机会；完整加缺尾始终等于原 1,536 行。信号归属 A 而退出到 B 的窗口分别为 16/48/88 个，仍在原资料中。这里明确按信号到退出的跨块定义，不能因入场已经在 B 就事后移动失效规则或删除边界数据。

负控只声明：共同比例价格路径下，同日期、同端点、同总敞口的规范横截面选择，相对同日等权基准没有毛收益增量；若还要求同样收费换仓，两者成本条件才相同。独立机械检查在该范围及失效题 B 决策后的三期限中，按 Decimal 60 位检查所有股票比例回报一致，差异不超过原预注册 1e-24 容差。它不是“绝对收益为零”，也不排除择时、杠杆、不同费用换仓、外部信息或不受限 PRNG 反推。

原两种正控候选具有不同设计族，但本次没有算其可发现率、收益、显著性或强弱排序；也没有利用 private 机制替研究者复现答案。失效边界按决策时点固定，B 中移除状态响应和特异创新，跨块窗口未剔除。以上是生成机械一致性，不能替代随机机制族泛化或未暴露样本上的独立发现。

## 仍未成立的资格与边界

- `tool_contract` 是 `contract_only_not_connected_to_research_harness`。inspect、期限、条件和最终报告动作尚未为这四题接通受控适配和提交 schema；16 查询、50 行分页、12 候选和 1 final 是后续合同目标，不是本次已执行的研究预算。
- 成本为未来参考声明，尚无真实股份、换仓成本、容量或日线策略执行复算；不能计算真实策略成功率。
- 独立同信息、同工具、同成本和同预算 reference 尚未运行，两个正控仍是候选；四题 `hidden_control_qualified=false`、strategy success 分母贡献 0、execution_valid=false。
- 固定公开根和内容 hash 加强正常静态读取边界，不是操作系统沙箱；未认证同用户恶意进程在检查与打开之间变更目录的权限隔离，也未证明此前没有其他观察者看到机制。
- 本次已经暴露给工程作者和审查者的资料，不能后改名为未见任务留出。正式比较依然需要研究机会、成本和暴露记录全栈冻结，不能仅据“题库生成通过”宣布 P4 公平或架构改善。

## 最终定向检查与身份

在项目根使用 v13/src 的 `PYTHONPATH`，仅运行 `.venv/Scripts/python.exe -m pytest experiment_traces/meta_ashare_revision13/tests/test_meta_casebank_independent_v13.py -q`。修复后结果：**10 passed in 3.08s**。中间曾单跑其余 6 项并通过，不能与最终 10 项相加。作者自己的 13 项及主控未来联合验收另以其原回执计数，本审查未跑全量。

| 文件 | 最终 SHA256 |
| --- | --- |
| casebank_generator.py（修后当前源码） | `1b6c300fb1dd2416de1841502f5a8008f0aeb67cec6191f6c9ffc6ced7e4daed` |
| casebank_public.py（修后） | `44387f637d7bfbb78a11c6cf415037d2f2b7624e8c976b8d13b5de56d3dc5695` |
| generate_development_casebank.py（未改） | `48fd7acbe0542a5dbca0039554d8884214266fc72fd70e4a07d0362063a6f699` |
| 作者 test_meta_casebank_generator_v13.py | `c4280e8ab81d9f1f9bdf08cbcace3a9c0d21e124839922535740626d194809e7` |
| 独立 test_meta_casebank_independent_v13.py | `fdc4ff8505c6644a9cd33da4b217a34814db5a67ccd33a6570e074bcf0e9c405` |

**这一步做得怎么样？** 通过独立来源/网格/时序/隔离反例审查，发现并复核三类真实工程缺口，保留修前失败和实际原生成来源。没有把已知数据、隐藏参考或机械相等混成研究成功证据。当前本范围没有待修复的已知阻断，通用工具适配与正控资格仍明确未完成。

**下一步该做什么，如何改进？** root 在同版源码冻结后做受影响联合验收；随后最有信息价值的是为四题实现统一、有限的公开工具与合法提交契约，再安排另一个仅拿普通包的参考角色。当前审查不执行该参考，不派发模型，也不授权补抽实现。若公开工具读到 private、读取重新无界、失败能重种、尾部/跨块行丢失、作用时序提前、对零横截面增量作无限制外推，或同预算参考必须读私有机制才能成立，则相关资格门失败；保留原包及否定证据，修订另记，不回写旧结果。
