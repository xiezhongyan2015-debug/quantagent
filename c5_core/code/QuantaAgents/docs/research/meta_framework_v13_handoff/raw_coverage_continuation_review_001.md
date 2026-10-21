# v13 原清单剩余部分续读：派发前独立复核 001

结论：对下列唯一已冻结 remaining 计划给予有界通过，未发现新增阻断。它只处理首轮正常时间准入停止后从未启动的 271 股，使用剩余字节预算；不重读首轮股票，不修改其结果。此结论不认证真实执行、PIT 股票池、公司行动、容量、价格来源真实性或收益。

复核时间：2026-09-07（Asia/Hong_Kong）。评审沿用 Astra/xhigh；新增研究 Gateway 调用 0，工程评审 token 计量未知。

## 冻结身份

所有路径相对项目根目录 `/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents`。

| 文件 | SHA256 |
| --- | --- |
| `experiment_traces/meta_development_raw_coverage_v13/attempts/20260906T184719050829Z/plan.json` | `b927d358b108ff3ee55ff0dc9f4d055762ed72d1edda7b63ee17f9a137240f79` |
| 同一父目录 `receipt.json` | `b592ceb884f7e245260cccfbe87f1d53071d8ad9954a1e15ffe44b2644d2922c` |
| 同一父目录 `continuation_prepared_claim_001.json` | `11c2b7581b52de35f193da5d8914e30f598547bd085cd45477cacbbd28cf6496` |
| `experiment_traces/meta_development_raw_coverage_v13/attempts/20260906T190344337689Z_remaining/plan.json` | `12877dedad07091e4e9dce8d67b221da2c85ba9d93ee9abb65909d124a23a6bc` |
| 同一 remaining 目录 `preparation_receipt.json` | `4b779b0bb03c5a18d5129e2aac21acd971e4023bb05c535cc34c01f6fdb878f5` |
| `experiment_traces/meta_ashare_revision13/scripts/prepare_raw_coverage_continuation_v13.py` | `e6ab4055a5de5ee1e42f22e2880b4b51853817786839672eb9b38a893fda0f73` |
| 同一脚本目录 `audit_development_raw_coverage_v13.py` | `7656d746d5e3006caca651c43160e8b740543f36094101ed458991a2b59baf55` |

## 实际只读检查

使用标准库逐项断言，exit 0，8.32 秒；没有导入或执行 reader，没有打开原 CSV，也没有解压行情行。已保存的压缩结果仅做字节 SHA 核对。

- 父回执为 `budget_admission_stop`，575 个 `code_receipts` 顺序与原计划前缀完全相同。575 个落盘回执与聚合记录逐对象相等，每个 intent 的 code、父 plan hash、首次尝试标记和源 stat 身份吻合。目录、intent、receipt 的股票集合完全相同，没有 pending、失败或未知的已启动股票。
- 575 个 `rows.json.gz` 和 575 个 `source_manifest.json` 的实际 SHA 全部匹配回执，也与 child 保存的 1,150 项 `preserved_artifacts` 完全相等。每个压缩文件的大小等于精确收费字节。首轮回执所称未处理 329,807 格仍为未检查范围，不能把“已启动股票无未知”写成全清单已经无未知。
- 原 846 个唯一股票按原顺序严格分成 575 + 271；remaining 恰等于父回执的未处理后缀，交集为空。原 1,217 个唯一会话日期、2017-01-03 至 2021-12-31 边界、remaining 的成员区间与事前源 stat 元数据均逐项相等。未以字段质量、收益或当前存续状态选择股票。
- 首轮完整保留 655,063 字段通过格与 44,712 拒绝格，共 699,775 格；加 remaining 329,807 格仍为 1,029,582。首轮成员分母 382,019，加 remaining 177,751，仍为 559,770；首轮拒绝没有从任何总分母删除。
- 父 exclusive continuation claim 精确绑定该 child 路径及父 plan/receipt hash，最多一个 continuation。检查当时 child 只有计划、准备回执和两份元数据，无 `run_claim.json`、无 `codes` 目录。原运行器仍要求外部 expected plan SHA，并用 exclusive run claim 阻止重复启动；准备器的 exclusive 父 claim 阻止重复创建这个后缀。
- 子计划其余范围、恢复政策、未来价格尾部读取限制、原 adapter 身份和全部否定性认证标记均保持不变；两份元数据字节 SHA 与父计划相同。源 stat 是原冻结元数据的相同子集；运行时仍由原 reader 在每股读取前后执行漂移检查。

## 预算与后续结果边界

| 项目 | 首轮已收费 | 唯一 remaining 可用 | 合计原上限 |
| --- | ---: | ---: | ---: |
| 原 CSV 前缀及日历读取字节 | 293,515,003 | 1,853,968,645 | 2,147,483,648 |
| 压缩结果字节 | 40,749,951 | 227,685,505 | 268,435,456 |

单股读取 32 MiB、压缩输出预留 4 MiB、每股最多一次均未放宽。额外 600 秒是明确新增的一次下一股准入时间额度，不是首轮自动重试，也不是对正在读取的单股实施硬中断。首轮保存的 601.016 秒正常停止记录保留。

本次通过只适用于上述外部 SHA 固定的 remaining 一次运行。运行失败、未知或再一次预算停止都应保留为结果，不能据本报告自动新建下一 continuation。最终聚合必须拼接两份原始结果并保留原完整格与成员格分母、首轮拒绝以及任何剩余未知；不能将字段检查通过率改称 execution_valid。

## 两问自检

1. 是否删掉异常、重置成本或偷偷换了股票/日期，使结果显得更完整？没有。原顺序、分母与拒绝全保留；只有未启动后缀进入剩余额度，字节预算精确承接首轮实际收费。
2. 是否把只读准备或字段检查当成已经执行合格，或产生额外行情/模型调用？没有。本轮只读脚本与保存的元数据、回执及产物哈希；没有启动续读、真实行情读取、模型调用或回测，所有执行/PIT/真实性边界继续保留。
