# v11 独立审查 001

2026-09-07，Asia/Hong_Kong；Astra / xhigh。只读 v10→v11 提交契约、精确报错和监控分页差异，核对已保存的 209 项定向验证与真实失败记录。新增文档及独立离线 JavaScript 反例产物，不改 v11 源码、脚本、测试或原 campaign。

**结论分开登记：提交契约修复通过；监控后端只读边界及顺序分页通过；GUI 异步切换存在已复现的证据身份显示问题，待隔离版本修复。v11 不能笼统标为界面全部通过。** 本结论不授权新的付费调用，两题原失败和 238797 token 保持不变。

## 提交接受集及公开规则

逐段对照 v10 `submit_diagnostic` 与 v11 `validate_submission_contract`，原判断条件保持一致：同一结构、任务/包绑定、本题已返回 evidence、numeric 标识集合与唯一性、字典单位、未知值空串、有限 decimal 格式、整数计数/会话、非 numeric 空串和 `none` 单位。只有错误定位和说明拆开。生命周期的相同最终报告幂等、不同最终报告拒绝及提交哈希没有改变；没有归一化、替换、不计费重答或扩展旧量字典。

v11 的 `submission_contract` 从公开 packet 的量字典和固定规则目录构造，明确“结构 schema 且全部适用契约规则”，给出 numeric 闭集、唯一性、单位、空值和说明项写法；额外事实可写在 non-numeric statement。相同 `SUBMISSION_RULES` 提供错误文字，解决原本模型只看到宽结构、提交时才遇到额外约束的问题。不存在 oracle 值、参考报告或评分回灌。

原两份回答仍然拒绝：第一题精确指向 `findings[5].unit [non_numeric_unit]`，第二题指向 `findings[10].quantity_id [numeric_registered]`。这符合接受集不变的要求，没有因实际金额正确就追认原非法表示。PROTOCOL 改为 v2，完整公开上下文 hash 随之变化，旧 v10 计划不得用新协议恢复派发。

既有 parity 测试逐个比较 v10/v11 的 26 种正常及拒绝路径，包括未知空值、非 numeric 任意标签/重复、同标识 numeric+non-numeric、重复 numeric、错误单位、整数/小数/科学计数、非法 decimal、foreign task/packet/evidence 及空 findings；另有真实两份原回答固定哈希负例和错误字段检查。本次结合条件级 diff 复读，没有发现扩大或缩小接受集的实质变化。有限测试不是对所有 Python 对象的形式证明；结论针对公开 JSON 协议域。

## 监控后端及顺序分页

接口只提供 GET 状态、调用摘要和已保存的公开 action。数据库以 URI `mode=ro` 打开，不实例化可写 Store、worker 或 gateway，没有 run/resume/retry/pause/submit endpoint。只绑定 192.0.2.10；此为本机读取工具，并非账户权限或 OS 隔离认证。

campaign 参数限定简单标识，解析 ledger 的绝对路径并检查 registry 范围与 run ID；call 仅在所选账本中精确匹配。摘要字段白名单只给状态、用量、请求型号、是否有回答和原拒绝原因。答案输出只取保存 receipt.response 的 `action/queries/submissions`，不返回 prompt、完整 receipt、原始事件、私有 evaluator 或隐藏推理流。供应商身份未认证在界面明确区分，不将请求型号冒充供应商证明。

调用页 limit≤20；答案字符页 1≤limit≤65536，非负整数 offset；HTTP 要求准确参数集合，拒绝重复/多余字段。返回 `next_offset`，不因翻页创建查询或计数。页面用 textContent/DOM 节点显示回答，未把模型字符串当 HTML 执行。现有 23 条调用分页、超长回答拼接、路径拒绝、错误不泄漏和 POST 禁止测试均在验证范围。

主控记录真实浏览器对实际 8253 字符回答的 1–4096 / 4097–8192 / 8193–8253 顺序翻页、返回与刷新检查，调用/费用保持 13 / 238797；本独立代理没有再次启动或操作服务。本次审查不将该顺序检查扩写成所有并发交互都正确。

## 待修：旧异步结果覆盖新选择

`loadAnswer` 请求发生时使用当时选择的 call，返回后却用全局 `selectedCall` 标注正文；`loadCalls`、翻页和错误分支也没有代际与身份检查。无网络可稳定复现：先选 A、再选 B，让 B 先返回、A 后返回，最终界面显示 `call_B` 标签配 `ANSWER_A` 正文。数据没改，来源显示却错；这会误导对两份研究回答的评审，属于需要修复的 P2 GUI 证据身份问题。

独立检查器执行从冻结 PAGE 提取的原始 JS，使用小型 DOM 和手动延迟 fetch 响应；未使用真实网络、浏览器、账本或模型。7 个保护预期在 v11 全部失败：

1. 旧 A 回答覆盖较新的 B 回答，并标成 B。
2. 旧回答错误覆盖新回答成功标签。
3. 换 campaign 后旧调用列表覆盖新列表。
4. 旧调用列表错误覆盖新运行的成功状态。
5. 换 campaign 后旧 pending 回答仍显示。
6. 较早发出的下一页晚到，覆盖更晚请求的上一页。
7. 旧 status 请求错误覆盖较新的刷新成功状态。

这 7 个反例没有证明账本身份验证失败、私有数据泄漏或重复付费；它们证明前端必须区分当前请求与过期请求。建议在隔离版本为 status/calls/answer 保存请求代际与 campaign/call/offset，只有仍属于当前选择的响应可更新；旧成功与旧错误同样忽略，campaign 切换作废 pending 并清空相关答案。原 v11 及其 209 项回执保持冻结。

## 验证与版本

本次只读核对 `validation_attempts/20260906T175127021941Z/receipt.json` 与 JUnit：209 tests、0 failures/errors/skipped，JS 语法通过；当前 source/scripts/所选 tests 字节仍逐项匹配该回执和 plan。回执 SHA256 `55bd6f97c0316d81774070209ac4ac35c67d1fa35a63a31861f6530b7a137471`，source hash `68496ad3e6d1917257ff614c0a81d64352ecf16048220a37389957a806bb93fb`。已保存回执记录旧源码和付费 artifacts 保留；新增 GUI 竞态反例发生在该次验证之后，不修改或隐藏原通过结果。

| 文件 | SHA256 |
|---|---|
| diagnostic_calibration.py | 99338343f6baf89d852709fa61586ba8fe70c810b7dad404b6b7802da0b52a71 |
| diagnostic_actions.py | 32d647e5428ba54b390ef93a749b8d609d3f975c628ac66e78427b40d35a166f |
| diagnostic_monitor.py | 61f0d543bbfb19be5d9b6db16ce82154038528a3fcd1c36ae6176a03878dd7e7 |
| 原 PAGE 提取的 frozen_monitor.js | 4278d6439cb5231d22ac2102fb6f1fba1b9800547fb7b42f8ebad24b1da1a8f4 |
| 独立 check_monitor_async_identity.js | c450f4336fc8985c9c52cbc4ca21ad8dc932c078ce0520178d9332a89c01f636 |
| 独立 async_identity_result_v11.json | 282efdc2436e1b2b1c81270a30c1ac6a559fef9ede456f4c667463dc6bc3e623 |

反例位于 [review_artifacts_001](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v11_handoff/review_artifacts_001/async_identity_result_v11.json)。同目录 `extract_monitor_js.py` 只执行 PAGE 的字符串赋值，不导入产品包或启动服务；`check_monitor_async_identity.js <提取JS> <新的JSON结果>` 可原样用于隔离修复，输出使用排他创建，不能覆盖旧结果。第一次常规导入因本机 Python 缺 langgraph 而失败，未改变源码；随后采用上述 AST 文字提取完成实际反例。

两问自检：**是否把通过数或顺序演示当成全场景身份正确？** 没有；提交接受集、只读后端、顺序 GUI 与异步失败分别登记。**是否以修复名义改变原研究答案或重付费？** 没有；v11 只读反例无真实网关/市场/回测，后续只能在隔离版本验证 UI 修复。工程评审自身模型用量另列，不计成零。
