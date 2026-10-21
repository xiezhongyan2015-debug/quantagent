# 实际第一题独立复核 001

2026-09-07，Asia/Hong_Kong；Astra / xhigh 独立评审。对象为 campaign `diagnostic_20260906T172850309804Z` 的 `calibration_01`，遵循该 campaign 冻结的 `evaluation_dimensions`，允许有效的不同解释和下一检验。只读已经保存的失败提交；未改 v10、campaign、原始回答或账本，未重新提交、调用研究模型、查询新市场或回测。

**结论：原结果仍为 `failed / invalid_diagnostic_submission`，但这次拒绝的直接原因是未完整公开的跨字段格式约束。完整报告的 9 项结构化数值均正确，主要诊断与弃权边界成立，下一项同日匹配检验有区分力。** 两项冻结要求的计数只写在正文，未单列 numeric finding，应登记结构化遗漏；不能把本次改记为合法 final 或无瑕疵成功。

## 实际记录与拒绝原因

原 campaign 回执：`experiment_traces/meta_diagnostic_calibrations/campaigns/diagnostic_20260906T172850309804Z/receipts/calibration_01_20260906T173533293868Z.json`。原回答见 [response.json](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_diagnostic_calibrations/campaigns/diagnostic_20260906T172850309804Z/calls/calibration_01_round_6-1/response.json)。全部 7 个调用已完成、6 次公开查询、131368 input + 7049 output = **138417 token**；最终公共 submission 表为 0 行。费用保留，不把缓存或 reasoning 子计数再次加入 I/O。

ledger 中最后 action 的原错误为 `non-numeric finding must not contain a number`；pre/post state 哈希相同，action 状态 `rejected`。触发的是第 6 条：

```json
{"claim_type":"limitation","quantity_id":"terminal_single_stock_unrealized_cny","availability":"not_identifiable","value_decimal":"","unit":"CNY"}
```

该条正文明确说 2020-01-21 没有库存，所以“期末唯一持仓股票未实现损益”不适用，不能填 0；可确定为 0 的是组合期末未实现损益。这与本题公开量定义及独立 oracle 一致。

真正触发条件是 `diagnostic_calibration.py` 对非 numeric 的 `unit != 'none'` 检查。`value_decimal` 已为空，因此错误文字“含有数值”并未准确描述这次失败。实际最后 prompt 中的 `final_report_schema` 和独立 `output_schema.json` 都只把 `unit` 列为 `CNY/return_fraction/sessions/count/none`，没有描述或编码“非 numeric 只能 none”的联动规则；量字典又明确该 scalar 的单位为 CNY。独立结构检查确认原回答满足模型收到的 JSON schema，所有引用也确实属于本题。

因而需要区分：它违反了冻结控制器的提交语义，拒绝操作符合当前实现；但它没有违反已经完整展示的 JSON 结构，也没有因此算错金额。不宜把这个失败全部归因于模型不会诊断。原工程验收和确定性参考路径没有覆盖这类 schema 合法、隐藏语义拒绝的表示；本报告追加记录此公开契约缺口，保留原工程报告和原失败，不事后修正文档历史或重付费。

## 数值逐项核验

对照已接受 v9 `independent_accounting.json`、`independent_horizons.json`、`independent_identifiability.json`，另外仅从这位研究器已经拿到的 daily/trades/horizon_inputs 原始列用 Decimal 重建账务和 t+1/t+h+1 端点。没有调用被测诊断函数或执行器。

| 第几条 | 注册数值 | 报告值 | 独立结果 | 判断 |
|---|---|---:|---:|---|
| 1 | net_pnl_cny | 669.3 | 669.3 | 正确 |
| 2 | realized_net_pnl_cny | 669.3 | 669.3 | 正确 |
| 3 | reference_pnl_cny | 700 | 700 | 正确 |
| 4 | unrealized_change_cny | 0 | 0 | 正确 |
| 5 | closed_duration_sessions | 7 | 7 | 正确 |
| 7 | common_event_count | 2 | 2 | 正确 |
| 8 | horizon_1_mean | 0 | 0 | 正确 |
| 9 | horizon_5_mean | 0.05 | 0.05 | 正确 |
| 10 | horizon_10_mean | 0.095 | 0.095 | 正确 |

上述 9 项与保存 oracle 的差值均为 0。金额沿用 1e-6、收益分数 1e-12、计数/会话精确相等，不放宽容差。其余正文出现的数值和日期逐组核验如下，不能因为没有单列字段就忽略潜在错误：

| 正文中的检查 | 核验 |
|---|---|
| 初始 20000、期末 20669.3、15 日净损益和、初始资本收益 0.033465、8 个期末空仓日 | 全部正确，现金日保留。 |
| 买入成交额 10010 + 佣金 5 = 成本 10015；卖出成交额 10689.3 − 佣金 5 = 净收入 10684.3 | 正确，净实现 669.3；成交价已含滑点。 |
| 同量 100、参考买价 100、卖价 107，参考损益 700；费用 10、滑点 20.7 | 正确，700−10−20.7=669.3。费用和滑点正文正确，但没有独立 numeric 字段。 |
| 1 月 10 日未实现 485；1 月 13 日转回 485，当日净损益 184.3 | 正确；没有把全期未实现变化 0 误写成每日浮盈都为 0。 |
| 1 月 2 日买入 100，1 月 13 日全卖；会话索引 8−1=7；闭合段 1、右删失 0；复核间隔 3 | 全部正确。两个计数正文已写，但冻结 coverage inventory 中 `closed_spells_count/right_censored_count` 未作为 numeric finding 提交，应记录结构化遗漏。 |
| 15 日期×2 股票=30 股票日；28 个 filter_false；唯一 1 月 1 日两个候选及共同事件；候选排除 0 | 与原始输入及已返回 denominator 一致。三期限共用样本 hash 一致。 |
| 1 月 2 日入场，1/5/10 期限分别在 1 月 3/9/16 日退出；两股各期回报 (.01,−.01)、(.06,.04)、(.10,.09) | 原始 open 独立端点算术一致，没有把当日信号用于当日开盘。 |
| 首次目标 .5×20000/100=100；其后权重×(9985+100×open)/open 约为 100；1 月 13 日起为 0，第二股始终 0 | 与公开权重约定、全部目标行和原订单一致；保存浮点权重只造成无意义尾差。 |
| 1 月 9 日第一股市值 10600、成本 10015、未实现 585 | 正确，并由显式 open=日末 mark 合同支持。 |
| 1 月 2 日后过滤值为 0；两股事件 1/5/10 与单股实际 7 会话、部分资本投入不同；6 次查询 | 均与完整保存记录一致。 |

本题冻结评审重点的 7 个 numeric 标识中，5 个单列、2 个仅见正确正文；此外报告主动覆盖了 4 个会计 scalar。不能把“9/9 已提交数值正确”表述为“冻结清单全部结构化完成”。期末 sole-stock scalar 不适用，未提交数值是正确处理，不算把应答题目漏掉；未知值没有被当成 0。

## 证据使用、弃权与解释

实际查询依次为 `summary, horizon_curve, horizon_inputs, target_weights, trades, daily`，各 1 页，6 页都完整返回且 `next_cursor=null`。页哈希、外层 evidence_id 和全部 finding 引用均匹配。它没有请求 spells/horizon_events/session_calendar，但已有原订单、15 个日期和公开完整性元数据足够重建这份小样本的库存段和端点；不应机械按参考查询序列扣为错误。

报告没有停在复制 summary：写出了原始买卖额/费用算式、两只股票各期限的 open 比值、库存会话索引以及从目标权重反推单位的算式。独立复核确认这些算式成立。这能说明输出中有可检查的原始证据核验；不能据此推断内部思维过程，或统计为全新隐藏发现。

主要解释符合四项冻结维度：共同事件集合与时点一致；事件等权毛收益与单股部分资金净损益不是同一个统计量；三会话 review 不会自动下单，七会话库存段不是策略必然的调仓周期；两笔预声明订单不证明完整策略。报告还明确同日两事件不独立，长端均值更大不能得出最优期限、显著性或预测力。

可识别性判断正确：显式 mark 合同使本题逐股估值可重建，summary 的一般性空字段提示不能推翻其他公开证据。末日无库存使唯一股票 scalar 不适用。关于真实到达时间、拒单原因、策略生成机制和推广预测力的缺口也有根据；没有把缺少真实来源记录误作“所有历史合成估值都不可得”。不过它没有输出完整 15 日逐股向量，应将其视为正确范围说明及例证，而非全向量已经在 final 中展示。

## 下一检验

提出在新的未暴露样本中预注册同日匹配事件与非事件对照、固定主要期限、保留排除、按日期处理依赖，以区分信号预测信息与共同价格走势。这个下一步与参考解“先做对象/成本对齐”的措辞不同，但本报告已经做了可用的对象和成本核对，且明确唯一信号日两股都合格、后续非信号股票日不能替代同日对照。因此这是一项有事实动机、有支持/反驳观察、并且不过度承诺策略成功的有效检验。

所需多日期、同日对照、风险特征、PIT 可用时间和端点数据目前没有提供；可达性是后续资料要求，不是本题现有证据。必须先确认并冻结可取得的开发证据才可实施，不能把“未暴露”擅自解释成授权打开封存市场，更不能因为提了检验就称已证明 alpha。本次没有执行该检验。

## 保存证据与两问自检

审计产物：[calibration_01_audit.json](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v10_handoff/actual_reviews_artifacts_001/calibration_01_audit.json)；同目录 `calibration_01_source_snapshot.json` 是 case-specific 只读 SQL 的逻辑快照，含原完整回答、rejected action 和原查询页；`input_manifest.json` 记录来源字节哈希。审计脚本只写这个新目录，没有调用 submit/reconcile/gateway。

| 身份 | SHA256 |
|---|---|
| 原最终 response.json | 35a081b0ca4c6b95201514c81159b5bcec4811ce6cf8494b25c71da1c2dcb82e |
| 原最终 receipt.json | 43b91a834fd0771dc54ee66204e4594be689372f5b766e6bfdc99c81b24f65a1 |
| 原最终 prompt.txt | 363a94863d70cba329dd3c3a7959a8dee9c6d87efcafb69842ecc146432cad56 |
| 原最终 output_schema.json | 854855d0e3d09f109564cfe8b1f16b8efc35f139e8826d795967ee096bb9bb66 |
| 本次 numeric audit | d4b79ce341486e49c76108936961e7b9e2d6c35553921fc25b730a15c73a8ab2 |
| 本次逻辑 source snapshot | 607ed1397a34011e85c8bee8063d1b321c3689e7a99cad86f44b76c5e6be6567 |

两问自检：**有没有把有用报告洗成合法成功，或以私有参考措辞作为唯一答案？** 没有；保留 1 个失败终止及全部费用，单列接口缺陷、9 项数值正确、两项结构化遗漏和有效不同检验。**下一步是否会修输出、泄漏答案或重复付费？** 不会；后续仅在独立新版本补齐公开契约并离线验证，原 v10 回答、协议、状态和分母保持冻结，本审计不回灌研究器。

记录中的模型为请求 Astra/xhigh，`model_verified=false`，本地完成证据不独立认证供应商身份。此次评审新增研究网关调用、市场读取、回测均为 0；评审自身工程模型用量另计，不能称为零。
