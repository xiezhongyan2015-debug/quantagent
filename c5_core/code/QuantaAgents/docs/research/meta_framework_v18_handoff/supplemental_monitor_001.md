# v18 补充阶段只读监控接线 001

本次完成补充阶段公开投影的实际接线及有界合成验收。`paid_dispatch_authorized=false`、`live_browser_verified=false`、`service_deployed=false`。这些是本交付的范围声明，不替代 root 对其他阶段的独立授权。本子任务新增真实 Gateway、真实市场读取、实际登记册写入和服务操作均为 0；测试使用临时登记册、真实保存/解析逻辑和 FakeGateway，工程模型用量不计入“真实 Gateway 0”。

## 行为与边界

`diagnostic_monitor._read_campaign` 显式调用 `casebank_supplemental.read_supplemental_projection`；仅返回 `None` 时调用旧 continuation reader。存在阶段但验证报错时关闭展示，禁止回退。得到投影后仍执行原 `_merge_continuation` 的身份、用量、回执及公开 observation 核验；本次未改其函数体。监控路径只有只读 SQL，未构造可写 Store、控制器或 Gateway。

`snapshot.campaigns[].supplemental_phase` 使用严格字段白名单：阶段 ID/hash/保存状态、固定起止、原截止、原阶段 expired_incomplete、时间资源增加、独立任务增量 0、旧付费许可 false、新阶段已登记许可、未验证 worker 存活、无第三窗口授权，以及有界时间违规记录。私有附加字段、准入正文、私钥和任意嵌套对象不公开。新许可不是当前可派发证明，仍受窗口、预算和暂停门约束，原 checkpoint 许可不会被新字段覆盖。

页面分别显示原阶段已到期未完成、原运行保存状态、新固定窗口、当前阶段保存状态和两个许可来源。时间违规时显示“阻止新派发”，即使原保存状态为 ready 也不能呈现为可执行；原费用、未知预留和原始派发时间仍可查看。worker 显示未验证，不据记录宣称进程存活。没有增加控制按钮或扩大研究工具合同。

## 实际证据

所有目录均相对 `experiment_traces/meta_ashare_revision18/supplemental_monitor_validation/`，原 stdout、stderr、JUnit、源码副本和差异均保留。

| 证据 | 结果 | receipt SHA256 |
| --- | --- | --- |
| `001_before/receipt.json` | 原 87d 源码：Python 3 failed、JS 3 failed | `1193523366cd656a8ff1087ff267dae3a477f28a9fcefba48ff6cfa8030c9819` |
| `003_after/receipt.json` | 最终 038f 源码：Python 4 passed（61.96 秒）、JS 4 passed | `d574e67e419d84f5ffcf79465c64ecf0f33378dc21319175af7cc0531eb26143` |
| `004_scope_proof/receipt.json` | 只读 AST、JS 函数体、来源和既存输出核验；没有重跑测试 | `b9da60130c618b14b424e97163061070eb841148ab2236c959e13c448013ec88` |

原三个失败分别为缺阶段公开字段、错误直接走旧 reader、阶段坏 hash 仍走旧路径。随后保留原测试副本并新增已独立复现的 deadline 风险对应显示检查。最终四项使用真实临时 SupplementalStage：prepare → FakeGateway 保存完整调用 → 显式 apply → 监控；未登记阶段仅 None 回退；已登记坏 hash 拒绝；准入后跨截止、0 模拟进程的未知费用仍可见。读前后临时数据库与工作台文件字节一致，SQL authorizer 仅允许 SELECT/READ/FUNCTION。

成功 fixture 在付费完成而未应用时不伪造 observation，显式应用后才显示已保存结果。时间违规 fixture 保留已报告 11235、名义预留 160000、暴露量 171235，两项未结清；这些均为合成测试金额/用量。新增 JS 四项检查固定窗口及原状态区分、许可/私有边界、时间违规不可显示 ready 许可、刷新后旧阶段文字移除。未重复旧 52 项、旧 20 项控制器组或已有 JS 组；不同证据运行不能合称一次全量通过。

## 冻结身份与精确差异

| 文件/产物 | SHA256 |
| --- | --- |
| `src/quanta_agents/meta/diagnostic_monitor.py` | `038f660f7530d583673d389531a7999f0d3e1cf9351efe7694131ccf4e41860c` |
| `tests/test_meta_supplemental_monitor_v18.py` | `933bbca1265150fce7f473bfbdc20bf8c90a72df54798fd65c5a3aa5af22d88b` |
| `scripts/check_supplemental_monitor_v18.js` | `d8ef845ff8aeb8bb9fac5a26eaeabc814add2911b2a5a67b92393ab142e1bcf3` |
| 最终展开 PAGE JS | `5668a35a3c3b8cc7e28a6299d4b5515e20f8d44790d02ebc72258cebaa8dcbcd` |
| `003_after/source.diff` | `a15bbdee316bca4c252d38fb329283ee0314b998639e9d453e42ea7ad38b9343` |
| `003_after/page.diff` | `a5b4f2135d1ed3c8f53eeb5a348619be942c4835ebe7094f8d860c418b5dfa81` |

最终 after 绑定 core `6e38e602a1850930cd5bb2fe3d43f7f6a7b35e4c4fe62953506e759de04f9919` 与 phase `83e04e8875fe4ebf85ec5ab6ad931545cac8934e2f6cd90ae91d6993475aa392`，九项源/测试输入前后稳定。phase 的截止修复由另一作者负责；本报告不把本次显示检查替代其控制器独立反例。

AST 对比原 `87d236c7cab3a6073fd1e5c1291f51c4263904651fb5873daace606705e948a4`：已有定义仅 `_read_campaign`、`snapshot` 改变，新增 `_public_supplemental_phase`；Handler、回答/分页、费用、自检函数和 `_merge_continuation` 全部不变。JS 旧加载/身份/分页函数体逐字一致；refresh 除新增阶段显示调用、违规标题、原状态前缀三处展示差异外相同。全部 v17 原 273 个复制来源文件仍匹配清单 `de8c84c0e7b41fcd5393869204df3a7f69e888634b9f995199fa477bb0d03c28`。这不是以源码比较冒充旧测试重跑。

## 两问自检与停止条件

1. **这一步做得怎么样？** 预期是新阶段真实记录能被只读显示，坏身份关闭且费用不消失。原失败证明此前未接线，最终合成闭环及 deadline fixture 支持修复。未观察到既有身份/费用/分页退化，静态等价证明范围已列明；真实浏览器与实际保存阶段的完整适配仍未知，不能据此宣布 GUI 整体通过或研究完成。
2. **下一步该做什么，如何改进？** 由 root 在最终来源冻结后只读审查并完成需要的真实浏览器/服务验证，预算与准入另行判断。本子任务不运行模型、不扩窗口、不改实际 scope。若发现存在阶段却回退、坏 hash 仍显示、未知费用消失、待应用响应冒充已应用结果、私有字段泄漏、时间违规呈现为可派发，或冻结 SHA 不符，应停止采用该展示增量，保留反例再修；不得用隐藏错误或补派发来恢复页面。
