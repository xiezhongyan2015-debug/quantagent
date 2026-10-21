# 两题公开原型的独立语义复评 002

2026-09-07，Asia/Hong_Kong；gpt-6-astra / xhigh 工程复评。

**结论：通过此次有界复评。** 新尝试 `20260906T164840172917Z` 已关闭 [001](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v8_handoff/prototype_semantic_review_001.md) 的两处语义缺陷。两题可作为**已暴露的开发诊断原型**供下一阶段最低限度行为校准使用；实际模型派发仍须另行完成预算、权限和恢复验收。不能据此宣称隐藏发现、真实策略达标、架构赢家或 OS 沙箱，全面目标仍未达标。

沿用 001 引用的既定评分标准、工程审查身份及作者重合披露，不重复其全部边界。本次只读保存记录及相关脚本，只新增本报告；未重跑测试、oracle、回测或模型。复评集中在以下四项变化。

| 变化 | 公开证据与复评判断 |
| --- | --- |
| 普通定义修复 | 题一 ordinary 明确“完整的固定合成执行记录；订单并非由完整策略回测生成”。三会话复核不自行下单；7 会话库存段来自两笔固定订单，不能解释为完整三日调仓策略。 |
| 可识别范围修复 | inventory 字典区分“本表未提供”与“所有公开资料不可推导”；新增显式 marking_contract。含混的 per_stock 标量已删除，terminal_single_stock_unrealized_cny 仅适用于期末恰有一只股票，要求指出日期/股票；零或多股期末不适用，不能填 0。 |
| 独立逐日核对补全 | 题一 15 日期、136 个金额/缺失比较；题二 7 日期、64 个比较。公开客户端的日期、股票集合、状态、数量、成本、市值和未实现水平，与另一个标准库实现保存的 oracle 均一致，沿用 CNY 1e-6 容差。 |
| 下一检验分题修复 | 题一对齐对象、时点、期限和原成交数量，再核对参考价格—费用—滑点桥接；题二先取得确切未知日期的同口径逐股标记。没有预设拒单、异常久持或新增回测。 |

**关键可识别性证据。** 题一公开规定同日 synthetic open 即日末 mark，全部 15 日期的逐股未实现可得，例如 asset_01 在 01-02 为 -15、01-09 为 585；零库存为 0。期末无库存，所以“期末唯一股票”标量不适用，客户端未伪报该标量为 0。题二仅 2019-12-30、12-31 同时持有 A/B 且缺少足够逐股标记，分解未知但聚合损益可核对；2020-01-07 仅 A 剩余 30 单位，`264 - 273.45 = -9.45` 已作为明确数值项提交。当前未实现水平可得不代表前日未知分配或所有相邻日单股净 PnL 可得；独立 oracle 保留了此边界。

[可识别性 oracle](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision9/scripts/calibration_identifiability_oracle.py:89) 从原始 daily/trades/calendar 和公开标记合同独立重建，不导入参考客户端或诊断内核。控制器在公开客户端唯一 final 保存后才进入 oracle 路径。本次逐项对照的是双方已存结果，没有重新执行计算程序。既有会计与期限数值检查仍通过。

**下一检验的区分力成立。** 题一使用已公开端点、固定订单、库存边界及费用核查口径与成本解释；对齐后仍有残差就核对映射和会计假设，不推出最佳期限或 alpha。题二明确请求 12-30/12-31 的逐股标记、时间与口径，核对分解总和及成本桥接；不一致就保留口径问题。尚未查询的目标与缺失的意图/接受/拒绝记录只作为另行研究数量路径时的资料需求；报告明确陈旧 mark 影响金额判断，不能解释股数持续。相符标记支持该口径下的分解，不构成真实价格或成交认证。

**记录核验。** [新回执](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_calibration_prototypes/attempts/20260906T164840172917Z/receipt.json) 保存 2 planned / 2 completed、无错误，公共查询为 8/5 次、各 1 final。只读 SQLite 与公开轨迹、最终 body/receipt 一致；公共包、13 页 page/evidence 哈希与 scope、来源 ZIP/报告字节哈希、提交哈希及全部 finding 引用均匹配。对应 canary 与私有目录名未出现在公共 initial/final context 或 reference_result；原回执另记录私有反馈变化未改变公共 context。这仍只支持执行过的受控接口路径，不新增 OS 隔离证明。后续研究器不得获得参考解、availability 审计、oracle、评分表或本报告。

001 固定的旧 plan、receipt、两份 reference_result 四个哈希均未变；新旧两题的 ZIP、原诊断报告、observation、calendar，以及题一 horizon_inputs 逐字节相同。v9 src 清单仍为 `ead65d49dcf554558d939d5af48fb59a3f672ba9787f6d7af229e9cd9f7fff4a`；修复在新 scripts 和普通包/参考输出，五个 calibration 脚本与新 plan 哈希相符，没有通过删订单或改旧数据获得通过。

[v9 定向验证回执](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision9/validation_attempts/20260906T164810966591Z/receipt.json) 保存 **233 tests、0 failures/errors/skipped**；JUnit 的哈希及数量相符。本次仅读取记录。工程复评自身使用 Astra/xhigh，工程用量由 root/平台另列，不能称人工无模型审查；新增研究网关调用、市场读取、回测与独立真实样本仍为 0，未进行研究模型行为评分。

两问自检：已用公开证据关闭原两处缺陷，未放宽容差、改旧记录或回灌答案提示；下一步应验收独立 live harness 的预算、身份、工具限制和崩溃恢复，再按冻结规则检查真实模型行为。保留 001 原结论与旧回执，以本报告登记新尝试的语义通过。

读取身份 SHA256：

| 文件 | SHA256 |
| --- | --- |
| 新 attempt / plan.json | `dec9d2f7ff8c3fe55b8e579248f2ff191e4c1ba79fc5b66cecbc60981c28eba1` |
| 新 attempt / receipt.json | `f1f8460434b15226c3816832d1f5b9e5babb7be691049dca67c04ee135aaee98` |
| calibration_01 / reference_result.json | `fc763cbf84781697433d89827d2f87a545112e9330b4abab3fa8c057cee1199c` |
| calibration_02 / reference_result.json | `d93b045cea17d7726dd792284dca431e1962d6809956380ddd8530677db47fe2` |
| v9 / calibration_public_reference.py | `a1a5186499337827ffa5e6975ab457603198be2792a6e0909e20fec1c34d6caa` |
| v9 / calibration_identifiability_oracle.py | `08101c0b7726857918078c54727a3d644001be67a383a9b3a5656445370d457d` |
| v9 / run_diagnostic_calibration_prototypes.py | `cb69efdb549e9c7572f6f00db92826818eb919a8d864ab9df8109b83f4e5eca9` |
| v9 / validation receipt | `52b3bda8f8081d6da79742b3ba188a63544cc5f06748d4df95f0b93748250e2a` |