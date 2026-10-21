# v12 两题公开接口复校：冻结终稿独立内容评审 001

2026-09-07，Astra/xhigh。**两个新上下文均提交了合法、唯一、已冻结终稿；各 10 个注册数值声明全部正确，主要诊断解释有本次公开证据支持。** 第一题有三个数值只写在正文、未单列 numeric；第二题没有点名两个多股估值不可拆分的日期。两项均保留为覆盖/表达精度遗漏，不改称数值错误。下一检验接受有效不同设计，但所需新证据目前不在接口内，也没有执行或可得性认证。

这证明本次两个已经暴露的固定题在公开提交契约下产生了合法终稿实例，不证明因果性成功率提升、稳定研究能力、架构优胜、独立 alpha、真实执行或样本外收益。v10 两个原失败、13 次调用及 238,797 token 永久保留；没有将它们改判为本次成功，没有第三 campaign，也没有反馈评语给研究器修稿。

## 身份、冻结与审查方法

新 campaign：[diagnostic_20260906T183231732416Z](../../../experiment_traces/meta_diagnostic_calibrations/campaigns/diagnostic_20260906T183231732416Z/campaign.json)，标准源码 hash `6abf192420312765c42b115c217fd05c760bb3b432e05dc1b4e76fed94c977b9`。按该 plan 的 `evaluation_dimensions` 分别评审合法终止、实际证据使用、全部数字与遗漏、解释、弃权及 next_test，不要求参考措辞，也不生成综合分或架构胜负。

| 题目 | 模型调用 / 实际查询 | 输入 / 输出 token | I/O 合计 | 已接受终稿 |
|---|---:|---:|---:|---:|
| calibration_01 | 9 / 8 | 201,767 / 7,148 | 208,915 | 1 |
| calibration_02 | 6 / 5 | 99,507 / 6,011 | 105,518 | 1 |
| 新 campaign 合计 | 15 / 13 | 301,274 / 13,159 | **314,433** | 2 |

全部 15 调用为 completed、没有未知或预留未结。两版已知研究 I/O token 共 **553,230**，不是账单费用认证。模型/effort 是冻结请求 `gpt-6-astra/xhigh`；实际供应商身份与请求绑定仍没有独立认证。工程评审用量另列，精确计量未知；本审查新增研究 Gateway 调用为 0。

审查只使用 SQLite `mode=ro` 查询原调用、最终已应用 action、query journal 和 submission；不构造 Gateway、不重放查询、不调用 submit/apply、不修改原数据。新产物在 [interface_recheck_artifacts_001](interface_recheck_artifacts_001/input_manifest.json)。复用旧独立 Decimal 记账函数的固定源码，按本次返回的 raw daily/trades/calendar/horizon_inputs 重建现金、加权成本、连续库存和端点；再与 v9 已接受独立 oracle 交叉核对。oracle 只供终稿后 evaluator 检查，不是研究器输入。

两份最终响应与保存回执、ledger action、唯一 journal submission 一致；纯格式校验通过。逐页 `page_sha256`、外层 evidence_id 与全部引用成员核对通过。**本次实际返回页**支撑本文结论；没有拿本次未查询的私有数据给模型补证。审查后再核验 137 个调用/题回执文件与 8 个公共证据文件，均保持原 hash。

## 实际查询与证据使用

| 题目 | 按调用次序实际查询 | 未查询但不应假装查过 |
|---|---|---|
| calibration_01 | summary → horizon_curve → horizon_inputs（30 行）→ target_weights（15）→ trades（2）→ daily（15）→ horizon_events（2）→ session_calendar（15） | 未单独查 spells、inventory、horizon_exclusions。库存可由已返回订单自行重建；28 个 filter_false 可由全部 30 行输入及事件覆盖确定。 |
| calibration_02 | summary → daily（7）→ trades（10）→ inventory（8）→ target_weights（7） | 未单独查 spells 或 session_calendar。summary 已给全部 3 个库存段示例/索引，daily 等页附独立冻结 calendar coverage；原始交易序和 7 日期可复核段长。未取得完整委托、拒单、逐股标记时点或信号对照。 |

所有分页都完整返回，next_cursor 为 null；本次没有缺页被悄悄忽略。两题先看了有派生结果的 summary，之后又查询原始日账和交易；报告给出可复核的跨表算式及序列区别。可确认的是这些可见证据和书面计算，不推断模型内部具体计算过程，也不把我们独立执行的 Decimal 脚本称为模型自己执行代码。

## 数值全量核验与结构化遗漏

金额容差为 1e-6 CNY、return_fraction 为 1e-12、count/sessions 精确。20 个注册 numeric 逐项残差均为零（按声明的十进制值与独立 oracle 比较）。

| quantity_id | 题一声明及独立值 | 题二声明及独立值 |
|---|---|---|
| net_pnl_cny | 669.3，numeric 正确 | -92.00，numeric 正确 |
| reference_pnl_cny | 700，numeric 正确 | -16.00，numeric 正确 |
| fees_cny | 10，numeric 正确 | 16.00，numeric 正确 |
| slippage_cny | 20.7，numeric 正确 | 60.00，numeric 正确 |
| realized_net_pnl_cny | 669.3，numeric 正确 | -82.55，numeric 正确 |
| unrealized_change_cny | 0，numeric 正确 | -9.45，numeric 正确 |
| terminal_single_stock_unrealized_cny | 期末零持仓；在 limitation 正确说明不适用，不填写 0 | -9.45，numeric 正确；2020-01-07 的唯一 A |
| closed_spells_count | **正文 1 正确，未单列 numeric** | 2，numeric 正确 |
| right_censored_count | **正文 0 正确，未单列 numeric** | 1，numeric 正确 |
| closed_duration_sessions | 7，numeric 正确 | 3，numeric 正确 |
| common_event_count | **正文 2 正确，未单列 numeric** | 未声称；本题无信号事件数据，不补造 |
| horizon_1_mean | 0，numeric 正确 | 未声称 |
| horizon_5_mean | 0.05，numeric 正确 | 未声称 |
| horizon_10_mean | 0.095，numeric 正确 | 未声称 |

第一题冻结数值覆盖清单中的 `common_event_count`、`closed_spells_count`、`right_censored_count` 没有独立 numeric 条目，原报告已经达到 12 findings 上限。它们在正文有正确值，所以应登记“结构化字段遗漏，实质数字已覆盖”，不能算成错误、零或完全缺失。第二题冻结 numeric 覆盖清单全部覆盖，另报最长闭合段 3 sessions。两题没有因格式修复发明新 numeric quantity_id。

原逐项结果：[题一算术审计](interface_recheck_artifacts_001/calibration_01_audit.json)、[题二算术审计](interface_recheck_artifacts_001/calibration_02_audit.json)。

## 正文数字、日期与库存顺序

额外执行 [52 项正文算术检查](interface_recheck_artifacts_001/prose_and_post_audit_checks_001.json) 和 [6 个逐事件端点检查](interface_recheck_artifacts_001/individual_horizon_prose_checks_001.json)。这些是同一批记录的校验项，不是独立研究样本。主要结果如下。

**题一会计及目标路径。** 初始 20,000、期末 20,669.3，与 15 日日损益和一致，8 个现金日保留。买入成交额 10,010 加佣金 5 得成本 10,015；卖出 10,689.3 减佣金 5 得 10,684.3；净差 669.3。买/卖滑点 10/10.7 合计 20.7；固定数量参考差额 100×(107−100)=700，扣 10 和 20.7 得 669.3。买单 realized_pnl 空值未被误读为零观测。

全期未实现变化为 0，不表示期内一直为零：1 月 10 日为 485，1 月 13 日退出后的变化 -485，669.3−485=184.3 为该日净损益。由本题明确 open=同日末标记的合同和订单库存，15 日×2 个股票的全部日末 marked value / 浮盈均可推导，合计与 daily position_value 相等。报告正确否定 summary 的笼统逐股估值不可得说明，没有把另一张表字段为空升级成跨表也不可识别。

同一公开 target 定义下，以当日 open 标记的交易前权益还原目标调整单位，asset_01 在 1 月 2–10 日均为 100（原浮点权重造成的还原偏差约 1e-14）；1 月 6 日权益 20,285、open=103 的例子正确。1 月 13 日目标归零且实际全卖；没有“零目标生效后旧库存滞留”的直接证据。唯一连续持仓段从 1 月 2 日索引 1 至 1 月 13 日索引 8，长度 7 sessions，闭合 1、右删失 0。每 3 会话审查并不生成强制卖单；不能按自然日、含首尾计数或 FIFO 年龄替换此定义。

**题一事件。** 全 30 股日中仅 2020-01-01 两事件通过 filter，余下 28 行 filter_false；两候选均有全部共同端点，没有候选被共同样本再排除。两事件均在 1 月 2 日开盘进入：

| h | 退出日期 | asset_01 | asset_02 | 等事件均值 |
|---:|---|---:|---:|---:|
| 1 | 2020-01-03 | 101/100−1 = 0.01 | 198/200−1 = -0.01 | 0 |
| 5 | 2020-01-09 | 106/100−1 = 0.06 | 208/200−1 = 0.04 | 0.05 |
| 10 | 2020-01-16 | 110/100−1 = 0.10 | 218/200−1 = 0.09 | 0.095 |

t+1/t+h+1 与公开冻结日历相符；两事件共享一个信号日期，三期限不是三个独立研究样本。毛事件均值不等于只持 asset_01 的固定订单净损益；“长端上涨不能确定最优持有期或证明信号有效”的限制正确。

**题二完整会计。** 保留 7 日、10 笔原交易，买入成交额 3,171.40、卖出 2,831.40，佣金 16。现金 20,000−3,171.40+2,831.40−16=19,644，加末市值 264 得权益 19,908，累计 -0.46%。费用 16 和滑点 60 加回净损益 -92 得同路径参考 -16，不能把它当新零成本策略回测。

A 第一闭合段已实现 -129.10、B 已实现 49.60、A 后一未结束段部分卖出已实现 -3.05，合计 -82.55；剩余 30 调整单位的含佣金成本 273.45，对应聚合市值 264，未实现 -9.45，二者相加 -92。原逐笔 realized 的浮点舍入差最大约 6.7e-15，远小于公开容差。

2019-12-27 现金日保留；2020-01-06 无交易但持仓净损益 -12 也保留。2019 年末未实现 203.80，2020 年变化 -213.25，才滚动到期末 -9.45；报告正确区分余额与期间变化。B 闭合 2 sessions，A 第一段闭合 3；A 在 2020-01-03 先全卖 70、再买 40，形成新段，不能把日末非零库存拼成一段。1 月 7 日卖 10 后余 30，右删失段观测年龄为 2，不能当作完成持有期。

B 在 2020-01-02 先买 10、再卖 40，前一日库存为 30，报告原序及数字正确。这支持“调整单位库存算术不能认证原始股份 T+1”的边界；它没有把合成记录直接判成已证真实违规。

## 弃权、解释与遗漏

**题一：局部可识别边界正确。** 空末仓使唯一末仓股票标量不适用；账户总未实现为 0 是另一个量。报告用 limitation、空数值和 unit=none 合法表达，没有重犯 v10 的格式问题。期中逐股估值可推导被明确识别；真实到达时点、独立日期、对照、容量与样本外有效性仍没有证据。固定两订单也不能验证完整日频选股或退出机制。没有发现过宽弃权或无据确定性。

**题二：金额可识别、原因未知分开。** 多股日仅有聚合市值，逐股分配不可唯一确定；单股日可推导。期末唯一 A 的 -9.45 不能因 inventory 标记字段空就弃权。独立重建显示真正多股不可拆分日期是 **2019-12-30、2019-12-31**；报告只写“多股并存日”，未点名这两日，亦未逐日输出全部可识别股票浮盈。记录为范围精度/详细向量遗漏，不能认定它声称其他日也未知。

实际 target 表在 1 月 3 日和 1 月 7 日 A=0，而日末各有 40/30；1 月 6 日 target=0.1，所以不能说期间一直是零目标。报告只点名两个正确日期，并明确目标生效和调仓机制未知，没有预设停牌、锁仓、拒单或持有过久。它同时保留 1 月 3 日真实全卖后重入，未把新持仓误说成旧仓从未卖掉。费用扩大损失是会计事实，不能据它单独推断亏损的市场、行业或信号因果来源。

## 下一项检验：接受有效不同方案

**题一的预注册同日匹配对照设计有效。** 它针对“长端上升由共同走势解释”，要求未暴露多日期资料、信号时点可确定的对照资格及特征、冻结共同 `[1,5,10]`、完整候选与排除、日期/股票/窗口依赖及多期限处理。支持条件为两组共同上涨但差值接近事前无实质效应区间且精度足够；不是把不显著当无效证明。相反，稳定相对优势可反驳仅共同走势的解释；仅较长期有差别也不能证明 1 会话预测力。此方案与参考模板措辞不同仍有效。

当前两个首日股票均为信号，没有本题已返回的合法同日非信号控制组；本题未提供多日期新样本和真实到达证据。报告明确这些缺口，只提出下一设计。v12 的 event_conditioning 是尚未接 researcher 的纯工具，不能把本份文字建议或工程 toy 算作该匹配检验已经执行。未来资料与时点证据是否实际可取得，仍需单独核验及授权。

**题二的目标时点—委托—成交—估值对齐设计有效，但实施有条件。** 依据已查询的两个零目标/非零库存日期，检验“有效清仓意图未完成”，并区分目标尚未生效、允许保留/重入、未生成委托、拒单、未成交以及快照/账务差异。它明确要补完整日志和实际生效规则，没有说当前接口已经提供，也没有为了贴合参考模板强行优先做相同逐股估值问题。

该设计中的估值部分只能核对残余敞口损失归因，**不能用陈旧 mark 解释股数为什么持续或单独推翻未退出的数量事实**。按报告原句“若损失来自陈旧估值修正，也反驳将其直接归于清仓未完成”的损失层含义理解是合理的；若扩读为估值更正可消除数量差，则不成立。原报告前文已把量、时点、损益分开，因此不作这种不利扩读。日后必须保留 1 月 3 日先卖后买这一竞争解释，不能把它省略为单纯拒单。

两题所需新资料当前不在 query 接口；尤其原合成记录可能从未生成所要求的完整委托/到达日志，不能事后手造日志补成原历史事实。若拿不到旧记录，只能另行事前记录的新机械实验或新数据验证，不能把本次诊断改写成已经完成的因果归因。此处评为**逻辑上可区分解释、依赖未来补证的设计**，不是已就绪可执行的研究。

## 证据文件与不可变身份

| 文件 | SHA256 |
|---|---|
| [题一原最终 response](../../../experiment_traces/meta_diagnostic_calibrations/campaigns/diagnostic_20260906T183231732416Z/calls/calibration_01_round_8-1/response.json) | `b607ed3ebff2b66b58764fdc6074e82d657d582a4857a9556f5cfc61f00dc0a4` |
| [题二原最终 response](../../../experiment_traces/meta_diagnostic_calibrations/campaigns/diagnostic_20260906T183231732416Z/calls/calibration_02_round_5-1/response.json) | `f84969441a24481ade50f534ec3bdea289e4a9a687232b8f85a4d9b79c1e4bae` |
| post_run_frozen_records_001.json（137 文件） | `ae0db82a8883bc20bc8162b8b66841e6a1b2d04bf67e3fdbed9976b1968ba50b` |
| post_run_public_evidence_001.json（8 文件） | `373f6d8b9ab44a616353bece04055419e9447a40ca9fd0df73d3f93ff0137798` |
| audit_frozen_finals.py | `6048455018f08c0605f79bd8431b44bca4c179fb9e1dca7e21431c9dac25b08e` |
| calibration_01_audit.json | `1907bbca96b9b4e4622914cda5ee26ce49334d4b695d4ad80ce9f9c97667d5a2` |
| calibration_02_audit.json | `558d68c02d717114d2ec1bc8d1ad558d888573e93acd744178a9c82c15c8e67a` |
| input_manifest.json | `a61d24641d639aabcab8933eb07c809f5d2ab39315312eea9b648ec77e1aea76` |
| prose_and_post_audit_checks_001.json | `7ed9d77a2ac28c35fb7f3f48bd54b20cfecd8654af14d3ba0b2f1d73d65214fc` |
| individual_horizon_prose_checks_001.json | `6bc3958bc4dc28f79c4ff295e77eeee6e537589a2aec14fc4093e8b9012b3a56` |

原题级回执为 [calibration_01_20260906T183900327962Z.json](../../../experiment_traces/meta_diagnostic_calibrations/campaigns/diagnostic_20260906T183231732416Z/receipts/calibration_01_20260906T183900327962Z.json) 与 [calibration_02_20260906T184314732461Z.json](../../../experiment_traces/meta_diagnostic_calibrations/campaigns/diagnostic_20260906T183231732416Z/receipts/calibration_02_20260906T184314732461Z.json)，都已包含在原冻结文件核验内。所有新审计产物用 exclusive-create 追加，不覆盖原输出；没有手改任何研究器终稿。

## 两问自检与后续边界

**这一步做得怎么样？** 已逐项验证合法终稿、证据范围、20 项注册数值及正文数字、连续库存原序和两种不同 next_test，明确保留结构化遗漏与未来补证条件。没有因参考答案不同措辞扣分，没有把 summary 中的派生值视为模型独立发现，也没有把我们复算的代码当作模型行为。旧两失败及费用不变。

**下一步该做什么，如何改进？** 关闭这一次接口复校并保留两新合法终稿及全部评审，不再开第三 campaign 或喂回修稿。后续若要研究稳定能力，应另行事前设计新资料、强基线和独立比较；当前两题不能替代这些证据。若推进日志/逐股标记或匹配工具，只能在另行冻结的证据与预算下取得，不能追补原历史未知为已知。`execution_valid`、真实 PIT、容量、实际供应商独立认证和正式策略目标仍未通过。
