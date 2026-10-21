# 完整回答后本地进程异常退出：独立恢复审查

日期：2026-09-06。范围限 revision5、后续隔离 revision6 的 `meta/codex_gateway.py`、对应 `tests/test_meta_gateway.py` 与调用方 `meta/runtime.py`。父任务中提到的 `test_codex_gateway.py` 在当前目录不存在，实际文件名是 `test_meta_gateway.py`。

本轮没有调用模型、启动 Codex 子进程、联网、修改旧批次状态或业务源码。复现使用完整网关 `run` 方法，仅将进程创建替换为内存中的假进程；标准输出、响应文件、非零退出与事件回调按真实接口输入。

**V5 修后恢复门已通过独立场景复核，三个发现的问题及随后发现的最终用量缺失/冲突问题均已修正。** V5 最终证据为 `review_v5/after_fixes_2/result.json` 与 `validation.json`，源码 SHA256 `6f4720579d456b6a014cbb7a30d116300831e204215800010393fce2e33eab72`。合计 36 场景包含 **33 个当前网关场景和 3 个单行重构的旧漏洞场景**，全部符合预先列出的应接受/应拒绝结果。V5 冻结后保留的意外第二轮计费边界，已在隔离 V6 中修复并单独验证，详见下文；没有回写 V5。

**版本边界：V5 已冻结，未部署。** 已检查其 `validation_receipt.json`：561 项联合回归绑定预算修复前的源码集合；预算修复后是 45 项 gateway/runtime 定向测试，不能合称“最终源码一次通过 561 项”。V5 中意外多轮的残余计费限制保留原样，父任务在隔离 V6 修复；本报告不会修改 V5 业务源码，也不会覆盖 `after_fixes_2`。

## 首轮复核结论与修复请求

首轮检查身份为网关 SHA256 `24bf45e6ca2ea2de4bb7259f1b49de873a9d92d731243143075d2f23d6a93c1b`。证据目录为 `experiment_traces/meta_completed_response_recovery/review_v5/`。

**保留已经完整输出并付费的回答这一方向可行，但恢复门必须绑定同一轮的完成与最终用量，不能只看历史上出现过完成和两个非空 token 字段。** 本轮报告给父任务三个具体问题：

1. `turn.failed`、`response.failed` 或顶层 `error` 的消息为空字符串时，原 `if parser.failed` 判断跳过失败。父任务已经改为 `parser.failed is not None`。当前 3 个空消息失败都被拒绝。为保留修前证据，复现脚本仅在内存中把该一行还原，三个场景均会错误恢复；这明确属于单行重构复现，不冒充保存下来的历史业务源码。
2. 完整回答及 `turn.completed` 后再出现新的 `turn.started`，第二轮尚未完成就异常退出。首轮代码保持 `turn_completed=True`，错误恢复前一轮回答。需要在新一轮开始时清除前一轮完成/最终文本证据，或者显式拒绝未完成的后续轮次。
3. 前面 `token_count` 给出 input=1/output=1，最终 `turn.completed` 只给 input=37006。首轮代码保留旧 output=1，错误把部分旧用量当成完整最终用量并恢复。这会低记付费成本。异常退出恢复应要求完成事件自身完整、合法的 input/output，或有明确关联的最终用量证据，不能拼接旧快照补缺。

父任务已完成第 2、3 项修复，并在新的证据目录复核：完成之后的 `turn.started` / `response.created` 直接拒绝；完成时分别保存 `completion_agent_text` 与 `completion_usage`，异常退出恢复要求该完成事件自身有合法完整 I/O、与最新 I/O 一致，回答必须在完成前存在且完成后未变。恢复时返回并重发完成事件用量，先前的缓存/推理字段不补入最终事件遗漏字段。

## 已验证的真实旧调用

固定批次 `dev_20260906_01` 的失败 entry04 为 `meta-20260906-024934-8f879e`，调用 `baseline_round_7-1`。保存的 stderr 包含 `memory allocation of 135184 bytes failed`；原调用账本记录退出码 3221226505、状态 failed、receipt=null。原事件流存在完成事件，已保存 JSON 与最后 agent_message 可一致解析，I/O 用量为 **37,006 + 968 = 37,974 token**。

独立复现将这份已保存事件流、response 和 schema 喂给假进程，网关恢复成功，调用方现有 schema 校验通过，保留原退出码并输出恢复警告，过程仅创建一次假进程。`model_verified` 仍为 false：执行请求锁定 Astra/xhigh，提供方事件没有完整确认身份，不能因回答内容提到模型名称就改称已确认。

复现前后检查 response、events、stderr、schema、request 五份原始文件的 SHA256，均不变。**旧批次仍保留其失败结果与费用，本轮不重写历史成绩，也没有为补齐该轮重新付费。** 该实验只证明新门槛对已知事故证据的处理行为；不能证明所有非零退出都属于同一种内存故障。

## 31 个离线场景的证据范围

首轮共 31 个网关场景，每个均只有一次假进程创建：

- 匹配的完成回答与真实旧调用允许恢复，保留退出码、usage、警告和身份验证状态。
- 普通/空消息 provider failure 均拒绝；工具事件、错误模型、错误 effort 即使出现在完成之后也拒绝。
- 缺完成、缺最后流式回答、缺 response 文件、坏 JSON、两份内容不一致、布尔与整数不一致、完成后坏 JSONL、取消均拒绝。
- 完全没有 usage、缺 output、input 为布尔/负数/浮点数且没有可借用旧 usage 时，均拒绝。
- 已报告的“后续未完成轮次”与“拼接旧用量”两个场景在首轮错误接受，不能把整个矩阵汇总成全部通过。
- 两份 JSON 一致但内容违反业务 schema 的场景，网关会恢复回答，**现有调用方 schema 验证仍拒绝**。恢复不应把这种回答直接当成有效策略。

原始结果在 `review_v5/result.json`，每场景原始模拟事件和响应保存在 `attempts/`。复现代码为 `review_gateway.py`。后续复核必须写新的子目录，脚本不覆盖已有尝试证据。

修后另加 5 个场景：新的 `response.created`、完成后 usage 改变、完成先于最终回答、最终回答事后改变，均拒绝；早期 cached/reasoning 明细在最终事件缺失时保留为 null。原两项错误接纳已变为拒绝，真实事故调用仍正常恢复。`validate_after.py` 对保存的全部结果逐项断言接受集合、schema 结果、身份标签、一次假进程、警告数以及实际 I/O 用量，通过后分别生成 `after_fixes_1/validation.json` 与 `after_fixes_2/validation.json`；第二次还明确断言缺失 output 和冲突 I/O 均为 null，不是只看脚本退出码或打印内容。

## Schema、预算与重试语义

`CodexGateway` 原本只检查最终 JSON 是对象，实际业务 schema 验证在 `runtime.validate_response`。异常退出恢复仍走同一返回路径，没有修改 schema 定义或跳过校验。独立复现使用从当前 runtime AST 提取的原验证函数检查真实响应和越界反例，没有依赖另写的宽松校验器。

`Engine._model` 在取得回执后写入 receipt，保存用量，再执行 schema 校验；失败时调用状态记录为 failed，成本仍保留。流式 usage 回调也持续写账本。输出 token 已包含 reasoning，预算累计 input+output，不额外加 reasoning。网关恢复警告不创建新的调用或叠加一次费用。

恢复只复用本次调用已经出现的产物。网关没有重试循环，尝试目录已有产物会拒绝重新覆盖；Engine 对同一步已存在 failed/running/uncertain 调用继续阻止自动重试。已完成且提示词哈希相同的结果仍可走原有回执复用。非零退出被确认恢复后，后续研究可以继续到下一步骤，但它不是“重新调用本轮一次”。

取消、超时、工具策略、模型策略和 provider failure 的异常先于恢复分支发生。当前取消案例证实取消优先；超时的具体行为来自现有网关测试与源码审查，本独立矩阵未再制造真实等待或启动超时进程。

**最终用量缺失/冲突的预算问题已关闭：** `after_fixes_1` 的“先有旧 output=1、最终完成事件缺 output”虽然已拒绝恢复，异常却携带合并后的 input=37006/output=1。对原 `Engine.usage` 函数的离线调用证实会得到完整 37007 token、unknown_calls=0。父任务随后在完成事件处用该事件自身 usage 整体替换缓存，并在拒绝恢复时把后来有冲突的字段改为 null。

`after_fixes_2` 中同一场景的异常为 **input=37006/output=null**；同一个原 `Engine.usage` 聚合函数返回 **known total=37006、unknown_calls=1、is_partial=true**。这会触发原有 `unknown_calls × reserve_tokens_per_call` 的预算预留。完成后 I/O 都改变的场景，两字段均为 null。最终明细缺失不会继承更早快照中的 cached/reasoning。真实旧调用的 37006/968 仍完整恢复。前后证据分别是两个目录的 `failure_budget_observation.json`，没有覆盖前一轮结果。

**另外保留一个意外多轮计费边界：** 完成后收到新的 `turn.started` / `response.created` 会正确拒绝，但在当前 `6f472…` 源码中，这个提前异常携带的仍是第一轮完整用量。假如第二轮已发生额外消耗，整次调用会被标为完整。`after_fixes_2/new_unfinished_turn_after_completion_budget_observation.json` 对原 Engine 聚合器确认这一标签行为。它不是本项目当前 CLI 正常单轮输出，也不影响真实旧 round7 的 37974 用量；已经向父任务建议将这种协议异常也交给未知预留，或明确只把已确认第一轮计作成本下界。本报告不把这个异常计费边界写成已解决。

上段是 **V5 冻结状态的结论**，不是 V6 当前结论。两版的区别如下。

## V6：意外多轮成本未知化，独立验证通过

V6 最终只读复核身份：

- `meta/codex_gateway.py`：`b8bdbb4458c5c21b3cc7d64f45647140a4c86fd33a7ab4037857c6a4cee7df10`。
- `meta/runtime.py`：`80cf90000c2454d40b2b4e49d64e532b2c83504671fa5222b76a8bf98db0018f`。
- 最终证据：`experiment_traces/meta_completed_response_recovery/review_v6/identity_verified/result.json`。复现脚本为同一 `review_v6` 下的 `review_gateway_v6.py`。脚本显式核对所加载源码在运行前后未变。

新一轮意外启动的拒绝路径，先保存完整的上次完成用量，再把整次调用的 output 标为未知。审计字段 `confirmed_prior_turn_usage` 来自 `completion_usage`，`latest_observed_usage` 另存最新观测计数，避免把完成后新来的计数误称为前轮已确认用量。

4 个有界场景的断言均通过：

| 场景 | 回答处理 | 当前 Engine 聚合结果 | 保存的证据 |
| --- | --- | --- | --- |
| 完成后 `turn.started` | 拒绝 | input=37006、output=null；unknown_calls=1、is_partial=true | 完整前轮 37006/968 仍在原 JSONL、usage 回调与审计 warning |
| 完成后含 response 身份结构的 `response.created` | 拒绝 | input=37006、output=null；unknown_calls=1、is_partial=true | 同上 |
| 完成后先更新计数为 38000/1000，再新轮启动 | 拒绝 | 保留最新 input 下界，output=null；unknown_calls=1、is_partial=true | confirmed prior 仍为 37006/968，latest observed 单列 38000/1000 |
| 原真实事故 round7 保存产物 | 恢复且原 schema 通过 | 完整 37974 token、unknown_calls=0 | 原异常退出码、恢复警告、model_verified=false 均保留 |

这次没有为了“保留已知费用”而把额外轮次未知费用标成零。账本总量明确是已知部分；完整的前轮 output 仍保存在原始事件与审计记录中，未知调用按原预算机制保留额度。也没有为了处理协议异常多调用一次模型：4 个场景各只有 1 次假进程，真实进程与模型调用均为 0。

复核前后比较 V5 网关源码、V5 验证回执、`after_fixes_2` 顶层 JSON 证据及旧真实调用五份文件的 SHA256，均未变。V6 结果没有覆盖 `review_v5` 任何已有结果。首轮 V6 结果另保存在 `review_v6/result.json`；最终 `identity_verified` 批次增加加载源码身份稳定断言，作为当前引用证据。

## 两问自检

**这一步做得怎么样？** 对真实事故保存产物进行了无模型、无历史状态修改的完整网关复现，确认可恢复已付费回答，也实际找出了三个失败门/证据关联问题。三个问题均已修复并单独留存首轮与两次修后证据；最终用量缺失/冲突的预算错误也已用原聚合函数确认修复。V5 冻结后发现的意外多轮计费问题在隔离 V6 修复，四个新场景验证通过，版本边界与旧失败记录完整保留。

**下一步该做什么，如何改进？** 将已验证的 V6 网关与实验控制器衔接，继续记录失败与未知用量，并按 V6 最终源码独立验收。V5 主路径通过与残余限制分别保留。后续变更仍不跨越 schema、策略、预算与禁止自动重试的边界，旧冻结批次的失败与成本继续保留。
