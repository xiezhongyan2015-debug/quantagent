# v15 首次参考调用 schema 拒绝：独立复核 001

2026-09-07，Astra/xhigh。**已证事实是当前请求链拒绝了整份生成 schema；尚不能把 array-valued enum 认定为已孤立确认的唯一根因。** 原 schema 能被本机标准 JSON Schema 验证器处理，本地 SDK 也原样接受它。此前 fake Gateway 测试没有经过供应商 schema 编译，这项兼容性没有被那些通过结果覆盖。

本轮保留暂停与原未结义务，不修改 v15 源码、脚本、测试、schema、计划或调用记录，不重发、不调用 API 探测、不释放预留。仅查官方文档、本机帮助/版本、保存证据和有界合成值。新研究 Gateway 调用 0、供应商 schema 探测 0；工程评审模型用量未独立计量，不能记成零。

## 保存证据证明了什么

原目录为 `experiment_traces/meta_casebank_references/campaigns/reference_v15_original4_001/calls/reference_v15_original4_001_case_01_reference_round_0-1/`。

- `codex_events.jsonl` 保存 thread.started、一个 code_mode_host disabled 错误项、turn.started、HTTP 400 错误及 turn.failed。最终 error 的 type 为 invalid_request_error，code 为 invalid_json_schema，param 为 text.format.schema；消息只说整份 codex_output_schema 不合法，没有具体 JSON Pointer、字段或关键字原因。
- 没有 agent_message、turn.completed 或 response.json；因此没有可评分研究回答，也不能把这个请求当作模型完成了一次四题研究。process_exit.json 记录启动后 exit code 1、duration_seconds=5.218；外围约十秒观察时长与该子进程时长不是同一计量。
- 保存 schema 原字节 SHA 与 codex_request.json 的 schema_sha256 匹配，内容与 intent.schema 一致。问题并非本侧误指向另一份 schema 文件。原文件绑定不证明 CLI 的完整 HTTP body、strict 标记或中间层改写已经被独立捕获；这些 wire 细节未保存在此目录。
- 只读 SQLite 核对：campaign paused 且 pause_requested=true；case_01 一次调用，另三题零调用。原 call.status 为 failed，但 input/output/cached/reasoning usage 全为 null，预算意义上仍是未结/未知调用，reservation_tokens=80,000。HTTP 400 不等于这里已经取得零账单回执。

原 code-mode 警告不能单独解释这次终止。已保存的 v12 成功首调用出现过完全相同的警告，随后有 turn.completed。本轮不为消除该警告而启用工具、放宽隔离或重发请求。

## 已执行的无付费检查

独立目录：`experiment_traces/meta_casebank_schema_reviews/reference_v15_failure_001/`。目录中的 wire_hypothesis_not_approved.json 仅为合成检验假设；它不是 v15 的修改或新运行批准。

| 检查 | 实际结果与边界 |
| --- | --- |
| 原 schema 结构盘点 | 根为 object；6 个 object 全部字段 required，全部 additionalProperties=false；22 个对象属性、33 个 enum 值。包含 7 处 maxLength、7 对数组长度约束及一对数值上下界。未见这些通用结构/规模条件的明显违例。 |
| 标准 schema/实例校验 | 本机 PowerShell 7.6.5 的 Test-Json / JsonSchema.Net 7.0.0 处理原 schema 和假设 schema，10 个合成正负实例符合预期。普通 inspect 和固定期限 candidate 有效，原 schema 拒绝错序、重复及错误长度。此结果是该本机标准验证器的接受证据，不是供应商兼容认证，也不是声称运行了另一未安装的 Draft 元 schema 工具。 |
| 本地 Gateway 值校验 | `_validate_output` 对相同 10 个实例符合预期；它检查回答值，不是发送前的供应商 schema 编译器。 |
| SDK strict 归一 | 已安装 openai 2.26.0 的 `_ensure_strict_json_schema` 原样接受保存 schema。源码主要递归处理 required/additionalProperties、引用等，没有为这份 array enum 提供供应商能力证明。 |
| 期限语义反例 | 枚举 `[1,5,10]^3` 的 27 种长度三组合，原精确 array enum 仅接受 `[1,5,10]`；移除 array enum、改为 integer-item enum 加 minItems=maxItems=3 后接受全部 27 种。本地精确语义检查不能随传输约束一并删除。 |
| 本机 CLI | 原请求使用的同一路径当前为 codex-cli 0.153.4。实际读取 top/exec/debug 帮助及版本；已公开帮助中没有 provider-schema validate/dry-run 命令。`exec --output-schema` 是实际生成参数，不能拿它当零调用验证器。未执行 doctor、prompt、resume 或任何代理请求。 |

审查前后原调用目录七个文件的 hash 一致。只读读取原 ledger，没有保存、恢复或修改它。合成实例不是模型输出，没有补造 provider 事件或 usage。

还比对了已有成功的 `diagnostic_20260906T183231732416Z/calls/calibration_01_round_0-1/`：同一 CLI 路径、请求 Astra/xhigh，有保存的 turn.completed；其 schema 已含 maxLength/minLength、minItems/maxItems、minimum/maximum。该历史正例反对“这些限制一律不能用”的简单归因。旧例十处 enum 全为字符串；新例还引入整数 enum 和数组值 enum，因此历史对照也没有把所有差异隔离为一个关键字。相同本机路径和请求模型名不证明两个时点实际后端相同。

## 官方资料与归因等级

按 OpenAI Docs 技能，于 2026-09-07 搜索并实际打开官方页面：[Structured model outputs](https://developers.openai.com/api/docs/guides/structured-outputs#supported-schemas)、[Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode#create-structured-outputs-with-a-schema)。

官方 Structured Outputs 只承诺 JSON Schema 子集，列出 array、enum、数组长度和数值范围支持；要求根 object、全部字段 required、对象关闭额外属性，并列出某些组合关键字及 fine-tuned 模型额外限制。文档没有在所查段落明确承诺数组值 enum；其一般字符串支持表也不是对本次后端每个长度关键字的逐项认证。不能把文档没有明确说明解释成已经证明拒绝，更不能把 fine-tuned 的限制无条件套到请求的 Astra。官方 Codex 文档把 output-schema 描述为生成最终结构化回答的入口，没有给出离线供应商接受集验证命令。

| 等级 | 结论 |
| --- | --- |
| 已证 | 当前调用返回远端形状的 invalid_json_schema / text.format.schema；本侧保存文件与意图绑定一致；原 schema 在本机标准验证器有效；现有离线通过链没有供应商 schema 编译步骤。 |
| 优先假设 | `properties.candidates.items.properties.horizons.enum = [[1,5,10]]` 是值得优先消除的兼容风险，因它是复合值 enum，且未在所查官方支持细则中获得明确保证。 |
| 尚未孤立 | array enum 是否唯一或实际触发点；新整数 enum、其他关键字组合、当前 CLI/wire 转换、服务路由或后端特定限制是否参与。原错误没有提供足够定位信息，本轮不以试收费请求补证。 |
| 不能推定 | 400 的真实 token/费用为零；实际供应商模型身份已验证；只改一处便已证明远端接受；原四题已获得合法回答或架构能力证据。 |

最直接的工程漏项是**把本地值校验、SDK 归一和 fake 运输通过，当成了足以进入真实 Structured Outputs 请求的兼容性证据**。原工程测试仍能证明它们实际检查的预算、身份和恢复性质；这次真实失败明确暴露其未覆盖的供应商接受集边界。不能事后改旧验收记录或称原 schema 已经通过了这一项。

## 最小下一版方案及无付费验收

建议下一隔离版本明确分开“供应商生成结构”与“公开研究语义合同”，两者分别 hash 绑定。本地完整语义和机会账务保持严格；生成 schema 采用事前规定的保守子集，发送前递归检查不允许把数组/对象值 enum 混入。这个本地检查器应诚实命名为兼容风险 lint，不能声称是未经证实的供应商验证器。

对 horizons 的最小候选是传输层使用普通整数 items.enum=[1,5,10] 与长度三，继续由公开、本地精确规则只接受 `[1,5,10]`。必须在任何 kernel 前拒绝另外 26 种长度三组合，并保留原失败机会/子尝试计量；不能排序、去重、填补或静默改写为正确期限。若另选删除冗余 horizons 字段并由固定协议提供期限，那是显式的新公开协议变更，不能在旧 run 上偷偷变换。

无付费验收可限于：递归检查最终生成 schema 的保守关键字/enum 类型、核对根/required/对象封闭及界限；证明四种动作有合法合成输出；测试期限、条件数、分页和总字节限制仍在本地精确执行；用现有 fake Gateway 验证新 wire/schema hash 与完整本地语义 hash 的意图/回执/工作台绑定，以及拒绝发生在 kernel 前。数组值 enum 的旧 schema 应成为发送前风险拒绝反例。没有必要通过扩大无关回归数量来替代这项缺口。

**这些检查通过仍只说明本地实现和选定兼容策略自洽，不能证明实际远端已经接受。** 在没有可核验供应商离线编译器、也不获准新请求的情况下，远端接受应保留为待验证。本报告不请求或授权一次探测，也不允许重派原 round。旧 80,000 未结预留与失败分母保持；不得通过修改旧 schema、换 key 或另建 registry 把它假装清零。

## 冻结证据与两问自检

| 证据 | SHA256 |
| --- | --- |
| 原 output_schema.json | `84b3180cd9c56854e18912279896d3dade48cd29b6020a614015b6bda495cb9d` |
| 原 codex_events.jsonl | `ee3b52d18bbe938741fe687925986cc7d2462859d0c8fff2658421dfda5d49a8` |
| 原 codex_request.json | `c420f45062b2fd3210740a674b8bd5206073db303f7748f260e333e4d9732534` |
| 原 process_exit.json | `a3078edb3cf6d24f2ca8ce07c9313a946e153476c0f39aa1ae398641173cb854` |
| 原 intent.json | `d7f4b01895e73f2c8ca009e2727822739faebe5a7413bb3791b38e687c4ca825` |
| 已有成功 schema | `854855d0e3d09f109564cfe8b1f16b8efc35f139e8826d795967ee096bb9bb66` |
| 已有成功 events | `7118f523c38e680b03300996c7d50f2305bbb5b9b88154e3f64a81e45283b7b7` |
| 本机原请求路径 codex.exe | `a1cf6360ca71918d5466bc3a32d9f18b7044c9128756d1949e715d277b88c9b6` |
| 独立目录 receipt.json | `079790cb4231651175dca4a305f79a553194e1f7ccf66db532a9b5849ff5c00d` |
| 独立目录 offline_result.json | `6ac7ce92f4f3a7278296a600a78279376aa480121ab9c1dddf0228d710945067` |
| 独立目录 standard_validator_result.json | `3c312c70ec529c5d73a04e97a59921adc5d1af59490be3658f14afbc30b048ce` |
| 独立目录 historical_schema_comparison.json | `e07756cdabbb21973ba86ac9d857d9e6b0a062247e354fb70fc273432ca0ce26` |

**这一步做得怎么样？** 定位到实际拒绝证据，验证原 schema 的本机标准有效性，复核先前成功约束和警告，证明了最小传输放宽的 27→1 本地语义边界；同时没有把优先怀疑包装为唯一原因。原失败、未结 usage 和预留全部保留。

**下一步该做什么，如何改进？** 在下一隔离版本补保守 wire-schema 风险检查与独立语义绑定，用上述少量反例做无付费验收；继续明确远端接受尚未验证。任何之后的真实阶段都须重新冻结具体准入、核对旧未结义务，不能由这份离线报告自动触发。
