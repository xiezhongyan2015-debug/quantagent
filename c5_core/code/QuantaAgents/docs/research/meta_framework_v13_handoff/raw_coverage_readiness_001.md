# v13 开发原价覆盖盘点：读取前独立审查 001

2026-09-07，Astra/xhigh。**对下述精确替代计划的一次有界覆盖读取给出通过。** 两项实际边界问题已由 root 最小修复，9 项独立合成反例通过。此结论只允许字段质量盘点；不支持收益/策略回测、股票池 PIT、行情真实性、公司行动完整性、容量或 `execution_valid` 认证。

本独立代理没有运行真实 `prepare/run`，没有打开真实 CSV、Parquet 或行情载荷，没有新增 Gateway、网络或回测。只读取源码、已有报告、原/替代计划和复制的元数据身份；动态验证使用临时两股两日合成 CSV。工程评审模型用量未知另列，不能写成零；本子任务新增研究 Gateway 调用为 0。

## 唯一已审计划

- 计划：[20260906T184719050829Z/plan.json](../../../experiment_traces/meta_development_raw_coverage_v13/attempts/20260906T184719050829Z/plan.json)。SHA256：`b927d358b108ff3ee55ff0dc9f4d055762ed72d1edda7b63ee17f9a137240f79`。
- 脚本：[audit_development_raw_coverage_v13.py](../../../experiment_traces/meta_ashare_revision13/scripts/audit_development_raw_coverage_v13.py)。SHA256：`7656d746d5e3006caca651c43160e8b740543f36094101ed458991a2b59baf55`。
- 原价适配器 `raw_daily_data.py` SHA256：`c006814ef5577e8ccecda705c30f3389f29f64f9ea146c687ce122c99116f7e8`，字节不变。
- 原开发并集 manifest SHA256：`4a3c8dc02bda53ff845e5d94e4827e95aea5fa97070a5ac5894ff10e8f116617`。

旧计划 `20260906T184056231757Z/plan.json` 原 SHA `0f25fc5a2ac48009b52746328981fb8aea5466f4ba7904db9eb653f348f36fbe` 保持原样，已追加 `superseded_before_market_read_001.json`，未有 run claim。替代计划与旧计划逐字段比较，**仅 prepared_at、script_sha256 改变**；codes、days、membership_intervals、budget、source_stat_before、raw_root、原适配器及源元数据身份均相同。替代计划复核时也没有 run claim。

本次独立重新计算计划内元数据：846 个唯一历史主板代码、1,217 个唯一有序会话，2017-01-03 至 2021-12-31；完整笛卡尔分母 **1,029,582 股日**，历史成员分母 **559,770 股日**。代码列表与原 manifest 完全相等，成分区间按日期包含端点计算；没有按原价完整性、当今名单、收益或是否存在不利事件换股。

## 发现、修复及反例

**1. 原运行入口没有绑定外部冻结计划。** 原先只有脚本/adapter/复制元数据的哈希核验；删 codes、改截止或增预算后，run 仍可能读取被修改的计划。现签名 `run(root, expected_plan_sha256)`，CLI 的 `--expected-plan-sha256` 必填，先比较外部指定原 SHA，再加载计划和创建 run claim。三个独立负例分别改股票集合、截止至合成未来日期、扩大预算，均在任何 RawDailyData 构造和 claim 前拒绝。本次 root 必须传上列固定 SHA，不能现场从已变化的 plan 重新取 hash 充当事前值。

**2. 原压缩输出失败可能把未知写成零。** rows.gz 写入、flush、fsync 或随后 manifest 失败时，磁盘可已存在部分/完整 gzip，但旧聚合计数仍可能为 0。现写入开始后任何普通异常都收取该股完整 4 MiB 输出预留，`compressed_output_bytes=None`、`output_bytes_exact=false`，聚合使用 `compressed_artifact_byte_charge` 及 exact 标志。独立半份 gzip 写后抛错反例确认文件仍在、输出按预留记账、不把未知写零；读取记账回到 `charged_before + cap`，没有把已知读取再加一次。

两项均为可影响固定范围或失败账务的具体问题，不是外部真实性或操作系统认证要求。修前问题来自只读代码分析；没有冒称在真实行情上执行过失败。当前修复反例真实执行于合成临时目录。

## 预算、来源及封存读取

| 项目 | 实际检查到的行为与限制 |
|---|---|
| 读取总量 | 原价前缀和每次日历读取共用 cap；累计最多 2 GiB，单股最多 32 MiB。下一股 cap 为单股上限与剩余额度的较小者，少于 65,536 字节停止准入。普通异常按该次完整读取 cap 保守收费并停止，不重读。 |
| 时间 | 600 秒是下一股准入界限，不是正在读取一股的硬中断保证。最多一次遍历计划的 846 股；超过时间后的未处理股保留 unknown。 |
| 压缩输出 | 256 MiB 是压缩 rows 产物预算，每股先检查可容纳 4 MiB 预留，压缩后超过该股预留则拒绝。已写或未知写入按新 charge 字段记录。这不是所有 metadata 文件的总磁盘用量声明。 |
| 事前登记 | 每股先 exclusive intent，再构造适配器。全次 exclusive `run_claim.json` 拒绝重复运行。未完成 intent 表示未知；不能因没有 aggregate receipt 清掉预留、移除 claim 或另启同范围读取。 |
| 源漂移 | 准备时记录全体文件存在性、size、mtime_ns；每股读取前后核对；不一致则该尝试失败、全股日 unknown 并停止。成功产物记录真正读取前缀 SHA 和原始偏移。size/mtime 加前缀 hash 是局部来源证据，不认证未读字节或历史供应商真实性。 |
| 封存价格 | 适配器以 `buffering=0` 打开原 CSV，逐字节读身份/日期，遇第一个 end_date 后日期即停止，**不读取该行价格/量额 tail**。若实际下一条日期不是 2022 首日，则停在实际第一条超截止日期的身份前缀，不预先保证源文件齐全。没有全市场 CSV 的 read_bytes/全文件 hash。 |
| 前缀与日历 | 为顺序定位开发行，前缀读取可包含 2017 年前已存在行的原始字节，但不将其生成开发字段结果或认证 warmup；预算包含这些字节。日历未来日期只作为已复制 metadata，不作为行情。实际 2024–2025 价格 tail 不获授权。 |
| 异常后来源证据 | 成功及适配器返回的拒绝行有 source manifest/prefix SHA。适配器若在发布 manifest 前因预算或其他异常中断，脚本只能保留 intent、错误和完整预留，不能编造 exact bytes 或 prefix SHA。没有这些字段仍应记为未知，不能自动补读。 |

## 分母及质量判断

成功完成某股字段检查时，保存该股全部 1,217 个申请日期，包括不在当日指数成员内、源缺失、源开始较晚、重复/乱序、非法数值、单位或 OHLC 异常。每日同时保留 `historical_membership` 与 `accepted`，二者不是同一概念。源缺失可成为“字段检查完成且全拒绝”的结果，并不必须让整个脚本报异常。

普通操作失败时，保留失败股的整段未知分母和所有未处理股票；不将其归为已检查拒绝或成功。总表的 `accepted_full_grid_days + checked_rejected_full_grid_days + unknown_full_grid_days` 应等于原完整分母。成员分母保持 559,770，接受成员计数独立累计；不能用“已处理股”或“存在原价文件的股”作新分母。尚无 materialized rows 的失败/未处理部分由原 codes×days 计划和 receipt 的 unknown 明确表示，不能解释为它们已完成字段检查。

完整网格保留退池后的日期，但它本身不是持仓、权益、税债的义务清单，也没有补足 2017 年前预热或 2021 年后未结义务。`accepted` 只表示这份原价行满足既定字段检查，不能改名为可交易、历史 PIT 或真实执行合格。行动、状态、费用、容量和源真实性缺口继续保留，不因全格读取完成而置真。

## 独立专组检查

[test_development_raw_coverage_v13.py](../../../experiment_traces/meta_ashare_revision13/tests/test_development_raw_coverage_v13.py)，SHA256 `17dc5c41bb8f93f9d27a1b49a685012b69ca40bc97d3cc140d53eaf488954006`。

使用 v13 `src` 和项目 `.venv`，仅该模块实际执行：**9 passed in 3.12s**。覆盖全格与成员格分别计数、缺价不删除；独立计算截止前缀字节数及 SHA；禁止 whole CSV `read_bytes`；三种计划变更拒绝；已有 claim 拒绝再次构造 loader；源 stat 漂移；半份 gzip 写入异常；读取预算异常满额预留和禁止下一股；600 秒准入停止保留全部未知分母。没有运行真实盘点或大范围测试；root 此前报告的 15 项 adapter guards 不与此数相加冒充同次运行。

## 两问自检与执行条件

**这一步做得怎么样？** 已把可执行的两项漏洞反馈给作者并用合成反例验证修复，又核对替代计划只换工程身份、没有选股或分母变化。没有用目录存在、字段通过或本地 hash 替代执行与来源认证；未知输出费用、未读取日期和失败分母均有明确归属。

**下一步该做什么，如何改进？** root 持本文固定 plan SHA 仅运行一次，完整保留原计划、替代谱系、intent、产物与失败/停止回执。之后只能从实际 receipt 判断读到了哪些格、还欠哪些格和原因；如果预算停止或出现未知，停止就是结果，不自动续跑或换股。若源/计划/脚本身份变化，先停止，不能更新 hash 让原准入通过。本报告不授权回测、价格认证、公司行动补全或开启 `execution_valid`。
