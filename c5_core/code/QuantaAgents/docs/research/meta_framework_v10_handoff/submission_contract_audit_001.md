# v10 提交契约只读审查 001

结论：两题都存在“结构校验通过、运行时跨字段约束拒绝”的接口失败。严格验收结果应保留为 failed，不能把修订后的离线重检或字段修补算作原 live 成功；当前失败也不能单独作为研究诊断能力不足的证据。

审查对象是 `meta_diagnostic_calibrations/campaigns/diagnostic_20260906T172850309804Z`。先仅检查已终态的第一题，收到第二题已结束的明确授权后再检查第二题。未调用 Gateway、模型、查询接口或回测，未修改 campaign、答案、账本或冻结源码。此次为 Astra/xhigh 工程审查；工程模型用量与 campaign 分列，不称人工无模型审核。

| 保存终态 | 精确触发点（下标从 0 起） | 实际原因 |
| --- | --- | --- |
| calibration_01：7 个 completed call，6 查询，0 已接受 final | `findings[5].unit = "CNY"`；`claim_type="limitation"`、`value_decimal=""` | Workbench 要求所有非 numeric 条目的 unit 为 `none`。原错误 `non-numeric finding must not contain a number` 未指出单位，且该数值字段实际为空。 |
| calibration_02：6 个 completed call，5 查询，0 已接受 final | `findings[10].quantity_id = "retained_session_count"` | 名称不在 packet.quantity_dictionary；没有发生数量名重复。原错误合并了 unknown 与 duplicated 两种原因。 |

两份最终回答均通过冻结的 `validate_action` 和 `submission_schema`，保存的 output_schema 与当轮 action_schema 相等，response 与 completed receipt.response 相等。第一题使用 6 个返回页面 ID 及 packet hash，第二题使用 5 个返回页面 ID；未发现未返回或跨题引用。引用规则已明确写为“返回外层 evidence_id，或 public_package_hash”，所以这两次失败不是引用身份问题。

公开契约不足的具体位置：`diagnostic_calibration.py:65` 的 schema 仅分别枚举 claim_type、availability、unit，quantity_id 为自由字符串；`diagnostic_actions.py:66` 的模型说明只要求符合外层 JSON 和单次一个动作，没有公开数值名白名单/唯一性、按字典匹配单位、非数值条目的空数值及 `none` 单位规则。Workbench 在第 259–283 行另外执行这些规则。第一题字典还明确该数量单位为 CNY、零终仓时不适用；说明性条目保留 CNY 是可以理解的表示选择，虽然违反现有运行约束。第二题字典提供了可用名称，但没有明确它对 numeric 是封闭集合、其他可观察计数不能新增 numeric 名称。

最小修复应在下一隔离修订中公开同一份机器可读提交契约，并由它生成模型说明与精确错误：包含绑定身份、已返回证据集合、numeric 名称白名单及仅 numeric 内唯一、字典单位、未知值必须为空、其余 numeric 值的有限十进制语法及 count/sessions 整数限制、非 numeric 的空 value_decimal/none 单位。保留既有接受集合。明确未知量可以仍是 numeric（空值、字典单位），说明性限制可以是 limitation（空值、none）；不把 statement 中出现数字误称为非法。不要为本次回答增加 retained_session_count 或转换单位，更不能覆盖旧报告。

离线要求：所有既有约束有公开规则与匹配字段错误；区分 unknown 与 duplicate、非数值非空值与非 none 单位；字典之外的数值说明仍可放在 interpretation/limitation 的 statement；原两份字节不变的回答仍拒绝，并分别给出上述精确字段。动态契约不得含 oracle、评分、参考答案或新证据。

账本核对为 13/13 completed provider call，输入与输出合计 238,797 token、缺失 I/O 用量 0。两次 action 均 rejected，pre/post Workbench state hash 相同，没有接受任何 final。请求冻结为 Astra/xhigh，但所有保存回执的 `model_verified=false`，因此只报告请求身份和本地保存链，不冒充供应商模型确认。本文不进行数值准确性、下一实验质量或架构胜负评分。

冻结证据 SHA256：

| 文件 | calibration_01_round_6-1 | calibration_02_round_5-1 |
| --- | --- | --- |
| response.json | `35a081b0ca4c6b95201514c81159b5bcec4811ce6cf8494b25c71da1c2dcb82e` | `cf052f729711c1d6052c4fc76b472e1be366f3f69422141c4a80773d4196a364` |
| receipt.json | `43b91a834fd0771dc54ee66204e4594be689372f5b766e6bfdc99c81b24f65a1` | `af9da04f8708652f2418e883ad3f9b6d13a5bcfced4516484c7b442142c0b78c` |
| prompt.txt | `363a94863d70cba329dd3c3a7959a8dee9c6d87efcafb69842ecc146432cad56` | `f83ad2c44439177385437fd9a33f287b45bfa25dff636ea5e01d5c5fae582076` |
| output_schema.json | `854855d0e3d09f109564cfe8b1f16b8efc35f139e8826d795967ee096bb9bb66` | `83e922997b4d5bda6dbbd91da014cd26b79b7454e0ded227b0ac7d7b0c9aef3e` |
