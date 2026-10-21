# v17 保存动作续行：独立工程评审 001

结论：列明版本的保存动作续行控制器、只读投影和分页接口有界通过。两个已运行的控制边界反例及 duration 展示缺口均有保留的修前失败和对应修后证据。具体实际 scope/admission 尚须另行核对；本报告没有打开付费，没有执行实际四题工作台，也没有延长原 2026-09-07 03:01:23.682649 UTC 下一模型准入截止时间。

独立评审为 Astra / xhigh。新增实际研究 Gateway 调用 0，实际市场 / private / 原题 CSV / cursor key 读取 0，实际账本与工作台写入 0。所运行反例全部是临时合成算术数据；后续验收只读保存的回执和源码。工程评审模型 token 未单独核定。

## 冻结版本与结果口径

| v17 文件 | SHA256 |
|---|---|
| `src/quanta_agents/meta/casebank_continuation.py` | `6cc0dc07cb60ac3f8f8b507b911c028a85a82305283132be4432a9b401b19b86` |
| `src/quanta_agents/meta/transport_correction.py` | `6344082f22c2fc4f517565ed14be5666f36f40d6cc7e26d3a6d95c1a50f96663` |
| `src/quanta_agents/meta/diagnostic_monitor.py` | `369a2d879108bf704d151985d94970891a33c83599cfa402548707e7b5b85a23` |
| `scripts/run_casebank_continuation.py` | `718120916a846cfe7ff9955b326d6148303f9ce5486746887dcf819540e98c2b` |
| 本评审 `tests/test_meta_casebank_continuation_independent_v17.py`，7 项 | `946e3f5b0c36b6a954c93d6915d0d474ec3805ee1f8c1c54b79716af27527b09` |
| 当前 GUI 集成测试 | `0423f6b2bcc6cc87c6cc1ba72a0954a809fb6e762b1f1f30bfb6df7c7a7f2f36` |

实际验证组合为 **核心 29 项 + 当前监控 52 项 + 修正分页消费的 GUI 集成 1 项 + duration 10 项**。JavaScript 另有 **15 项**。它们来自明确不同的执行记录，不称同次 92 项或同次全量通过；本评审没有重复运行根代理与作者已完成的组。

核心 29 项包括作者 21、本评审独立 7、另存的 saved-origin 1。核心组验证时 transport 仍为继承版本 `5fa687…`；后续 10 项和源码 AST 差异证明 transport 变更只限只读 duration 元数据，当前监控与真实合成控制器集成在新 transport 下通过。因此保留核心控制实现的原版本边界，没有虚称全部 29 项都在新 transport 字节上重跑。

## 缺陷与独立验证

**可变缓存预算绕过。** 冻结 task cap 为 171,234，原未知 80,000 与已报告 11,235 使下一次 80,000 预留本应被拒。将 `c.budget['per_task_hard_tokens']` 改为 171,235 后，旧实现实际抵达 ForbiddenGateway.run；陷阱立即停止，没有模型调用。修前 1 failed / 10.14 秒及源码副本保留。修复在 `_check()` 中核对缓存预算和控制器 identity 与冻结 scope 完全一致，原反例在核心 29 中通过。

**保存纠错回答被入口忽略。** prepare 后，已付费完成的纠错回答尚未应用，旧 `_history()` 仍允许构造下一 prompt 和内部预留；公开 run 原本先 `_apply()`，其正常路径安全，但单独入口的防御门不完整。另存反例真实 1 failed / 12.83 秒。修复只增两行：首 task 的历史中必须存在该 correction origin 且 applied，才能构造下一 prompt 或预留。修后独立保存来源检查通过。

**来源不能跨 task。** 本地投影不能仅因两个 task 返回相同 inspect JSON，就接受将一个 paid origin 绑定到另一 task 的 observation / 计数。root 提示后作者先作静态修复；本评审未回滚制造 before。修后反例仅修改临时库的新续行投影行，重新计算本侧一致性 hash，同时保留原 plan/run/call/correction，确认 origin 的 task identity 和 semantic round 不一致被拒。

**真正跨进程与暂停。** 两个独立 Python 进程竞争同一保存来源，只有一次 WB.execute、一个 query；两个进程竞争第三 transport，只能登记一个预留，最终仍是 3 次传输、11,235 已报告、160,000 未结预留、171,235 exposure。已有 intent 但 WB slot 尚不存在时，首次执行再次核 ready 和原 deadline；暂停或过期不能借旧 intent 开始新工具计算。仅本地应用的 admission 无法通过 run 预留或调用 Gateway。

**公开上下文。** 模型 prompt 保留本侧全部实际 action 和返回的 public observation，不为原 HTTP 失败编造 assistant 回答；独立检查第二次新模型入口的已生成 prompt，核对实际页 limit=1、完整本侧轮序及其他三题隔离。保存完整 backing artifact 的身份不等于把其全表内容暴露给模型。完整上下文超限会停止，不截掉历史后继续。

核心作者组还覆盖原四题任务身份、逐阶段预算、普通失败与 invalid final、两库崩溃后保存证据结算、未知费用停止、原 Workbench 源码隔离以及 SQL 只读路径。恢复没有重新询问模型；缺完整 WB 产物时不重算工具。

## 展示修复与失败记录

首次联合为 52 passed / 29 failed。29 项在深层临时路径中生成合成纠错夹具时遇到 WinError206，尚未抵达各自控制断言；保存诊断逐一记录这些错误。换独立短 TEMP 后核心 29 真正通过，GUI 先暴露夹具遗漏原 call 的 plan_hash/task_identity，再暴露 duration 安全投影漏字段。这些失败都保留。

夹具修复只有两行说明和一行 `call.update(plan_hash=..., task_identity=...)`。独立从当前 `bab55…` 测试文件去除此确切三行，整文件 SHA 还原核心运行的 `1cd1…`，说明测试断言和其余逻辑未改。这是有 hash 验证的逆变换，不声称它是当时保存的完整源码原件。

duration 补丁仅在 `read_correction_projection` 校验已保存的 duration 为有限非负数或缺失，并将它加入 safe receipt；没有改派发、费用结算、预留或恢复控制。AST 核对除该函数和 `math` import 外不变；原 v16 文件仍为 `5fa687acd947870d4b4fd2896a1a693e6f1e3de9c8a28614336e82ffbff0d3f1`。10 项定向测试已核验通过。

因没有旧 `8ed50…` 监控源码原件，未使用“当前 API AST 与旧版等价”的未证声明承接旧 52 项；root 在当前 `369a…` 源下重新运行了 52 项并全部通过。该次 GUI 单项把 16,384 字符第一页误当完整 JSON，产生 JSONDecodeError。测试修正为最多八页，严格沿 next_offset 取得原限额页面，核对 campaign/call/content_kind、逐页 offset、observation hash、evidence binding hash、总字符数，再核对组装后 canonical observation hash；未放大产品 page limit 或修改产品代码。修后单项通过，先前失败保留。

该集成使用真实控制器和原版本 Workbench 的合成临时包：两次显式 apply 仍只计一次 query；原 run/call 与 correction body 不变；其后 inspect / snapshot / answer / observation 读取不改变两类数据库。它是 API 集成验证，不冒称真实浏览器交互认证。15 项 JavaScript 来自当前提取源码的确定性离线反例，亦保留这一限制。

## 保存证据索引

下列目录相对 `experiment_traces/meta_ashare_revision17`：

| 回执 | SHA256 |
|---|---|
| `continuation_independent_validation/001_mutable_budget_before/receipt.json` | `c5daf5b3337aa6a56c753752acdf15d1b6bedb01917b4ed24661f6da063491bc` |
| `continuation_validation/saved_origin_before_001/receipt.json` | `950bc8def00598dca7409606b79a1ab51033252db72a6574b8e42707793d120e` |
| `continuation_joint_validation/002_short_temp/receipt.json`，29 pass / GUI 1 fail | `c77977b66936db2678073b707053c6ca0af25dc6d025ab7e12997c71a92ec6be` |
| `continuation_validation/duration_projection_001/receipt.json`，10 pass | `9037debe9fcc33b9541456b068681cb0a82e10c94bebefad4cb911eb11a239bf` |
| `continuation_joint_validation/004_final_monitor/receipt.json`，52 pass / 分页测试 1 fail | `3c560b171768303136aeb6180229b5a3c25f234dece94a883c386b7543562ce7` |
| `continuation_joint_validation/005_paged_gui/receipt.json`，1 pass | `6f465159b7091af4b981bd896f7c4aea8319a70bc5ed233dec2dd5eed895d233` |
| `continuation_monitor_js/002_final/receipt.json`，15 JS | `6b8e3e6e76089227d370e8d5012e30383d687db7ba8eebac99f7a02ce911f94e` |
| 本评审 `002_core_saved_review/receipt.json` | `a235f4360f4852d8ac966ab5059a6779ee26ec4565ef5afffb1a303d7a92b5f1` |
| 本评审 `003_duration_metadata_saved_review/receipt.json` | `e140343597711ac8dc1103d46dec4a116e9dc983c06d92cd3108acb90f64d864` |
| 本评审 `004_final_monitor_saved_review/receipt.json` | `d037d6e442f894c0f0e60f7f47698934aa4fe874531b8a080a380e918b1cd017` |

后三个“本评审”路径均在 `continuation_independent_validation` 下，逐项核对 JUnit、产物 hash、源码前后 hash 和当前相关文件；未重跑测试。未把原 001 联合的 81 项称全通过。

## 实际准入边界与两问自检

工程结果可以支持另行审核的、明确绑定原 scope / 来源 / 保存回答 / 原工作台 slot 的本地一次应用。若实际 admission 为 paid=false，不能派发模型；若允许原截止后应用保存回答，该允许位也只能作用于明确本地应用，不能重置下一模型 deadline。旧失败未知 80,000、旧两个 transport 与已报告 11,235 的分母必须持续保留。实际供应商模型身份仍未独立验证。

1. 是否借换版本、移除失败、缩减预算分母或旧 intent 开新机会？没有；真实失败、环境失败与测试消费失败均分别保留，修后路径仍绑定原任务和账务。
2. 是否把测试可运行、保存回答应用或本侧 hash 一致视作研究成功、收益、PIT / 执行认证或架构稳定？没有；本结论只覆盖所列工程控制，实际工作台内容发现与后续研究资格不在本次检查内。
