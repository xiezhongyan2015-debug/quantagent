# v16 同账本传输更正监控 001

本增量仅修改隔离 v16 的 `src/quanta_agents/meta/diagnostic_monitor.py`、新增 `tests/test_meta_transport_monitor_v16.py`，并保存离线凭证。监控在同一只读 SQLite 连接上合并原 calls 与 `transport_correction.read_correction_projection(db, run=stored_run, plan=plan)`。缺更正表仍返回原记录；不构造 Store/TransportCorrectionController，不恢复、派发或应用任何回答。传入投影器的是添加 `_monitor_*` 展示字段之前的原 run 副本，防止展示字段污染冻结原 run 哈希。

## 记账与展示

更正记录按同域 call 汇总 reported/reserved/exposure/count，保留原未知调用的 80,000 名义预留。投影器负责原 run/plan/call/task、permit/intent、原 prompt、wire/原 schema/新 source 的身份检查，以及 completed 必须有完整已提交回执、I/O 与回执相等。监控再校验 plan/task/run 和 call ID 不冲突；不以更正成功覆盖原 calls。

`public_calls` 新增或保留 `kind=transport_correction`、`transport_ordinal=2`、`round_index=semantic_round_index=0`、corrects_call_id、original_schema_status、workbench_validation、public_action_applied、usage_availability。原 kind、arm、task_run_id、两登记册命名空间、分页参数保持兼容。task 行新增 transport_calls_reserved 与 semantic_rounds_reserved，任务状态、queries/finals 始终取原任务状态，不由传输完成推导。

计费完成与返回格式分轴：原 JSON schema 校验通过时可查看已保存公开四键 action；它仍是 `workbench_validation=pending_not_performed`、`public_action_applied=false`。工作台的跨字段、引用、机会及实际工具应用都没有运行。原 schema 拒绝时传输仍可 completed、费用完整，但不显示公开回答按钮，也不显示已完成研究报告。页面文字明确“原返回格式通过；工作台待校验，动作未应用”或“原返回格式校验拒绝；公共动作未应用”。

用量完整度单列 `not_started/not_reported/partial/reported`，按实际 I/O 字段是否为非负整数判断；已知小计可为 0，但无回执时显示“未报告（已知小计 0）”，部分已知显示“部分报告”，真实已报告零与尚未调用不同。名义预留是否结清仍由原 call_budget_usage 的状态和完整用量门决定。

投影只导出白名单元数据与已保存公开 response。没有读取 keys/private 文件或暴露 prompt/raw events/permit/admission/整个 receipt。它校验数据库中已提交的身份和记录哈希，不重新重放 Gateway 文件证据，不能据此认证供应商账单、模型真实性或进程活着。

## 保存的反例与回归

凭证根为 `experiment_traces/meta_ashare_revision16/transport_monitor_attempts`：

- `before_001`：旧监控未知用量缺 usage_availability 的真实 **1 failed in 3.97s**，保留原源码/测试副本、stdout/stderr/JUnit；receipt SHA `b3af91b21036c576f731a0c9ca7d0825b2fcfece40cd0684fc295813f57ad754`。
- `after_001`：首次 **11 passed in 2.97s**，receipt SHA `6d6b30c9e4c815ec869c90a93558e7aefb3cf3cc165625a8b0a46deeb99d5db7`。其适用投影为旧 408d94… 版本，不能当后续加固投影的验证。原回执未改，另用排他写入保存该版专组快照和最终 PAGE/JS UTF-8 字节。
- `after_002`：投影增加回执/用量及完整请求策略身份门后，仅给手工 SQLite fixture 补齐实际控制器已有字段，监控源码未改；对当时 0e2100… 投影 **11 passed in 3.53s**，输入源码/测试前后未变。receipt SHA `9dd8d64d76be7a926290a866540143dbd772274a091184398407da0c311e4ac9`。

后续读侧审计又确认：完整 controller 已校验 budget，但独立 projection 还应自己核 `permit.budget == plan.budget == run.budget`、`base_created_epoch == run.created_epoch`，否则不能借原控制器的验证声明当前显示的预算身份一致。投影作者在最终 5fa687… 版本补此门，另明确 running 不代表 controller_stopped、worker_liveness 未核验。监控源码/PAGE不变，只给专组夹具补相同完整预算/时钟字段；after_002 的原测试已排他复制保存。按根任务指令不第三次单跑 11，最新 5fa687… + a2cb6f… 专组待根任务与 correction/独立/CLI 统一受影响回归；本文不把 0e2100… 的旧 11 回执当新增门的最终通过。

11 项覆盖：原未知与无更正表；合成旧 28 已知令牌加新 13 为 41、原 80,000 仍保留；新传输失败且部分 I/O 时两份预留共 160,000；同一语义 round 的第二传输；格式拒绝；格式通过但 reports 为空的 final 仍未应用、任务 final 仍 0；四类非计划身份扰动；SQL authorizer 限定 SELECT/READ/FUNCTION；构造 mutable Store/controller 陷阱、keys/private 文件读取陷阱、私有 canary 不导出；原异步身份检查保留。这里所有金额/令牌/状态均来自合成测试，不是读取实际研究账本得到的新观察。手工 projection fixture 不等于完整真实 admission 测试。

根任务在相同最终 PAGE 上另执行原 7 项异步身份检查与新增 3 项真实 JS 渲染检查，10 项通过；`transport_monitor_js/001_root/receipt.json` SHA `6409c20e345e5622506705786b83d9c23854f03cbe427fee810cf569be215505`。作者未重复原 29 个后端测试或根任务 JS。actual browser/8776 部署由根任务单独执行，本作者没有启动或部署服务。

## 冻结指纹

| 文件/产物 | SHA256 |
| --- | --- |
| diagnostic_monitor.py | 1fb2d796a13305a7fb8ef86de9fc30f6aad41015ef5cbad065176af8eed4ea51 |
| test_meta_transport_monitor_v16.py（新增预算/时钟 fixture，待联合） | a2cb6f8d19d4ba224f9292405b63a286a005d7d9ec1d0164f1ce97facd13a797 |
| after_002 验证所用 transport_correction.py | 0e21009f00f98e81d0105dd4a98583253dccf1f63115e469b855be1ad53bb657 |
| 最新 transport_correction.py（新增预算/时钟门，待联合） | 5fa687acd947870d4b4fd2896a1a693e6f1e3de9c8a28614336e82ffbff0d3f1 |
| 最终 PAGE UTF-8 | 94927d770dd1ae2e23b6fb4c5254a02e50347fc6e9165409944d9353f1a4f857 |
| 最终提取 JS UTF-8 | a5ad933e9483789ae8b0f7ec57729e5dfb4959d04fd682d6e81226c561ed23fd |

本子任务新增项目 CodexGateway 调用 0、真实市场/实际 campaign 数据库读取 0、新回测 0；工程与评审模型用量非零另列。未改 v15/main、WB/helper、Gateway、campaign/live、原题包或实际准入。若后续投影源码变化，上述适用哈希应重新比较，不能把源已变的旧 11 项当自动通过。

## 两问与停止条件

**是否修正了真正会误导的显示？** 是。未知用量不再伪装为已报告 0；更正传输与原请求同域计费但分行保留，原预留不被新完成抹去；返回格式通过不等于工作台动作或 final 已完成。证据是失败记录和上述直接合成反例。

**哪些事情仍未完成？** 本文不证明实际供应商接受 schema、不认证真实账单/实际 worker，不证明更正回答是合法工作台动作或正确研究结论。完整工具 observation 浏览与工作台后续校验/应用仍不在本增量中。若计费与语义状态混淆、原未知预留被扣除、串运行数据被计入、SQL/文件写入或私有数据泄漏、乱序页面覆盖出现，停止监控版本准入并保留反例；不能用改标签或手改实际账本掩盖。
