# P1 两道诊断校准题：独立评分与答案隔离审查

日期：2026-09-06 至 2026-09-07，Asia/Hong_Kong。审查配置为 gpt-6-astra / xhigh。

**判断：可以按两道“证据核对与诊断口径”题建设最小闭环；现在尚不能宣布两题可自主解答或评分已通过。** 题二优先，题一随后。首轮不加真实策略收益评分、不加匹配对照、不扩大到隐藏机制发现题库，不比较架构赢家。

本轮只读 `p1_tool_readiness.md` 和当前 revision7 源码、相关协议，唯一新增文件为本报告。没有调用外部研究模型、运行测试或回测，没有读取 2024–2025 市场记录。下述 schema、oracle、参考解和隔离测试均为可冻结的实施建议，**不是已经实现或通过的验收结果**。工程审查自身使用模型，不能因新增研究网关调用为零而声称本轮全部模型用量为零。

## 1. 与现有实现对照后的三项必要补充

1. **题二必须可取得原订单及顺序。** v7 `execution_diagnostics.py:396` 的 tables 是派生 daily/inventory/spells，没有原 trades。仅开放 readiness 建议的 summary/daily/spells/inventory，不能证明研究器独立复核了平均成本和每笔已实现损益。最小只读接口应增加来源 ZIP 中的原 `trades`，带固定 `source_order_ordinal`；不得只给派生计算结果，再用私有原订单证明参考解可达。
2. **删除现金日能否检出取决于独立日历。** v7 `execution_diagnostics.py:348–351` 明确只保证所有归档行被保留，未独立认证完整市场日历。普通包必须附该合成题的完整 `session_calendar`。缺少它时，研究器不能区分漏行和休市，不能因此扣诊断分。首轮只需冻结小型合成日期清单，不需要新接真实交易所日历。
3. **当前摘要已给出若干结论，评分不能称为隐藏发现。** v7 summary 本身含会计桥接、费用不应重复扣除、持仓右删失以及 UNAVAILABLE 解释。保留正常工具说明是合理的，但“引用摘要”和“从合法原始证据完成复核”应分项记录。首轮通过只证明能正确使用和核对证据，不证明发现了隐藏机制或创造交易优势。

题一另有已确认缺口：`ashare_case.py:496–559` 每个 horizon 独立构造 mask、pooled quintile 边界；`_weights` 返回的 eligible 股日也不是调仓 top_n 或实际入场集合。相同 observations 数量不能证明共同样本，组合调仓间隔不能替代连续库存段时长。

## 2. 两题的最小冻结范围

采用中性公开题号 `calibration_01`、`calibration_02`，题干沿用 readiness 的普通输入。既有七日会计原型已被元设计者阅读，只能登记为 `exposed_development_prototype`；不能换目录或题号后称为新盲测。后续隐藏变体另冻版本，不能用同一已反馈实例反复挑战。

| 项目 | calibration_01 | calibration_02 |
| --- | --- | --- |
| 核心问题 | 事件期限观察能否解释已执行组合的持仓证据 | 保存的现金、订单和库存证据能解释哪些损益 |
| 数据 | 小型合成证券与冻结 session_calendar；合法可见的历史特征/资格；固定起点策略和已保存观察 | 小型合成完整 daily、trades、target_weights、初始现金和 session_calendar；初始无持仓、无外部现金流和公司行动 |
| 最小扩展 | 一次请求固定 horizons `[1,5,10]` 的共同样本结果和事件明细；最初不用分组搜索 | 已注册执行观察的只读分页，增加原 trades |
| 首个完整实例 | 合法事件完整覆盖最大期限；至少一段实际库存跨两个调仓边界且在十个会话内结束；全体候选事件均登记 | 允许加仓、部分减仓、同日归零后再入场、亏损/盈利闭合段及期末右删失段；保留现金日 |
| 不做的推断 | 不由有限事件推断显著性、最佳持有天数、因果机制或可交易 Sharpe | 不把同成交路径成本加回当反事实策略；不猜拒单、逐股估值或实际原股份认证 |
| 合法终态 | diagnostic_report / insufficient_evidence | diagnostic_report / insufficient_evidence |

`[1,5,10]` 是建议的首轮固定有限集合，在看新结果前冻结；若实际题目必须覆盖更长库存段，则该题公开资料不足，应在研究启动前改题并另冻版本，不能看结果后追加到“最好看”的期限。第一轮不用五分组、匹配对照或参数搜索；新增不同路径/缺失/重叠情况先作零研究模型的工具反例，不自动算作额外研究题或独立样本。

普通包应显式列出 `task_kind=diagnostic_calibration`、合成/开发与暴露标签、session_calendar、决策及执行时间语义、起点策略、公开可用字段、工具契约版本、可访问 observation_id 和公共包哈希。它不包含 oracle 名称、机制标签、生成器、种子、预期数值、评分谓词或参考解。

## 3. 最小公开工具契约

只需一个受控校准适配器，不复用现有要求策略占位的最终 submit。run/architecture/task 权限由控制器绑定，不能让模型通过参数任选其他运行。

```text
inspect_execution(observation_id, table, cursor="", limit=50)
  table ∈ {summary, daily, trades, spells, inventory}

diagnose_horizons(observation_id, horizons=[1,5,10],
                  sample_policy="common_full_horizon",
                  observation_unit="eligible_signal_stock_day")

inspect_events(result_id, cursor="", limit=50)

submit_diagnostic(submission)
```

此处 observation_id 绑定本题已冻结的起点 strategy/expression，不允许借此再搜索策略；题一的起点和表达式必须来自普通定义的固定版本。`inspect_execution` 返回同一已存在 development observation 的数据，缺失就报 unavailable，不隐式 evaluate 或补跑市场。

所有查询返回 `result_id`、task/observation/public_package/data/strategy/source/policy 身份、表 schema、内容哈希、固定排序、total_rows、覆盖截止、当前页行 ID 和 next_cursor。游标绑定 observation、表名、版本、内容哈希及偏移，禁止换表/换观察复用；重复页只计缓存查询，不增加独立实验。请求不接受路径、SQL、任意字段表达式或外部 URL。

`trades` 保留 date 与 `source_order_ordinal`，同日顺序沿原档案，不能改按买卖方向排序。`daily` 同时提供原归档值与受控派生值的明确字段来源，不能把派生表冒充原账本。summary 仍按通常研究口径提供；原表可达性决定能否计“独立复核”。

共同样本工具的最小返回必须包括：

- 候选事件总数、独立 signal 日期/股票覆盖、各 horizon 单独可用数、共同事件数、共同 event_id 集合哈希。
- 每个 horizon 的同一集合等事件权重均值，事件 entry/exit 会话及对应开盘价、逐事件 gross return；首轮不输出统计显著性。
- 所有排除事件的 event_id 和固定原因：缺信号/资格、缺入场、缺某终点、超 cutoff 等；同一事件有多项原因时全记，同时固定用于互斥计数的优先级。
- 每个事件的最大 outcome_available_at 与末端 cutoff，明确这些是历史诊断结果，不能作为当时可交易特征。

不允许循环调用原单期限 diagnose 后拼接均值。共同集合要从冻结最大 horizon 的完整性条件一次取得，所有结果核对同一 event_id 集合。第一轮样本不重叠，不由此声称独立市场样本；重叠与截止反例分别检查身份和拒绝/缺失处理。

查询上限建议冻结为每题 16 个只读动作、每页最多 50 行、一个 horizon 集合、一次最终提交、新回测数 0。这是有限原型的接口预算，不是已批准的研究模型调用预算。参考解先证明此额度足够；不足只能在任何研究启动前调整并重冻。当前阶段研究网关仍为 0 调用，后续实际模型阶段另冻模型请求次数、名义预留与停止规则。

## 4. 建议冻结的最终诊断 schema

以下是可直接保存为 JSON Schema 的最小结构，仅使用当前受控网关已支持的基本类型；不使用 oneOf、任意程序或动态 schema。独立校准适配器还必须执行后述语义校验，不能把通过 JSON 结构当作诊断正确。

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "task_id", "public_package_hash", "outcome", "findings", "next_test", "self_check"],
  "properties": {
    "schema_version": {"type": "string", "enum": ["diagnostic_submission_v1"]},
    "task_id": {"type": "string", "enum": ["calibration_01", "calibration_02"]},
    "public_package_hash": {"type": "string", "minLength": 64, "maxLength": 64},
    "outcome": {"type": "string", "enum": ["diagnostic_report", "insufficient_evidence"]},
    "findings": {
      "type": "array", "maxItems": 12,
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["claim_type", "quantity_id", "availability", "value_decimal", "unit", "statement", "evidence_ids"],
        "properties": {
          "claim_type": {"type": "string", "enum": ["numeric", "interpretation", "limitation"]},
          "quantity_id": {"type": "string", "maxLength": 160},
          "availability": {"type": "string", "enum": ["observed", "derived", "not_identifiable"]},
          "value_decimal": {"type": "string", "maxLength": 80},
          "unit": {"type": "string", "enum": ["CNY", "return_fraction", "sessions", "count", "none"]},
          "statement": {"type": "string", "maxLength": 1500},
          "evidence_ids": {"type": "array", "maxItems": 16, "items": {"type": "string", "maxLength": 160}}
        }
      }
    },
    "next_test": {
      "type": "object", "additionalProperties": false,
      "required": ["method", "required_evidence", "observation_supporting", "observation_refuting"],
      "properties": {
        "method": {"type": "string", "maxLength": 1500},
        "required_evidence": {"type": "string", "maxLength": 1500},
        "observation_supporting": {"type": "string", "maxLength": 1500},
        "observation_refuting": {"type": "string", "maxLength": 1500}
      }
    },
    "self_check": {
      "type": "object", "additionalProperties": false,
      "required": ["assessment", "next_step"],
      "properties": {
        "assessment": {"type": "string", "maxLength": 1500},
        "next_step": {"type": "string", "maxLength": 1500}
      }
    }
  }
}
```

必须同时冻结的语义校验：

1. task_id 与包哈希必须匹配当前授权任务；包哈希是 64 位小写十六进制。拒绝重复 JSON 键、非有限值、未知字段、重复或矛盾的同一 quantity 声明。
2. numeric 的 quantity_id 必须属于公开数据字典或合法查询结果的量；当 availability 为 observed/derived 时，value_decimal 由 Decimal 严格解析，禁止 NaN/Infinity。金额、收益、会话和计数的单位不可互换。结构校验不接受把任意量命名为“已证明 alpha”。
3. not_identifiable 必须 `value_decimal=""`；interpretation/limitation 非数值项也为空并用 unit=none。零是确定的数值，不能作为缺失代号。非数值项 quantity_id 可为空。
4. evidence_ids 只能引用当前任务实际返回的包、工具结果或行 ID，至少一个有效引用；引用 private/oracle/其他架构 ID 直接拒绝。证据缺失本身可引用公开数据字典及 unavailable 返回，不要求虚构支持证据。
5. schema 合法、数值正确、语义正确、权限合法分开登记。self_check 的长度/角色数量不计分；不同有效解释不因没有使用预设术语而扣分。

现有 runtime.validate_response 的 minLength/minItems 等支持不完整，不能直接宣称它已完整执行本 schema 的所有约束。实现时由校准适配器使用完整的冻结校验函数并测试，或明确同步补支持；本轮不改 v7。

## 5. 独立数值 oracle 与参考解可达性

### 题一：时序、样本、库存

oracle 只从冻结原始合成 session_calendar、日期/股票/开盘价、当时资格与公开信号规则、原订单重算。不得导入或调用被测 `_weights`、`diagnose`、`diagnose_horizons`、库存诊断函数或它们的中间结果来生成 expected。

用独立会话序号映射逐条列举 t、t+1、t+h+1。对事件 i、期限 h：

```text
r[i,h] = open[code, session(t)+h+1] / open[code, session(t)+1] - 1
C = 所有 horizon 均有合法入场与终点、资格/信号合格且不越 cutoff 的事件集合
mean[h] = sum(r[i,h] for i in C) / len(C)
```

用整数分数或从十进制字符串构建 Decimal，独立验证 C 的完整 ID 集合、各期限终点和排除原因。这里的共同集合严格是 eligible signal stock-day，既不是 top_n，也不是实际成交入场；参考解若比较实际组合，必须另引原订单/连续库存证据。不同观察对象产生差异只能先确认“不能直接对应”，不能仅凭符号反转认定某个市场机制。

库存 oracle 从订单逐笔累计数量，零→正开始一段，部分卖出不闭段，归零结束，之后同日再买是新段；会话时长按冻结日历的退出序号减入场序号。期末未归零只给 observed_age 与 right_censored，不能作为完整持有时长。调仓日历单独从普通策略规则取得，不替代库存路径。初始题应避免近零浮点歧义；通用边界仍按冻结 1e-8 数量、1e-6 金额双条件判定。

### 题二：现金、平均成本、同路径桥接

oracle 只读原始订单和逐日聚合估值，以独立 Decimal 状态机实现；不能调用 `build_execution_diagnostics` 或复用其 expected JSON。

```text
买入现金变化 = -(成交数量 × 成交价 + 该笔费用)
卖出现金变化 =  成交数量 × 成交价 - 该笔费用
新平均成本 = (旧库存成本 + 买入成交额 + 买入费用) / 新库存数量
卖出已实现净 PnL = 卖出成交额 - 卖出费用 - 卖出数量 × 当时平均成本
当日净资产 = 现金 + 已归档聚合持仓市值
当日净 PnL = 当日净资产 - 前日净资产
当日未实现 PnL = 聚合持仓市值 - 剩余库存成本
```

逐日同时核对净 PnL = 已实现净 PnL + 未实现变化，以及净 PnL = 同一已成交数量路径参考 PnL − 费用 − 滑点。参考 PnL 使用同批订单的 reference_price 现金流和同一聚合估值独立计算，不运行“零成本新策略”。成交价已包含滑点，平均成本及已实现量已含相关费用，不能再次减去。全部日期、订单、正负闭合段、右删失段逐项对上，不能只核对期末总数。

固定金额绝对容差 1e-6 CNY、收益 1e-12、数量零判定 1e-8；会话序号、计数、事件集合和哈希精确相等。容差事前固定，不能随被测误差调整。对数量/价格字段首先验证表示、舍入政策和单位；首轮原型采用精确十进制可表达数据，避免另引货币舍入研究。

**公开资料无法确定的量，即使生成器或 oracle 知道也不能要求研究器答出。** 没有逐股日末标记就不能定位每股浮亏；没有拒单记录就不能证明停牌/涨跌停导致久持。应构造两种私有细节不同、公开聚合账本相同的情况，确保两者的正确公开结论均是 not_identifiable，而不是让评分依私有答案强判某个具体原因。

### 两条验收路径必须分开

- **数值 oracle 路径：** 独立实现从冻结原始数据证明内核/导出值是否正确，保存来源与逐项差额。它可以读 evaluator 私有数据，但其结果不是研究器可达性证明。
- **参考研究解路径：** 独立客户端只通过上述研究器公开契约、相同公共包和资源上限生成合法 submission，保存完整允许的查询轨迹。它不能导入生成器/oracle、访问私有路径，不能使用硬编码题号→结论映射。小规模普通算术必须也在研究器能力范围内；若实际要求隐藏脚本或额外计算工具才能完成，先把所需能力公开或缩题，不能事后算模型失败。

评分器最后把合法参考 submission 与独立 oracle 对照。只有两条路径都通过且公开轨迹可由同权限客户端重放，这题才可进入实际 Astra/xhigh 校准。脚本成功只证明接口和确定性推理路径可达，不证明模型已经自主找到参考解；后者仍须后续实际研究调用检验。本轮两条路径均未运行。

## 6. 评分表与公平分母

每维只记 `met / unmet / not_evaluable`、证据 ID、评分规则版本和原因，不合成总分。以下表仅给可信评分端；研究器收到普通任务、字段和工具语义，不收到 expected 数值或“本题应该找到什么”的提示。

| 维度 | 题一的冻结判据 | 题二的冻结判据 |
| --- | --- | --- |
| 来源/观察对象 | 引用本题数据，区分 eligible 信号、选股、实际订单和连续段 | 引用同一观察的原账本与订单，区分原记录与派生摘要 |
| 时序/覆盖 | t→次开盘→h 后开盘正确；完整 event_id 集合相同；不跨 cutoff | 全部 session_calendar 日期和 source_order_ordinal 被保留，缺失明确指出 |
| 数值复核 | 至少完成与所述结论相关的共同事件期限计算及实际段时长核对 | 相关逐日现金/净资产、成交/平均成本、两条桥接与期末合计正确 |
| 解释有效性 | 不把调仓周期当持有期，不把有限 gross 事件差异当净策略收益 | 不重复扣费，不把成本加回当反事实，不把部分退出当整段闭合 |
| 缺失/弃权 | 截止或对象不能对应时保留不足；不以低功效无差异证明无机制 | 右删失保留；不捏造逐股估值、拒单原因或原股份执行认证 |
| 下一检验 | 可取得或明确待取得的证据，能区分当前解释，写出相反观察的影响 | 直接针对未识别原因/剩余残差的证据，而非无依据追加收益调参 |
| 工程/权限 | 合法 schema、真实证据引用、无越权；一次合法最终报告 | 同左 |

数值和身份部分由独立程序确定；自然语言中的解释、相关性和“下一检验是否有区分力”由事前书面规则人工复核，不暗中加模型裁判。人工复核只看合法公开轨迹与评分依据；允许不同等价推理和替代有效下一检验。确有语义争议时标 ambiguous 并保存理由，不为本轮胜负事后改规则；修改后的标准另起版本。

查询原表不能证明模型内部没有抄摘要。可观察记录只使用 `summary_cited`、`raw_evidence_cited`、`numeric_claims_oracle_consistent` 等事实，不能把“点过原表”授予独立发现分；独立性首先指评分 oracle 的实现与被测内核分离。若后续要单独测数值重建能力，应另冻不含派生答案的普通原账本任务，并与当前提供常规摘要的条件分开报告，不能在本轮途中撤掉信息。

始终保存计划的两项、实际启动、工具/模型失败、格式失败、合法报告、弃权、各维结果、全部成本和观察暴露。权限/工具缺陷导致 not_evaluable 不能偷偷删掉该项；公开报告同时列工程失败及可评维度。首轮只逐题报告，不用 2 题的百分比宣布稳定性或架构赢家。

诚实 insufficient_evidence 是合法终态，是否正确取决于该题的公开可识别性；全题一律弃权不自动得满分。合成诊断题永远不进入真实策略成功率分母。`execution_valid=false`、`formal_target_success=false` 固定保留；没有策略提交也不填占位策略计成功。

## 7. 隐藏答案的可测试分隔

最小采用三个权限区：`public_packet`（普通输入和工具说明）、`evidence_service`（冻结题目数据/本运行工具输出）、`evaluator_private`（oracle、参考解、机制、评分表和生成器）。研究器只得到公共包和工具结果，不直接挂载任一区的文件系统。受控工具服务按数据白名单读取，私有区不在其可读根中。当前 v7 的 CLI 工具禁用不等于已经完成此操作系统级隔离；若做不到可验证权限分隔，只能报告受控接口隔离，不能声称对同权限任意代码保密。

必须实际执行的最小隔离/反例验收如下，当前均待实施：

| 测试 | 应观察到的结果 |
| --- | --- |
| 给 evaluator 私有答案加入随机 canary；检查实际序列化 prompt、工具响应、错误、导出和日志视图 | 无 canary、expected 值、机制标签、私有路径；这只是明确泄漏探针，还需其余权限检查 |
| 保持公共 fixture 不变，只改私有答案/文件名 | 所有合法公开查询结果逐字节相同；评分仅在最终提交后内部变化，查询不能成为答案探针 |
| 越权 observation、另一架构 result_id、绝对路径、`../`、编码路径、其他表游标 | 在任何文件读取/工具计算前拒绝，错误不能泄漏文件存在性或私有内容 |
| 枚举 artifact、源码下载、source snapshot、GUI 调试结果和重放接口 | 研究器凭证无法取到私有区、其他运行或完整父任务；允许的公共导出也只有白名单字段 |
| 参考客户端的 import/read 权限禁用私有区 | 仍可从同一公共接口完成参考报告；偷用 oracle 或生成器立即失败 |
| 两期限观察数相等但 event_id 集合不同；换一天终点；漏负事件/现金日/右删失段 | ID/日历/会计核对拒绝，或明确 evidence_invalid；不能继续出“已复核”评分 |
| 金额错 0.01、引用另策略的同字节表、遗漏同日订单顺序 | 数值或语义身份核对失败；不只凭文件哈希相等接受 |
| 同一个合法提交重复发送、完成后再次查询评分并尝试新提交 | 只返回原提交回执；不增加研究样本，不开放 oracle 反馈进行同实例调优 |

公开包哈希只覆盖公共内容。不要公开低熵隐藏答案/seed 的裸哈希让研究器枚举反查；如需提交隐藏包的外部承诺，采用高熵随机盐/HMAC 并把密钥及私有清单留在评测端。中性 task_id、symbol、文件名和错误文本也不得编码机制或正确方向。

本 readiness、当前审查报告、旧会计原型 expected 数字和 root 对话均是元设计/评测材料，不能直接拼入研究器 prompt。正式研究调用使用新隔离上下文的白名单输入，不 full-history 继承父任务。最终评分/参考解/人工提示一旦被架构设计者或后续研究器看到，登记反馈暴露；该实例后续使用标为开发，不再称未见题。

## 8. 最小顺序、停止与两问自检

只安排四个离线交付：普通包/数据字典与权限清单 → 题二 trades/calendar 查询及独立 Decimal oracle → 题一共同事件接口及独立时序/库存 oracle → 同权限参考客户端与上述拒绝测试。两题全部达到“oracle 正确、参考路径可达、隐藏区不可由公共接口获取”后，再冻结实际模型校准预算与次数。当前不启动研究网关，不重复历史回测，不把新文档当闭环完成。

任一来源/日历/事件集合/数值不一致、参考解使用额外权限、工具把未知填零、公开接口泄漏 oracle、或执行未认证却计真实策略成功，立即停止该题上线；保留失败和原版本。修复后只重跑对应边界，不能靠追加提示暴露答案提高得分。

**这一步做得怎么样？** 将两题收敛为能被核对的有限诊断，补出原订单与独立日历两项可达性缺口，明确普通摘要已含结论所限制的能力解释，并提出与当前基本 schema 类型相容的最终报告结构。数值、自然语言评分、权限、分母和执行认证彼此独立。尚未实现或运行 oracle/参考路径/隔离测试，因此没有新增题目通过证据。

**下一步该做什么，如何改进？** 最有信息价值的是题二同权限客户端能否只凭 trades、daily、calendar 和正常说明重建两条会计桥接。若可达，固定接口并验证拒绝/缺失边界，再接题一；若必须读取私有生成器或额外原表，就改公开契约或缩评分范围，在启动研究前重冻。保持新增研究网关为零，待两条独立离线路径通过后另冻实际模型预算和停止条件。

本轮读取身份 SHA256：

- `p1_tool_readiness.md`：`85c6969e2eb2badeae7d7ceb70f9057609556fdacca6bcd6ff88c3ddaebe25c7`
- v7 `meta/ashare_case.py`：`c0c213e5ad6325a7353f95505566ec1df514b93a7c3375418a0f747177ee3893`
- v7 `meta/ashare_research.py`：`60b5ccad8b6c0ea3a0518cdaa6ab60ca3952315605ecd23a8cd598158cde6320`
- v7 `meta/execution_diagnostics.py`：`bfb496de608691a9cde916b991a4c721e94b82b2cab4ed4dc2309bbff2b8d1b1`
