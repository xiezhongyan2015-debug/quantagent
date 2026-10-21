# v12 条件分组与同日对照：离线纯计算核 001

范围是已暴露合成开发数据的描述性工程工具。只新增 `meta/event_conditioning.py` 与专属测试，没有接入 `ashare_research`、旧 case、模型网关、运行器或付费校准。它不读取文件、网络或市场，不调用策略回测。工程 toy 不进入隐藏题、独立研究样本或策略成功分母。

两个接口：

```python
build_conditioning_plan(*, decision_rows, feature_manifest, specification, identity) -> plan
summarize_conditioned_horizons(*, plan, horizon_report, control_outcomes, identity) -> result
```

第一阶段没有 outcome 参数。第二阶段读取端点之前，使用保存的原决策字段重新计算整个确定性 plan，核对内容及哈希，拒绝自洽重写后的换股或规则偏移。输入顺序按 signal_date、symbol 规范化，`input_hash` 绑定规范输入；原文件字节来源仍应由 controller 单独绑定。

这不能证明真实的先后承诺：调用者仍可能在看过结果后选择另一份合法 specification 或伪造特征时点。因此两阶段均返回 `chronological_commitment_verified=false`、`controller_commitment_required=true`。后续可信 controller 必须先登记请求、排他持久化 plan/输入/规格身份，再提供 outcomes，并记录失败、缓存、重复请求和所有替代规格。当前模块没有权限隔离、不可回填时间戳或 OS 沙箱能力。

输入契约：

| 输入 | 固定规则 |
| --- | --- |
| identity | 精确字段为 run_id、architecture、observation_id、case_id、split、start、end、loaded_through、datahash、strategy_hash、source_hash、calendar_hash、data_kind、exposure。仅 development；end=loaded_through；仅 `synthetic_development_fixture` 与 `exposed_development_prototype`。各阶段完整相等。 |
| decision_rows | 每个股日一个坐标，包含显式 eligible/is_signal 布尔、decision_at、classification_available_at、features。同日所有行必须共享同一带时区决策时点；决策位于该合成会话收盘后、次会话入场前。分类可用时间不得晚于决策。重复坐标拒绝。 |
| feature_manifest | 每字段固定离散域，kind=discrete、role=pre_decision、availability_basis=synthetic_declared。每行每字段给 value/observed_at/available_at，必须 observed_at≤available_at≤decision_at；缺时间、无时区或晚到拒绝。值为空则保留排除。这里验证的是合成声明一致性，不认证历史 PIT。 |
| specification | 仅 group_features、match_features、pairing、horizons。固定 `[1,5,10]`，pairing=`same_day_exact_without_replacement`。无阈值扫描、全样本分位数、距离拟合或结果排序参数；额外字段拒绝。 |
| horizon_report | 已保存的 `common_horizon_diagnostics_v1` 报告，完整身份、内容/事件哈希、候选坐标、端点日期及结果状态核对。保留原 horizon 内核，不循环拼接独立期限样本。 |
| control_outcomes | `{identity,sessions,rows}`；calendar_hash=SHA256(canonical `{sessions: [...]}`)。行包含 event_id、signal_date、symbol、entry_date、entry_open、outcomes；每个固定 h 的 outcome 仅 exit_date/exit_open。允许价格或末端日期为 null；缺对照整行有独立排除原因，不补加载。 |

分组遍历事先声明的全部离散组合，包括空组；所有用到的特征值必须存在。分组标签来自信号事件。对照只从同日 eligible=true、is_signal=false 的股日中取得；按日期、信号 symbol 顺序，精确匹配声明的 match_features，选择字典序首个尚未使用的控制股。每个候选比较均保存选中、已使用、异 strata、缺字段等状态。不同日期可以再次出现同一股票，但不是同一控制事件的重复使用。

选定后不重新匹配。任一侧缺任一期端点，该原 pair 被共同 `[1,5,10]` 样本排除；其短期已知差值、所有失败原因、可用时点及原 pair 身份仍保存。事件分组与配对分组分别标记 observation_unit、各自共同集合及哈希，不能混用分母。两个集合都按同一固定三期限交集计算。

输出只有等事件/等配对均值、等日期均值、逐日期簇结果、原候选和排除、共同样本哈希、重叠与控制事件复用计数；空集合数值为 None。日期数不是已认证的独立 n。无 p 值、置信区间、夏普、可执行 PnL、盈利或因果效果判断。所有 group×两种观察单位×三个期限均登记子尝试，包括空组；参数规格数为 1，内部参数扫描为 0。第二阶段的确定性复核比较次数单列，不算新搜索机会。

限制为：2,000 决策行；单个原始输入包 2,000,000 UTF-8 字节；最多 4 特征、每特征 8 个离散值；每个分组/匹配列表最多 2 字段；16 个分组组合、1,000 个配对、10,000 个候选比较；单份 plan/report 上限 8,000,000 字节。超过限制拒绝，不截断。非法请求在纯计算阶段没有持久日志，需 controller 在调用前登记；不能凭函数内计数声称覆盖调用者所有历史搜索。

专属离线矩阵覆盖独立 Decimal 端点差值、共同集合、时点反例、同日异决策时点反例、缺 10 期端点不换候选、未匹配及空组、无信号、配对耗尽、跨日重叠、完整身份/日历/控制坐标篡改、缺整行、幂等和资源上限。原首题的条件仅复现为两个首日信号、后续 28 个非信号股日，其结论是 `no_same_day_control`；没有读取或修改原 campaign 输出。另一个明确标注的工程 toy 提供合法同日非信号对照以验证运算，不冒充首题增添的新证据。

验证：专属 27 项通过；独立审查新增的 5 项 conditioning 反例也通过（其同轮 9 项全部通过），包含两控制股交换后重新自洽封 hash、跨行可用时点、日期簇 2:1 数量下的 Decimal 均值及输入重排。最终联合验证由 root 保存独立凭证。以上是工程正确性证据，不是模型行为评分或策略验证。
