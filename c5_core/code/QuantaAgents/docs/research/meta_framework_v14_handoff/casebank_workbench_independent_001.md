# v14 CasebankWorkbench 独立路由审查 001

2026-09-07，Astra/xhigh。**离线公开工作台在本次范围内有界通过。独立反例发现超长有限 JSON 请求漏记机会，作者已最小修复；修后一次受影响回归 33 项全部通过，包含未修改的 8 项独立检查。** 原始 2 项失败保留，不回写为修前通过。本结论不授权模型派发，不证明四题可发现性、同预算参考通过、架构稳定性或执行收益。

本审查只新增 [独立测试](../../../experiment_traces/meta_ashare_revision14/tests/test_meta_casebank_workbench_independent_v14.py)、该测试的 before 凭证和本文。未修改产品源码、作者测试、v13、原题包、旧付费记录或监控。没有调用 Gateway、联网、读取真实行情、运行策略回测或执行四个实际题包的候选发现。工程评审本身使用模型，其 token 未在这里独立计量，不能记成零；新增项目研究 Gateway 调用为 0。

## 接手版本与实际反例

作者交接源码为 `f16bd56fb15e6f103cea59a780f2cf2857509e97762095a7793895bf723c616e`，作者 25 项专组为 `086af15b914a9de2cd0c03d04e331d8193f61ce03d9b612b12abca6a36401716`。阅读模块全文、公开加载器、Store 原子 update、期限与匹配核相关路径、公开 packet 合同和作者交接文档之后，使用独立构造的 16 会话 × 4 中性符号字面等差价格 fixture 检查实际 API。这个 fixture 不读取生成器、私有种子/标签/参考，甚至不读取实际四题目录；网络、子进程、私有路径和实际四题路径均有测试读取守卫。

修前实际运行：**2 failed、6 passed，36.32 秒**。凭证目录：[casebank_workbench_001_before](../../../experiment_traces/meta_ashare_revision14/independent_review_attempts/casebank_workbench_001_before/receipt.json)，receipt SHA256 `22b53f90112cc3a1aeeff8dfb3525043c3e5f2693191e4712effbf0b69b595dd`。保留初始源码/测试 hash、完整 pytest 输出和 JUnit；修后再次逐项核对其文件 SHA，原失败未改。

| 发现 | 修前实际结果 | 最小修复及验收 |
| --- | --- | --- |
| 超长 final 在预算登记前被拒绝 | 12 条 4,000 字符 claim、12 条 4,000 字符 limitation、8,000 字符 summary 均满足各字段公开 schema，但总规范 JSON 超过 65,536 bytes；`execute` 在 `_copy` 抛错，`final_attempts=0` 且工作台仍 ready，留下免费改稿机会。 | 规范 UTF-8 JSON 分段计完整 SHA/字节数，原子登记唯一 final 后保存有界失败摘要；不运行工具，final 为终态。同槽原请求只能重放保存证据。 |
| 超长有限 JSON 候选也漏记机会 | 带超长额外字段的已知候选动作被入口拒绝，query/candidate 均为 0。 | 已知动作仍收 query/candidate 和对应子项机会，再记已知失败；后续新槽仍需消耗机会。原正文超界时只存明确的摘要及原 hash/bytes，不宣称全文已保存。 |

原文身份 `request_hash`、实际保存摘要的 `stored_request_hash` 分开绑定。`_row` 校验它们的关系、原字节数与 `body_retained`；同槽重放还核对完整 request identity。作者在既有恢复测试内补充中文多字节请求、原/存储双 hash、精确字节数、关闭后重开、改变一字符和伪装成 rejection envelope 的拒绝。公开 overlay 现在直接说明字段上限与总规范 UTF-8 JSON 上限必须同时满足、超长有限 JSON 仍计机会、超长 final 结束工作台。不可序列化/非有限对象属于入口拒绝范围；这不是传输解析或 OS 内存沙箱。

## 公开路由、机会与恢复

`create` 以控制器固定的公开 manifest hash 经公共白名单加载器读取 packet/CSV/sessions，建立新目录、新 Store 和包含 run/arm/原公开资料/overlay/源码/key fingerprint 的 scope。`load` 只能核对这一已存在工作台的冻结来源；`prompt` 公开原普通包、执行合同、当前剩余额度和本侧已保存历史；`action_schema` 给出当前可用动作。inspect、diagnose、condition、唯一 final 均通过 `execute(slot, action)` 路由，`recover_saved` 不调用计算核。

- 16 query、12 candidate、1 final 在 Store 的 `BEGIN IMMEDIATE` 事务内检查和登记。分页每页消耗 query，最多 50 行；12 个候选是 query 的子预算，不能额外增出 12 次免费查询。新槽重复与已知失败仍保留机会；同槽相同请求只能恢复。超过可用预算的动作被保留并结束工作台。
- diagnose 固定 `[1,5,10]`，登记 selected/benchmark/increment 共 9 个输出子项；condition 单条件 15、双条件 27 个子项，包含全部空组。它们另有本槽 ID、核子项 ID、结果行 hash 和 computed/empty/failed/reused 状态，不能把三期限或多个组写成独立研究样本。
- 独立双 Store 连接反例在第 16 个 query 已预留且未完成时尝试竞争同槽与抢占第 17 个 final：均被拒绝，query 保持 16、final 保持 0；原请求完成后第 17 槽唯一 final 可提交，同槽重放没有新增机会。作者专组另外覆盖 12 candidate 边界及新槽重复/已知失败复用。
- 完整文件的预期 SHA/bytes 先登记，随后 exclusive 写入并 flush/fsync。独立反例在匹配计算结束后仅写 31 bytes bundle 即注入磁盘错误：原 1 candidate、1 核启动与未知槽保留；关闭重开后的恢复和新动作都拒绝，没有重复核调用。作者专组覆盖意图、plan、核、完整 receipt 后中断及 artifact 篡改；完整回执恢复只做保存证据核对。
- 游标 HMAC 绑定 scope/table/source evidence/offset；诊断表必须属于已返回诊断的 available_tables。最终报告的 scope、必填字段、非空文本、结论词表和本侧证据引用均公开，无隐藏 quantity 或参考模板。失败 query 的 evidence 可以支持不同文字的局限/弃权，独立实际提交通过。合法 final 只得到结构及引用合法，`quality_assessment=not_performed`。

## 数值、条件与时间核验

独立 fixture 的双条件 `state_01=1 AND state_02=1` 只选一个公开符号。A 块产生 8 个信号，固定三期限共同覆盖 5 个；其余 3 个尾部机会仍在完整事件表。5 个完整 h10 退出都跨入 B，仍参与计算。未通过缩短结束日期或改块标签删除跨块退出。独立 Decimal 按原字面价格逐事件计算 t+1 入场、t+h+1 退出，以及当日完整四符号等权基准，再核对三条增量均值；没有调用被测诊断函数生成 expected。

匹配反例逐次检查：核开始之前 `plan` 已按登记 SHA 完整保存；decision-only 行不含 open/close/outcome/entry/exit 字段。交换所有符号的未来价格斜率后重新用同一决策规格构造独立 fixture，保存的信号/对照配对坐标完全相同；不会择优选结果较差的对照。双条件保留四个声明组，包括没有选中信号的组。

另在已选定的首日第一对照结果缺失时保留原 8 对，其中共同覆盖从 5 降到 4；三个期限均保留 `control_outcome_row_missing`，没有换成仍有结果的第二个对照。该故障仅模拟纯核返回链的缺失结果，没有修改实际题包。各观察单位的共同样本由其自身明确分母定义，不能把 signal-only 与 matched-pair 的不同样本数混成同一个比较。

`signal_block` 只限制信号日期，opening frame 保留全会话；单状态与双状态都是事前公开字段条件，不是价格结果筛选。历史 CSV 在创建时已读入，研究者也可查询历史结果，所以 plan 先持久化只证明本地派生计算顺序。`chronological_commitment_verified=false` 和 `historical_PIT_verified=false` 是必要且正确的边界，不能把 source hash 当外部到达时间认证。

期限与匹配结果均为毛端点回报/毛增量；成本合同公开但 `costs_applied=false`，没有组合现金、真实股份、成交约束、换手与净 PnL。日期相关性、重叠端点及空共同样本均保留，`independent_sample_size=None`。当前没有把 accepted final、单个显著方向或测试算术收益转成 alpha、执行有效性或研究成功。

## 修后回执与冻结身份

作者按 root 的协调要求统一运行自身 25 项与原样独立 8 项，结果 **33 passed in 90.31s**；after receipt 的驱动总耗时为 92.641 秒，两者口径不同。审查者没有重复跑第二遍，而是复读最终修复、核对当前三文件 SHA 与回执/input_hashes 一致、pytest 日志 hash 一致、JUnit 恰有 33 testcase 且 failures/errors/skipped 均为 0，其中独立 8 项全部存在。

[after receipt](../../../experiment_traces/meta_ashare_revision14/independent_review_attempts/casebank_workbench_001_after/receipt.json) SHA256：`843ef8f6c3138e228211a2638ed5d993a15b1f0e7fadf4fad61a68406be95013`。JUnit SHA256：`6cadafdc4ff49ad9633396723b9782c715e934d6e14bb88f86e434558dc8de23`。不把修前 6 pass 与修后 33 pass 相加成独立样本。

| 最终文件 | SHA256 |
| --- | --- |
| `experiment_traces/meta_ashare_revision14/src/quanta_agents/meta/casebank_workbench.py` | `ea2941fc5776e72c32a47f67b01c169b94a754945b0ce85ee8a3af8b04fb9ff3` |
| 作者 `tests/test_meta_casebank_workbench_v14.py` | `1f8acf6872f4411f2fe3dc67526985cf590601d6b383539b1cd902c15248de2e` |
| 独立 `tests/test_meta_casebank_workbench_independent_v14.py`（修前后未改） | `ab81f77936e8c816855dda4d109a361f7bba11afec1adc6ddfb4ac578037979d` |

另核对 copy_source_manifest 中 228 个继承文件：v13 全部仍与复制时 SHA 相符；v14 的 227 个相符，唯一继承差异为 root 维护的 `diagnostic_monitor.py`，不属于本审查及本工作台五文件来源绑定。未把作者较早“228 个 v14 继承文件未变”的快照扩展为当前结论，也未重开已关闭的 GUI 审查。

## 仍未成立的资格与两问自检

此处通过的是离线公开 API 到纯工具、持久证据和唯一 final 的接线。尚未接入可信 live harness，没有模型 call_id/费用/工作台动作的实际付费闭环，也没有四题实际候选延迟或同信息同预算参考发现结果；这些不能从 toy 检查外推。原包维持 exposed synthetic、hidden_control_qualified=false、execution_valid=false、独立策略成功分母贡献 0。控制器单实例预算不是跨账户预算或同用户任意 Python/文件权限沙箱。

**这一步做得怎么样？** 独立运行实际反例定位并保存了一类机会漏记，作者修复后原反例与相关专组一起通过。公开规则、机会、原数据与失败分母均保留，没有以模板答案或裁掉跨块/缺尾事件获得通过。本范围目前没有未关闭的实质阻断。

**下一步该做什么，如何改进？** root 按最终 hash 完成其余受影响工程封板，再独立决定后续 harness/参考研究准入。本报告不自行启动研究。若出现超长 finite JSON 再次免费纠正、未知槽允许新计算、保存 plan 后按结果换对照、跨块/尾部机会消失、私有资料进入公开 prompt、跨侧证据接受，或把毛事件结果标成净执行收益，应停止相应扩展并保留反证；不能回写旧结果、重抽题库或删分母补过。
