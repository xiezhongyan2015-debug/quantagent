# v15 新研究登记册监控：独立复核 001

2026-09-07，Astra/xhigh。**最终冻结版本在本次只读监控范围内有界通过，未发现未关闭的实质阻断。** 作者新 16 项加旧后端 13 项共 29 项通过；root 对最终提取脚本执行原 7 项异步身份反例，7/7 通过。审查者核对了源码、差异、测试、JUnit、原日志、回执和输入/输出哈希，没有重跑这些组或原研究控制器 15+6 项。

这是新 casebank 登记册的观察入口复核，不是实际四题计划、预算或付费派发授权。未运行实际 prepare/run、Gateway、真实市场读取、原四题候选发现或回测；未启动/替换持久监控服务。工程评审模型用量未在本文独立计量，不能记为零。测试使用临时合成 SQLite；作者旧后端测试包含临时本机 HTTP 服务，不应把它写成所有测试都未启动任何服务。

## 路由、身份与预算显示

监控仅扫描项目内两个固定根：`meta_diagnostic_calibrations/campaigns` 与 `meta_casebank_references/campaigns`。HTTP 输入只接受公开 identifier，不接受调用者文件路径。命名空间为 diagnostic/casebank；新 casebank 始终明确标记，两个登记册同 run_id 时分别显示 `diagnostic:<id>` 与 `casebank:<id>`，裸同名请求拒绝歧义，不静默选中一个。

固定登记册、campaign、ledger 以及新 campaign.json 的解析路径必须仍与原路径一致；重定向、符号链接/连接点造成的越界在打开数据库前拒绝。SQLite 以 `mode=ro` 打开，读取现有 run/calls；没有构造 Gateway、Campaign、Workbench 或 mutable Store，也没有调用 execute、恢复、暂停、重试或创建阶段。HTTP 仍为原 GET 展示入口。

新登记册额外校验 kind 为 casebank_research，manifest 的 run_id 与目录/ledger run 身份一致；`wrapper.plan_hash == canonical(plan) == run.plan_hash`。任务列表唯一且与 ledger tasks 集合一致，每个 call 的 run_id、plan_hash 和完整 task_identity 均与保存计划匹配。审查者在未冻结草稿中指出了计划/hash 和外来 call 身份核对的缺口，作者在本次稳定组前收口并加入六项扰动测试；没有把这次预读描述成实际研究已串账或一个已运行的修前失败。

casebank 按 task_run_id 统计，case_id/arm 取自已绑定计划及 call identity，不误用旧 cases 字段。未开始的任务也显示；查询、候选、final、已知用量和调用状态保留。旧诊断与新合成参考研究分类型展示，用量可合计但类型不混合，两者均不贡献正式策略成功计数。阶段总预留采用 ledger 保存的 reserve 与既有 `call_budget_usage`，没有因为接入监控而重置、释放或重新派发调用。

显示依据仍是本侧已保存记录，不是实时供应商账单或后台 worker 活性认证。请求的模型/effort 与 provider_model_verified 分开展示；供应商实际模型身份仍未独立确认。原失败、弃权、未知和预算停止身份不能通过监控显示改为研究成功。

## 公开回答与分页

这里的公开完成回答指模型完成事件所保存的 **action JSON**，包括 inspect_inputs、diagnose_horizons、condition_events 与 submit_research_report，不只研究最终报告。

casebank 答案仅从 completed call 的 receipt.response 取出，要求顶层恰为 action/queries/candidates/reports，action 是字符串，其余三个字段是列表。该结构检查允许展示之后被工作台拒绝的原公开动作，不替代动作合法性或研究内容评分；它不把“可查看”当作“结论正确”。未完成记录、额外私有顶层字段和非公开形状不导出。旧诊断回答保持原三键协议。

调用列表只投影已列出的状态、身份、用量与应用错误字段；回答接口只序列化这个 response，不返回整个 receipt、prompt、raw events、计划、控制器 key、隐藏推理或私有 evaluator。合成 canary 测试验证私有字段未出现在结果中，且 key/private 文件没有被读取。模型自己公开返回的文字不是后台隐藏推理的导出。

调用列表每页至多 20 条；回答 offset 非负、limit 为 1–65,536 个字符，页面使用 4,096 字符并保留 campaign/call/offset 身份。所有模型文字仍经 textContent 展示。该分页是输出边界；底层会读取当前登记册的 run/calls 和必要计划，并序列化整个公开 action，**不能宣称是对任意损坏或无限大本地 ledger 的内存硬帽**。

**完整工具 observations 尚未单独接入 GUI。** 当前可查看模型提出的查询/候选/诊断动作及最终报告，不能据此宣称已经能在页面展开全部返回工具页，亦不能宣布 P3 全部完成。

## 实际验证与页面差异

作者定向组为 **29 passed in 4.76s**，其中新 16 项、继承后端 13 项。审查者解析 JUnit 得到 29 tests、0 failures、0 errors，并逐项核验当前源码/测试与回执输入，以及 JUnit/日志 artifact hash。

新组覆盖同名 namespace 拒绝与类型/任务统计、读前读后数据库字节不变、公开 query 和长报告逐页重建、key/private canary、未完成/多私有字段拒绝、四个非法路径、两个路径重定向，以及六个计划/调用身份扰动。身份不一致时 API 拒绝且 snapshot 显示 unavailable，没有用损坏材料生成正常任务显示。

PAGE 的状态文字、研究类型和 arm/task 标签确实改变了 JavaScript 字节，不能声称旧脚本逐字未变。审查者阅读最终 page.diff，异步身份守卫保持：请求代际、campaign/call/offset 核对、切换时作废旧答案，以及旧成功/旧错误不能覆盖新状态。

root 对最终脚本单独执行原七个确定性乱序反例，检查跨调用答案/错误、跨 campaign 列表/错误、切换作废 pending answer、旧页不能覆盖新页、旧 status 错误不能覆盖新成功，全部通过。审查者核对 result/output/script 原字节 hash，并从当前冻结模块导出 PAGE/JS，确认脚本与 root 实际测试文件逐字相同。没有再次执行该检查器。

该七项结果是离线乱序模拟，回执明确 `actual_browser_checked=false`；本文不把它当作本轮真实浏览器已检查。持久服务切换和当前实际页面核验由 root 后续执行。

## 冻结证据

路径相对 `experiment_traces/meta_ashare_revision15/`。

| 文件或内容 | SHA256 |
| --- | --- |
| src/quanta_agents/meta/diagnostic_monitor.py | `e9b7784c68411c8b67d9a9c884129d6052e9b4b2e816fb7f5b85673ade4e4a4e` |
| tests/test_meta_casebank_monitor_v15.py | `85f1b05f7ca86e3a9a373ceef10ed913351d94ff0150f063e0e1a6145ac4025f` |
| casebank_engineering_attempts/monitor_author_20260906T205027203267Z/receipt.json | `2c03d1697b83fc8092c9e39af74f2b94aaa8c3d9e2a698fd3b9dbf35ddf94c0e` |
| 同目录 junit.xml | `457518a4748a384ecb5b5206a197e8707319cba0c7a6827a9a9f0d322f1c85b9` |
| validation_artifacts/monitor_js_001/receipt.json | `7f6d4d054e5cdedd988c3a5a2e0285e1e471348d908ebeeb24397ffbaa03e2f3` |
| validation_artifacts/monitor_js_001/monitor.js | `2d8ba6c7d2712abe40a34eb5bb31000adcf0430abb96960cf393075eb6ffbb15` |
| validation_artifacts/monitor_js_001/result.json | `0b5c866618d32a320816c6775349156c14f02363d068355d23593c0ace2c9bcc` |
| 当前模块 PAGE 以 UTF-8 编码的内容 | `48648be7a90de45e7714bbff0ed55f1873e6b3dad9d1dbea935010ca13f10b86` |

作者导出的 final_page.html/final_page.js 使用 Windows CRLF，原文件字节 hash 分别为 `512fc16bf0a7782d6f41a290871bcde123176abb370d868a874361e0ca8ac65c` 与 `d29a4e41bd30bbe7ffe0cdbbb9e4a4bd6548afcf29d82cd69d2eec18c6402918`。实际核对 CRLF→LF 后，它们分别等于上述 PAGE 和 root 测试 JS；不能把两种原字节 hash 直接称相同，原件未改。

## 两问自检

**这一步做得怎么样？** 新登记册、普通任务身份和全部公开完成动作接入了已有只读观察路径；同名、损坏身份、路径重定向与私有字段边界都有具体合成检查。29 后端与 7 乱序检查的最终来源和证据匹配，没有通过扩大复测数量掩盖范围差异。

**下一步该做什么，如何改进？** root 完成组合验收和唯一只读服务的实际页面核验，另一独立评审再检查具体四题计划与预算。完整工具观测界面作为明确未完成项保留。监控显示不得自行授权调用、改失败分母、替代派发门或证明模型能力/架构/收益。
