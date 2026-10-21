# 下一版 A 股元框架：冻结架构、跨题重复与有界研究预算

日期：2026-09-06。状态更新：下述第二版方案已经完成主要实现并于 01:23 启动 8 项冻结开发批次；原文保留作为事前设计记录。当前批次源码继续冻结。第三版在隔离副本补充数据身份前置门、文件占用恢复和原始股份账本，尚未替换运行服务。现状与证据见 [迭代日志](META_ITERATION_JOURNAL.md)、[第三版批次审查](META_REVISION3_BATCH_REVIEW.md) 和 [L2 样本审计](META_L2_CONTRACT_SAMPLE_AUDIT.md)。

本轮已证明真实行情、因子表达式、诊断、组合回测、逐轮自检和恢复可以连接起来。调度方在编写任务时报告：基线完成 6 轮，开发期 Sharpe 从约 -1.13 改善至约 -0.107，仍未盈利，候选尚在运行。这是工具与研究过程的证据，不能据此判定架构改进，更不能把目标改成“把同一道题调到高分”。

下一版的具体目标是：**把本轮得到的候选研究方法冻结，和冻结基线分别在新普通题目上独立启动多次，完整计算失败率、达到净 Sharpe > 1 的比例、收益分布与成本。** 首个小批仍是开发实验，不是正式泛化验收；采用同一 Astra/xhigh，默认每次最多 12 次研究调用，18 次仅作为另一个预先冻结的预算档。

## 1. 先修复已确认的显示错误，保留原始回测

当前 GUI 存在单位映射错误，不是资金或回测计算错误：

- `src/quanta_agents/meta/web/index.html::evidenceSummary()` 把 `metrics.max_drawdown` 一律乘 100 格式化为百分比。真实 A 股该字段是金额；例如 `-528547` 元会错误显示为约 `-52854700%`。
- `renderComparison()` 把 `max_drawdown` 排在 `max_ddpercent` 前，两个字段使用同一显示标签，并按标签去重。因此金额字段遮住了正确的比例字段。
- 真实结果还使用 `sharpe_ratio`、`total_trade_count`、`completed_exit_orders`；当前展示表缺少这些别名。评分可以间接显示 Sharpe，但交易信息容易缺失。
- `ashare_case.py::_year_metrics()` 的 `yearly[].max_drawdown` 又是比例。相同字段名在不同对象中有不同单位，不能靠字符串包含 `drawdown` 推断。

等当前运行结束后，最小修复是让摘要与比较表共用一份**显式、按结果类型和层级选择的指标映射**：

| 结果对象 | 字段 | 单位与显示 |
|---|---|---|
| 真实 A 股 `metrics` | `sharpe_ratio` | 无量纲，夏普 |
| 真实 A 股 `metrics` | `max_ddpercent` | 比例，乘 100 后显示 % |
| 真实 A 股 `metrics` | `max_drawdown` | CNY，显示“最大回撤金额”，不得乘 100 |
| 真实 A 股 `metrics` | `total_trade_count` | 成交订单数；不标成独立交易样本 |
| 真实 A 股 `metrics` | `completed_exit_orders` | 已实现卖出订单数；可能包含部分减仓 |
| 真实 A 股 `yearly[]` | `max_drawdown` | 当年净值路径的回撤比例 |
| 旧合成 `metrics` | `max_drawdown` | 比例，兼容原定义 |

建议下一版 evaluator 额外返回 `metric_schema_version` 和明确的 `metric_units`，GUI 先支持当前两种版本。缺字段显示“—”，未知版本不猜单位。资金数值、原始 JSON 和历史产物无需修改。

有价值的回归：同一个真实结果同时包含 `max_drawdown=-528547`、`max_ddpercent=-0.497585`，研究摘要与比较表都必须显示 `-49.76%`；金额如展示，应为人民币金额。再覆盖旧合成比例、真实年度比例、缺字段、NaN/null 和真实交易数名称。之前仅使用比例型 `max_drawdown` 的 DOM 夹具没有覆盖跨适配器单位差异，应使用真实 schema 的最小脱敏样本。

实施补记：已在 `experiment_traces/meta_ashare_revision2/src/quanta_agents/meta/web/index.html` 隔离副本实现上述共用映射，补齐真实字段别名；原 `src` 未改。新增副本测试 `tests/test_meta_gui_metrics.js`，执行 `node --test tests/test_meta_gui_metrics.js`，5 项通过，覆盖真实摘要/比较、旧合成、年度单位和缺值。仍须在当前运行结束后由主任务统一合入、重启并验证实际页面。

## 2. 把架构提案与架构比赛分开

当前 `ashare_research.py::run_step()` 在 `meta_proposal` 中读取本题基线轨迹，临时产生指导，再让候选研究同一道题。这个路径应保留为 `architecture_development`，但新增的 `frozen_evaluation` 不能执行这次元提案调用。

新增 `meta/architecture_registry.py`，只负责受控 JSON 校验、冻结与按哈希加载：

```text
ArchitectureSpec
  schema_version
  architecture_id, parent_id
  model = gpt-6-astra
  reasoning_effort = xhigh
  change_scope = research_guidance
  research_instructions
  hypothesis, expected_effect, regression_risk
  source_run_ids, source_case_ids, source_trace_hashes
  development_exposures
  semantic_hash, artifact_sha256
```

`semantic_hash` 只覆盖实际会影响执行的指导、模型锁、工具/上下文/停止策略引用；创建时间、说明文字单独进入 `artifact_sha256`。当前提案在 meta 节点用整个 response 算架构哈希，研究结果又用 instructions 算架构哈希，两个名称相同但对象不同；新版本应明确区分并用一个规范身份贯穿。

从本轮的 `candidate_architecture.json` 导入一次候选，记录其来源及已经查看的题目/时期。可在开发环节审查它是否夹带本题公式、股票或日期，再冻结为一个通用版本；审查本身也记录。禁止在每个新题里重生成指导，禁止在看到新题成绩后覆盖同一个架构 ID。提示词要求“通用”或简单关键词扫描都不足以证明没有内容泄漏，来源登记和真正的新题才是必要约束。

冻结基线需要同样完整的 `ArchitectureSpec`。基线与候选共享同一研究工具、动作 schema、上下文裁剪算法、交易规则和预算，只改变本批声明的一项研究方法。基线允许自然地产生竞争解释和诊断，不为了制造优势而禁止它本来具有的能力。

## 3. 题目注册只改变题目，不复制回测器

新增 `meta/case_catalog.py`，提供版本化 `CaseSpec`。继续复用 `AShareCase` 的读数、可投资状态、因子解释、权重和回测逻辑，不为每个题目复制文件。

```text
CaseSpec
  case_id, version, mechanism_family, task_type
  task_definition
  starting_strategy
  data_config_id, data_snapshot_id
  development, confirmation, final
  execution_spec_hash, evaluator_version, tool_schema_hash
  used_for_architecture_design
  exposure_group_id
```

当前 `ashare_case.py` 中 `CASE_ID`、`manifest()['task']`、`baseline_strategy()`、`development_packet()` 和部分返回值都固定为下跌修复。最小改动：将受信 `case_spec` 注入实例，增加 `starting_strategy()` 方法，结果统一取实例 `case_id`；旧全局 `baseline_strategy()` 保留为旧案例兼容入口。`ashare_research.py::prepare` 与离线夹具改为调用实例起点。题目声明不能覆盖固定执行规则、评分、预算或任意磁盘路径；未知配置项应拒绝，避免改了名字却仍运行旧题。

首个开发小批建议选择两个未参与本轮指导生成的普通题：

1. 价格延续：观察上涨后哪些情形会延续，哪些已经过度扩张。
2. 放量突破：观察区间突破后的延续与失败，允许模型否定量能的增量价值。

只给普通定义及未经收益搜索选出的朴素起点。两题使用同一可审计日线股票池、可用字段和执行模型，有助于先检查研究方法迁移。它们的股票和日期相关，不能因此说获得两份独立市场证据。后续再扩展至少第三机制家族，以及独立的 L2 可用时点/库存/执行适配批次。

同题改写提示词属于提示扰动，不是新机制家族；同题改 `case_id`、换运行目录，也不能把已经查看的时期变成盲测。L2 日内事件平均收益不能直接与当前日线组合 Sharpe 混合排名。

## 4. 每个“题目 × 重复 × 架构”是独立运行

新增 `meta/batch_runtime.py`，负责有限队列与调度，不再发模型请求。建议第一版串行，继续使用现有 `Engine` 的单 worker 和 `.worker.lock`，避免先引入并发数据争用与恢复问题。

```text
BatchSpec
  batch_id, purpose = development_screen | frozen_acceptance
  frozen_architectures = [baseline_hash, candidate_hash]
  frozen_cases = [case_version_hash, ...]
  repeats_per_case
  research_protocol_hash, budget_tier
  schedule_seed, frozen_schedule
  planned_members
  batch_call_cap, batch_token_cap, batch_wall_cap
  analysis_plan, stopping_policy
```

成员键为 `(batch_id, case_id, repetition_id, architecture_hash)`。所有计划成员在第一条模型请求前登记，创建 `run_id` 后与成员键绑定。调度 seed 只用于固定顺序，不宣称它控制模型随机性。按题目和重复编号配对，以 AB/BA 交错顺序降低时段偏差；不要把所有基线跑完再统一运行候选。

现有一条 run 串行包含两个架构，基线失败可能令候选根本没有开始。为避免失败耦合，给 `Engine.create()` 增加窄范围 `frozen_arm` 模式：一个 run 只执行一个指定架构，仍保留原有 `research[baseline/candidate]` 容器和阶段名。图中非参与方阶段及 `meta_proposal` 直接返回明确的 skipped 元数据；研究指导来自冻结的架构文件；冻结提交与评测只处理参与方。这比复制一套循环或构造任意动态图改动更小，旧联调默认保持双架构路径。

`finish` 对单方输出 `arm_result`；批次汇总器再配对，不能在单方 finish 伪造另一方分数。一方失败后另一方按原定资源独立运行，双方失败都保留。暂停批次表示不再派发下一成员；停止在途调用须明确转交现有控制器，GUI 分开显示“停止排队”和“中断当前运行”。

初始小批：**2 题 × 2 次独立启动 × 2 架构 = 8 个研究运行，每个最多 12 次调用，共最多 96 次研究调用，另计事先冻结的技术重试。** 元提案已来自上一批，此批内不再调用元研究员。这个规模用于观察工具是否够用、方法是否迁移、成本是否失控和失败是否集中，仍不提供统计稳定性的结论。正式数量与判定沿用并进一步冻结 `META_ASHARE_PROTOCOL_DRAFT.md` 的政策，而不是看到结果后临时加样本。

## 5. 6 次调用的上限与 12/18 次档位

当前轮数分布硬编码为前 3 轮和后 3 轮；第 6 次只能提交，一个动作要到下一次调用才获得解释反馈。因此最多 5 次探索动作，最后一次的程序结果没有下一轮专门复盘。一次语法修正也消耗一次机会；一个“解释—诊断—对照—回测”的研究问题就可能用掉数轮。6 次适合工程联调，很难由其失败证明 Astra 缺乏创造力。

下一版增加独立于事故上限的 `ResearchProtocol`：

```text
max_research_calls = 6 | 12 | 18
initial_research_calls = 3
min_calls_before_submit = 4
final_call_action = submit
context_policy = frozen_structured_history_v1
stop_on_valid_submit = true
technical_retry_policy = explicit_bounded
```

保留原图两个研究阶段：`initial` 处理 `[0, initial_research_calls)`，`refine` 处理余下调用；把 `index==5`、`max_calls=6`、`5-index`、`(0,3)/(3,6)` 等常量统一从协议派生。最后一次的提交可以沿用 agent 自己选择的已验证策略；控制器不能在失败后自动挑历史最高分补交。

建议下一开发小批选 **12 次**，两方完全相同；18 次用于后续另一个已冻结的能力/成本试验。不要只给输的一方续跑，不要把 6 次基线与 18 次候选称作架构提升。若想区分方法效果和更多思考的效果，把预算作为明确的第二试验因素，分别报告相同预算下的架构差异。

不宜直接按 6→12/18 倍数推断 token：当前 `_trace_for_model()` 会把全部历史策略和观察重复送回，累计输入可能随轮数明显增长。先根据本轮实际调用账本估计后段上下文与模型耗时，在批次启动前冻结两方相同的 token 和活动时长上限；将每次输出、错误修复和未知消耗预留纳入。总批次事故预算独立于每个研究运行，避免派发器把单次上限重复乘成意外消耗。

上下文第一版保持可复核的确定性裁剪：共同起点、当前选择的策略、最近若干完整观察，以及所有历史尝试的紧凑索引（含失败、策略哈希、证据哈希与来源）。不能只留下高分结果。若需要更完整旧证据，提供只读 artifact 检索动作，访问范围仅限本次本方开发轨迹。上下文裁剪与检索策略属于架构共同底座，在同一批内不能随意变化；新增模型摘要角色也必须 Astra/xhigh，并算入同一总调用机会与成本。

停止标准在开跑前固定：合法主动提交、用尽研究机会、越权、明确的不可恢复数据/工具错误、事故预算或人工停止。早停可以由模型在相同协议下决定；不要因开发 Sharpe 首次超过 1 就强制宣告完成，也不要因为没到 1 自动无限延长。终止原因和当时最后一个合法策略照实记录；无合法冻结提交就是失败。

## 6. 补足“分组 × 年度”诊断

目前 `AShareCase.evaluate()` 已有 `yearly` 组合指标；`diagnose()` 已有全开发期分组结果，以及不区分组的年度均值。缺的是**同一分组在不同年度是否保持方向、样本是否被某一年主导**，无需重写回测器。

在 `ashare_case.py::diagnose()` 内复用已生成的 observations，增加：

```text
group_definition: method, boundaries, fit_period, descriptive_only
groups_by_year: year, group, observations, distinct_signal_dates,
               feature_min, feature_max,
               mean_gross_forward_return, median_gross_forward_return,
               positive_fraction
coverage_by_year: all_signals, feature_valid, full_horizon_valid, excluded
```

第一版沿用现有全开发期边界，清楚标识这是描述性分组，不声称其阈值是当年事先已知。若要检验可迁移阈值，另提供明确动作：仅在指定早期开发子段拟合边界，再固定应用于之后开发年份；边界、拟合时段和无有效分组情况全部返回。不能每年重新分位数后把“第五组”误解释为相同绝对条件。

年度分组的未来收益必须完整落在开发期；报告按信号年份归属和跨年持有的定义。保留缺失与未完成 horizon 的覆盖统计，不把尾部被排除的样本算成亏损删除。重叠事件相关，先提供日期等权摘要、样本覆盖和年度方向；不要自动用逐股票事件数当作独立样本计算显著性。

分组收益仍是毛事件诊断。最终 Sharpe 只用固定资金、真实规则近似与全部现金日组成的净组合路径。对候选的“这个组更好”应能进一步请求对应策略的完整回测与条件消融。

## 7. 共享状态、数据泄漏与恢复的具体风险

| 当前入口或结构 | 扩展时容易发生的问题 | 最小控制 |
|---|---|---|
| `Engine.case_instances[run_id]` | 为省内存把整个 `AShareCase` 在架构之间共享，使 `_loaded['confirmation']`、结果缓存、计数和可变 pandas 对象串入另一运行 | 每运行持有独立研究状态；只共享按内容哈希索引、只读的开发行情缓存。共享 evaluator 的结果必须深拷贝；确认数据不进入研究服务对象 |
| `AShareCase._evaluations[(split,strategy_hash)]` | 推广成全局缓存后，不同数据、股票池、费用或 evaluator 版本误命中 | 全局 key 包含数据内容哈希、CaseSpec、执行/评分版本、split、策略哈希；单实例现有 key 只在严格实例隔离下有效 |
| `_trace_for_model(records)` | 把其他架构、重复试验、已查看确认结果或人工总结混入共享记忆 | 本次本方开发白名单投影；其他方轨迹仅在独立元开发任务中可读；冻结比赛无跨运行策略记忆 |
| `expose_evaluation(case_key,split,...)` | 当前只有本 ledger 的登记，改目录或 case 名称会遗漏过去查看；同一市场窗口跨题共享也可能漏记 | 批次共享稳定曝光登记；区分 case 身份与底层数据窗口的 exposure_group。记录日期范围、数据版本、策略提交哈希、谁何时看过；新目录不能声称全局未见 |
| `AShareCase._file_identity()` | size/mtime 快速指纹不是内容不变的充分证据；已缓存数据不会持续重新读盘 | 冻结已投影数据内容哈希并用于恢复与缓存身份；保留文件元信息快检，不把它称作原始数据完整 SHA256 |
| `Engine._model()` receipt 复用 | 当前主要匹配 step 与 prompt_hash；扩展后相同提示文字但工具/schema/模型策略不同可能误复用 | 冻结 `request_fingerprint = prompt + schema + architecture + case/data + tools + model/effort + protocol`；先验证所有身份再复用 |
| 多次 repeat 的相同 prompt | 误把前一次付费结果缓存为“新的独立启动” | 付费 receipt 只在同一成员/同一 attempt 恢复时复用；新 repetition 不复用另一运行结果。可信纯回测可内容缓存，必须另记 cached 状态与用时 |
| `research[prefix].append(record)` 与后续 artifact | 保存观察后崩溃可能重复付费、重复 append 或缺少压缩账本 | 保留现有 receipt→round→artifact 修复路径；新增成员唯一键和 round 唯一键，恢复时重建缺产物，已完成动作不重复扣机会 |
| 单 run 全局预算与顺序 | 基线先耗尽全局预算，候选没有同等机会；提案费混入一方预算 | 独立 arm 预算；公共元开发成本单独报告；批次启动前预留全部计划上限或按冻结停止规则停止新派发 |
| GUI 人工补充 | 只给受挫架构补提示，或查看留出后继续指点，结果还进正式成功率 | 持续保留 comparable=false，批次成员标记 tainted；主有效成功分母保留但不记成功，另报敏感性结果；不能删除或换名补跑 |
| 模型身份与工具 | 后续增加评审/摘要角色绕过 Gateway，经旧 V2 LLM 默认模型调用 | 所有角色强制同一个 Gateway，禁止 fallback；保存请求及提供方确认状态；缺少确认就如实标记，不能用模型自述认证 |

权限边界仍由控制器中介的 JSON 动作构成。为更多研究轮次开放宿主终端、全仓库读取或任意 Python 并非必要步骤。数据与评分不可写的边界应继续保持。

## 8. 失败计入分母，两个问题分别评估

新增 `meta/batch_evaluation.py`，只读冻结计划、成员状态、提交与评测证据，不调用模型，不修改策略。

先报告**系统是否达标**：在计划的任务×重复运行中，有多少产出合法、执行约束通过、覆盖足够、净 Sharpe 严格大于 1 且符合预注册风险要求的策略。再报告**候选是否优于基线**：同题同重复配对后的有效成功差与完整成绩分布。旧基线同样成功时，候选可能达标但没有改进；比负 Sharpe 基线更好也可能仍未达标。

主要计数来自冻结的 `planned_members`，不能从成功目录数量倒推分母。每个成员最终只有一个状态：success、valid_below_target、invalid_submission、no_trade/insufficient_coverage、budget_stop、tool_or_data_failure、provider_failure、cancelled、tainted 等。未启动的原定成员报告为 pending/not_started；批次若提前取消就明确 incomplete，不能把已完成部分冒充原定完整验收。失败不虚构无限负 Sharpe；“有效成功率（含失败）”和“有效策略 Sharpe 分布”分别报告。

首个小批不计算夸大的显著性结论。正式批次按预注册题目权重聚合，保留同题重复和相同市场日期的相关性，使用适合该分组结构的区间估计。不能把相关策略订单当作独立架构样本；不能每题挑较强架构再拼成一个声称可部署的系统；不能查看留出分数后继续追加到显著。

可固定输出：计划/完成/失败数，成功率，按题目和机制家族的配对表，有效 Sharpe 的中位数与低分位，年收益与回撤分布，最差年度，token/活动时长/实际调用数，成本与成功率关系，污染/曝光及未解决执行限制。数字应可以由已保存 daily/trades 证据独立复算。

## 9. 可分工的实施范围与测试

| 工作包 | 独占文件范围 | 验收与必要测试 |
|---|---|---|
| A：指标单位 | `meta/web/index.html`；新增 GUI 契约测试 | 真实金额和比例同时出现、synthetic/年度兼容、真实交易字段、缺值与文本注入；不重跑模型 |
| B：冻结定义 | 新 `meta/architecture_registry.py`、`meta/case_catalog.py`；注册定义文件 | 同 ID 不可覆盖；来源明确；两种哈希用途明确；未知字段拒绝；多题不会仍报告旧 case_id 或旧起点 |
| C：研究协议 | `meta/ashare_research.py`；小范围 `runtime.py` 接口，由同一人协调 | 6/12/18 最后一轮必须 submit；提前提交合法；失败占机会；frozen 模式不调用 meta；双方只收到自己轨迹；所有角色 Astra/xhigh |
| D：案例诊断 | `meta/ashare_case.py`；案例与诊断测试 | 分组×年度逐项手算；固定边界与年度重算边界能区分；尾部标签不跨区间；未来数据扰动不影响早期特征；全局缓存身份正确 |
| E：批次状态 | 新 `meta/batch_runtime.py`；`meta/store.py` 新表；`runtime.py` 接口由 C 负责人整合 | 两次派发竞争只有一个成员 run；进程崩溃后队列恢复；已付费 receipt 不重付；一个 arm 失败不阻止另一 arm；暂停不派发下一成员；批次事故上限生效 |
| F：统计与批次 GUI | 新 `meta/batch_evaluation.py`；GUI 在 A 完成后接手 | 全失败批次分母不为 0；未启动不被算作完整验收；同题多次启动不得取最大值；人工干预计数；不完整批次明确显示；配对顺序与题目权重稳定 |

恢复与隔离的高价值故障注入测试必须包含：

1. 模型 response 已落盘、观察未保存时崩溃：恢复只执行开发动作，不新调用模型。
2. 观察已经保存、压缩资金账本缺失时崩溃：修复产物并校验重算 hash，不重复增加 round。
3. 先完成基线、候选尚未开始时重启：恢复成员队列，不重跑基线，不重新提出架构。
4. 新 repetition 的提示与旧 repetition 完全相同：依然必须产生新的模型调用；同一 repetition 的恢复必须复用。
5. 确认期结果放置唯一哨兵字符串：任何研究、元研究冻结赛、上下文摘要和 artifact 检索响应中都不得出现。
6. 改变 execution、schema、数据内容或冻结指导任一哈希：旧 receipt/缓存不可继续作为同一次协议结果。
7. 同一道题两个架构用完全相同输出：开发/确认分数一致，失败和 token 计账规则一致；缓存只改变计算耗时，不改变调用机会。
8. 一方最后一次提交无效：不能自动挑早期高分版本替它交卷，另一方正常完成，失败进入计划分母。

## 10. 实施顺序与每轮自检

当前 run 结束并导出结果后，先做 A 与 B；C 与 D 可并行，C/E 对 `runtime.py` 的修改由一个负责人串行整合。离线模式和故障注入通过后，冻结本轮源码与新协议，再启动上述 8 成员、12 次档位的开发小批。各成员完整落盘后才能派发下一成员；GUI 只观察运行，不作为唯一状态。

每次改动都保存同样的两问：

- **这一步做得怎么样？** 指向具体测试、失败修复、可重算证据及仍未解决的限制，不用程序尚未返回的结果给自己打分。
- **下一步该做什么，如何改进？** 明确下一条可证伪假设或工程缺口，以及继续/停止的条件。工具不充分就补共同底座；候选方法无效就放弃该方法；策略无效也允许结束该题。

更长的研究预算只解决探索空间和反馈次数不足。是否能稳定产生净 Sharpe > 1 的 A 股策略，仍取决于数据、执行模型、可研究的真实机制与跨题重复证据。本次扩展的价值是让这些因素可以被分别检查，而不是让循环一直跑到碰巧出现一个高分。
