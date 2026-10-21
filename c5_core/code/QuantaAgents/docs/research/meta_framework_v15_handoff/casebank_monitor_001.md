# v15 两类登记册的只读监控接线 001

本增量让已保存的诊断校准与合成参考研究在同一监控接口和页面中可查。没有启动监控常驻服务，没有创建真实参考阶段，也没有调用 Gateway。本取证/监控子任务新增项目 CodexGateway 研究调用 0、真实市场读取 0、新回测 0；工程和评审模型用量非零，另列，不可推广为整个项目累计调用为零。

## 公共接口与身份

原 `snapshot(project)`、`public_calls(project, campaign_id, offset=0, limit=20)`、`public_answer(project, campaign_id, call_id, offset=0, limit=16384)` 及 HTTP 参数保持兼容。新增固定目录 `experiment_traces/meta_casebank_references/campaigns`，原目录仍为 `experiment_traces/meta_diagnostic_calibrations/campaigns`。调用者不能传文件路径。注册目录、具体 ledger 和 campaign.json 的解析路径必须等于固定预期路径，拒绝符号链接与解析重定向。

新研究的公开运行标识为 `casebank:<run_id>`。旧诊断无冲突时仍返回原裸 ID；两目录重名时旧项为 `diagnostic:<run_id>`，裸 ID 请求拒绝歧义。分页响应原样返回所请求的 campaign_id，保留原页面对运行、调用、页偏移和请求序号的检查。

新登记册仅读本侧 ledger 与 campaign.json，验证 wrapper.plan_hash、完整普通 canonical plan 哈希和 run.plan_hash 相同；核每个 call.run_id、call.plan_hash、完整 task_identity 与计划一致。按 task_run_id 归属统计，公开投影只保留 case_id、arm、task_run_id、kind、状态、次数、令牌及原有调用摘要。私钥文件、evaluator_private、prompt 和 raw events 没有读取或返回接口。这些哈希核验只能发现本地记录不一致，不认证市场真实性，也不替代 Gateway 保存事件复核。

## 展示范围

已完成模型调用的公开 response 严格限定四键 `action/queries/candidates/reports`。inspect、diagnose、condition、submit 的公开动作请求均可按需阅读；后续动作语义被拒绝也保留该已完成公开回答。未完成调用或含额外顶层字段的 response 不导出。这里的模型最终返回不等于整项研究的唯一最终报告。旧三键诊断公开回答仍沿原接口显示。

调用分页上限仍为 20；回答分页默认 16,384 字符、上限 65,536。分页不是对底层 ledger 全量读取或单回答 JSON 序列化内存的硬限制。新工具 observation 的完整大型返回目前没有新增按需展示 API，因此本交付不代表 P3 查询交互全部完成。

总用量合计两登记册，并增加 `campaign_types` 分类汇总；合成研究和诊断校准均不计正式策略成功。页面总量说明改为“合计旧诊断与合成研究两登记册；不含工程用量”，阶段门改为“已冻结研究阶段派发”，运行标题区分类型，题目和调用显示 arm，调用显示 task_run_id。running 仍只显示“允许执行”，不据静态记录声称 worker 存活。

## 定向验证与冻结

新组 16 项和原 v10/v11 后端 13 项一次执行，**29 passed in 4.76s**。反例覆盖两目录同 ID、完整查询/终稿分页、私有 canary 不导出、未完成/额外字段拒绝、四种路径输入、两种解析重定向，以及六种计划/调用身份扰动。临时合成 SQLite 文件读取前后逐字未变。继承 HTTP 测试只启动临时本机测试端口并关闭。

凭证目录：`experiment_traces/meta_ashare_revision15/casebank_engineering_attempts/monitor_author_20260906T205027203267Z`。原 receipt 包含输入 SHA、JUnit、日志和执行前后源码一致性；后追加 `page_comparison.json`、`page.diff`、`final_page.html`、`final_page.js`，未修改原 receipt。

| 文件/产物 | SHA256 |
| --- | --- |
| diagnostic_monitor.py | e9b7784c68411c8b67d9a9c884129d6052e9b4b2e816fb7f5b85673ade4e4a4e |
| test_meta_casebank_monitor_v15.py | 85f1b05f7ca86e3a9a373ceef10ed913351d94ff0150f063e0e1a6145ac4025f |
| monitor receipt.json | 2c03d1697b83fc8092c9e39af74f2b94aaa8c3d9e2a698fd3b9dbf35ddf94c0e |
| 最终 PAGE UTF-8 | 48648be7a90de45e7714bbff0ed55f1873e6b3dad9d1dbea935010ca13f10b86 |
| 提取 JS UTF-8 | 2d8ba6c7d2712abe40a34eb5bb31000adcf0430abb96960cf393075eb6ffbb15 |

PAGE 和 JS 因文字、类型及 arm 标签变化，均不与 v14 逐字相同；未改异步身份逻辑。根任务负责最终页面原有 7 项 JS 检查一次。本子任务不重复这 7 项，也不重复此前研究循环 15+6。本文件完成时源码与新测试冻结，等待独立只读复核和根任务组合验收。

本文件之外，薄 CLI 两处空 SHA 修复已单独保留 before/after 和 `casebank_reference_pin_repair_001.md`、`002.md`。最终 CLI 为 d9796523fd2724491ce40a382981d24aafbb680de24e6de54989283fa6603cdb，after_002 的 2 项通过回执为 025f330663fd11edb9fb19508efc3e2c41ffe6e04149e4b4e93e47bf175a50bf。监控和 CLI 不修改十个已冻结研究执行依赖。v15 继承文件差异另包括根任务授权其他代理改动的 raw_portfolio_backtest.py；此项须在联合来源清单中单列，不能把整个 v15 描述为仅监控变化。

## 两问、下一步与停止条件

**实际解决了什么？** 两类保存账本可以按正确任务和架构归属显示用量及公开动作；同名、重定向、串运行或计划身份错配不能静默归入正常行。该结论来自上述合成反例，不依赖模型研究成功。

**还有什么不能据此声称？** 未认证真实费用、策略收益、四题参考发现或运行进程存活，未完成所有工具返回的 GUI 浏览。只读监控不提供派发许可、恢复、重试或私钥访问。下一步为根任务运行最终 JS 检查并组合来源证据，再单独审核冻结预算与真实准入；本交付本身不放行付费。

若出现跨登记册静默选错、foreign call 被纳入用量、未完成/额外顶层字段泄漏、私有文件被读、页面乱序覆盖、或原文件读取后变化，立即停止监控准入并保留反例。若需要展示完整工具 observation，应新增有身份与分页约束的只读协议，不能临时向本接口导出整个计划、账本或私有目录。
