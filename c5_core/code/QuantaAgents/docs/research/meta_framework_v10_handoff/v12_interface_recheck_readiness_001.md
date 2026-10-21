# v12 一次两题公开接口复校：独立准入复核 001

2026-09-07，Astra/xhigh 独立只读审查。**对同一冻结 v12 下、仅一次新建 campaign 的两题 exposed 诊断接口复校，给出有界通过。** 当前未发现需要先修改实现的实质阻断。目的仅为观察充分公开提交规则之后，新上下文中的模型能否形成合法终稿；不能借此修复、替换或抹去 v10 的失败，也不能据两次新题目启动估计架构稳定性、成功率或策略收益。

本报告不是派发操作，未创建 campaign、网关、游标密钥或工作账本，未调用研究模型、网络、真实市场数据或回测。root 应先生成绑定本文、正式验证回执及完整代码/脚本清单的追加 gate，再仅准备一份新 campaign。本文放在 v10 handoff 目录，是沿用未修改 runner 的 gate 路径约束；它审查和接受的是下列 **v12** 身份，不是重开 v10。

## 精确范围与停止规则

| 项目 | 此次准入范围 |
|---|---|
| 目的 | 一次新版本公开协议复校；观察合法终止、证据使用、数值覆盖与正确性、弃权、解释和可行下一检验，分别记录，允许有效不同表达。 |
| 题目 | 仅 `calibration_01`、`calibration_02`，仍是原 v9 已接受的两个公开开发题。所有普通输入字节保持不变；公开提交契约和错误解释来自 v11/v12 冻结源码。 |
| 请求身份 | 请求 `gpt-6-astra` / `xhigh`。实际供应商身份及请求绑定的独立认证仍为 **unknown / unverified**；本地请求、CLI 事件及哈希不得冒称供应商认证。 |
| 上限 | 总至多 34 次模型调用、2 题；每题至多 17 调用、16 查询、1 个唯一终稿机会。名义总 1,200,000 token、每题 600,000，下一调用预留 80,000；原 300,000 软提示、单调用及总运行时限不变。 |
| 新身份 | 新 run ID；每题在该新 run 下新建 scope、cursor key、journal 及调用目录，使用无父线程的全新 CLI 上下文。`architecture` 仍是固定角色标签 `single_researcher_diagnostic`；隔离依赖新 run+case/scope 身份，而不是给角色标签换名。 |
| 禁止回灌 | 不提供 v10 已保存报告、失败评语、本文、独立 oracle、参考解、评分维度或父上下文。模型只见本题原普通包、公开格式契约及本次已返回的公开查询记录。 |
| 终止 | 非法完整行动/终稿即停止该题并保留其费用和失败；其他原计划题仍可按原控制器继续。未知调用或未解决的公共行动暂停 campaign。不得另付重试旧槽，不因终态失败自动或人工使用本准入再创建第三 campaign。 |
| 分母 | v10 两个失败永久保留。新 campaign 的两个计划题、全部查询、调用、预算停止、弃权和失败分别保留；两版可以描述接口行为差异，但不是隐藏题重复验证、独立题库扩充或架构获胜。 |

token 数是项目内 nominal 准入与未知风险预留，不是供应商账单或账户级绝对费用帽。本报告不要求与当前明确边界无关的 OS 认证，也不把禁工具 CLI 宣称为完整 OS 沙箱。

## v10 → v12 诊断路径差异核验

本次逐字节比较确认，下列模块在 v10、v11、v12 **完全相同**：`diagnostic_campaign.py`、`diagnostic_live.py`、`diagnostic_inputs.py`、`store.py`、`runtime.py`、`codex_gateway.py`、`evidence_queries.py`、`calibration_views.py` 及 `scripts/run_live_diagnostic_calibration.py`。因此既有原子预算预留、唯一 attempt、registry/dispatch lease、立即用量持久化、已知用量最大值保留、未知预留、在途暂停和 saved-only 恢复边界没有被此次改动放宽。

诊断路径实质改动仅为 v11 已接受的两文件，v12 与 v11 完全一致：

1. `diagnostic_calibration.py` 将原有规则公开为 `submission_contract`，列出 numeric quantity 的封闭字典、单位、未知值表达、整数性、已返回 evidence 约束以及非数值 finding 的 `unit=none` 和空 `value_decimal`。校验逻辑抽取成纯函数，错误带路径/规则名；原接受集合没有放宽。两份 v10 实际失败报告仍因原对应规则不合法，不能导入成新成功。
2. `diagnostic_actions.py` 将版本升级为 `diagnostic_actions_v2`，明确结构 schema 与公开契约同时适用，并细化错误路径。一次 query 或唯一 final 的外层行动合同、次数和停止政策不变。

[v11 独立评审](../meta_framework_v11_handoff/review_001.md) 已核查接受集与两份实际失败的反例。本次重读上述 diff，未发现改变数值含义、增加隐藏条件、返回参考答案或移除终态失败的路径。

v11 → v12 全源码清单的既有文件变化只有 `ashare_case.py`、`ashare_research.py`、`diagnostic_monitor.py`，另新增 `event_conditioning.py`。前两项为通用 A 股 horizon 动作接线；新匹配核尚未注册进 researcher harness；monitor 是单独只读显示。它们没有改变当前两题诊断 runner 的可查询 table 或控制器行动路线。此次复校不是新 P2 匹配功能的模型试验。

## 已有验证能否沿用

本次直接核验了正式 receipt、其 JUnit SHA256、源码/脚本清单与冻结测试文件，而非只引用通过数字：

| 回执 | 核验与适用范围 |
|---|---|
| [v11 209 项回执](../../../experiment_traces/meta_ashare_revision11/validation_attempts/20260906T175127021941Z/receipt.json) | `passed`；209 tests，0 errors/failures/skipped。该次全部诊断/校准测试文件在 v12 字节不变，相关 live 控制器及依赖同版，可沿用其公开契约、预算、恢复、隔离、语义数值和独立反例验证。 |
| [v12 301 项正式回执](../../../experiment_traces/meta_ashare_revision12/validation_attempts/20260906T181855964601Z/receipt.json) | `passed`；301 tests，0 errors/failures/skipped，原 7 项异步 JS 反例通过。当前 v12 完整源码、脚本及其所选测试文件逐项匹配该回执与 plan；覆盖新 P2 及受影响依赖。 |

两个不同验证运行不相加成“510 项同次通过”。本次没有重跑无关测试、演示、市场研究或旧题。v12 horizon demo 的成功仅支持工程接线，其 `b81b3cf…` manifest 使用不同路径域/序列化，不能直接等同正式 `6abf192…`；本准入一律使用标准正式 manifest。demo 不能替代诊断 live 控制链的已有验证。

正式身份：

- v12 `source_manifest.hash`：`6abf192420312765c42b115c217fd05c760bb3b432e05dc1b4e76fed94c977b9`。
- v12 正式 receipt SHA256：`bd8e8d52c5499eca7f1bedfc355480d0bacef287124bd7929b4d4ebf203d2424`。
- v12 `scripts_manifest` 的规范内容哈希（`content_hash` 算法）：`4304d715d0e311799864b7630a12f409512ebc0022a93643212654382823762b`。gate/plan 仍须绑定整个原 manifest 对象。
- v11 receipt SHA256：`55bd6f97c0316d81774070209ac4ac35c67d1fa35a63a31861f6530b7a137471`。

## 原输入与旧失败身份

本次只读实际执行了 `verify_prerequisites()` 与 `accepted_inputs()`，二者没有网关或写入行为。先验证原接受 receipt，再验证题目包身份和全部公开文件字节，结果如下：

| 题目 | public_package_hash | 完整公开输入 manifest hash | 总字节 |
|---|---|---|---:|
| calibration_01 | `58b06b2b84b858aa9255c97ecf185f02f6175f92f2b6bc1ca9d50c95cad83cf3` | `c0bfc28333ef978a02743f01ba009c358e6eb3a82cc9c78aa0d83052c3bab385` | 34,530 |
| calibration_02 | `5c3731b1c8dab3e267a4626e8e69c83151056e00c7215d92cda700759691c977` | `309f8fb5fd631fc6006f64135fb80008f3a68c438d8f5361c948f590ba794af1` | 38,717 |

输入只允许 packet、原诊断 report、execution ZIP、session calendar、observation，以及题一已公开 horizon inputs。没有 evaluator_private、旧回答或评审文件入口。新增 `submission_contract` 在普通包外由源码确定生成，并通过完整源码/PROTOCOL 身份冻结；原题 packet 自身哈希没有伪改。

对旧 campaign `diagnostic_20260906T172850309804Z`，本次逐项重验正式 plan 记录的 **121 个原文件**，全部与 v11/v12 原清单一致；通过 SQLite `mode=ro` 核对当前状态仍为 **13 completed calls、238,797 reported I/O tokens、0 reserved、0 unsettled**。两题仍 `failed`，已完成公开查询分别 6 和 5，合法终稿计数均为 0。这里的 0 表示无被接受终稿，不能说模型没有提交最终回答；两份非法完整终稿继续保存在旧记录里。

新上下文使用同一个原公开包不意味着可以沿用旧 journal。`build_public_case` 以新 run/case 注册新 scope，`open_workbench` 核对 journal 的 packet/scope 身份；live `_bound` 另校验本 campaign 的 packet/scope/PROTOCOL hash。旧 v10 plan 的 source/PROTOCOL 不匹配 v12，不应使用 v12 `run/reconcile` 打开旧槽。旧 campaign 的全部终态只用于只读评审与账务，不构成本次研究器输入。

## 控制门与实际准备后的核对

当前 runner 的 `prepare` 会要求：正式验证 receipt 在 v12 stage 的 append-only validation 目录；`status=passed` 且完整 source/scripts manifest 与当前相等；gate 位于指定 v10 handoff 目录、状态为 `accepted_for_two_exposed_diagnostic_cases`，绑定该 receipt SHA 和完整清单；gate 指向本文并核验本文 SHA。随后 `load_campaign` 固定验证回执、gate、source、scripts 和 plan 哈希。报错文案中残留“v10 validation”只是旧字符串，实际路径使用当前 `STAGE`，本次没有为了文案改源码。

准入证应明确本次是一份新 campaign 的接口复校，登记旧 campaign lineage、两题永久失败及禁止第三 campaign。**现 gate 不是机械的一次性令牌**：registry 防止同时存在未结算 campaign，但在前一份全部终态后，显式再次调用 prepare 仍能创建另一份。这不构成自动重派路径；单次新 prepare 是本次授权边界，不能凭相同 gate 再次执行。root 应在成功准备后记录唯一新 campaign ID 与计划哈希，不把本报告当成长期可复用许可。

首次新派发前核对新 campaign 的两个 journal 均为空、无旧 call/action、独立 scope/key、公用包身份正确，并保存计划及 preparation receipt。若准备部分失败、出现未知用量、证据哈希变化或未解决的 public action，则沿用既有 fail-closed 与 saved-only 流程，不另建 campaign 绕过。非法终态不获得额外模型修稿机会，研究器不会收到独立评分或参考解。以上核对属于已授权准备的具体完成条件，不要求新增网络或市场验证。

## 关键诊断文件 SHA256

| 文件（相对 v12） | SHA256 |
|---|---|
| `src/quanta_agents/meta/diagnostic_campaign.py` | `9ce41fff24d71099edfaa8f037b67906bc56c63878b2d61cadb9bf4ee7e29dfb` |
| `src/quanta_agents/meta/diagnostic_live.py` | `9c84be10746f88b5ca854e561c03ea2fe6ac787ccc137e4cf341f190acfb9992` |
| `src/quanta_agents/meta/diagnostic_inputs.py` | `eb443cb96b3b879c38e108616aee88fbfb233370f51955f72f7dd0df648fa864` |
| `src/quanta_agents/meta/diagnostic_actions.py` | `32d647e5428ba54b390ef93a749b8d609d3f975c628ac66e78427b40d35a166f` |
| `src/quanta_agents/meta/diagnostic_calibration.py` | `99338343f6baf89d852709fa61586ba8fe70c810b7dad404b6b7802da0b52a71` |
| `src/quanta_agents/meta/store.py` | `a722ca5256125f3eb10cedce51e3324f9eea8740672a6152597c795c3e2e9929` |
| `src/quanta_agents/meta/codex_gateway.py` | `f4b7d865ce2de39765324cad1afcd2a2f42cd856514eac997c2845ebb9f20e99` |
| `scripts/run_live_diagnostic_calibration.py` | `a864e38ac0f8f4b495f04045290a42a3875a4bd09e9bd4429f2c53184def04db` |

## 两问自检

**这一步做得怎么样？** 已核验新版本接受集合与公开规则、旧控制链字节身份、正式 source/scripts/test 清单、精确原输入及原失败账务。通过范围足以支持一次新接口复校；没有因旧报告的语义正确而改判其接口失败，也没有把新 P2 工具或本地模型请求标签升级为模型能力或供应商认证。研究 Gateway 新调用为 0；本工程评审的模型用量另列，不能写成零。

**下一步该做什么，如何改进？** root 按本文绑定唯一 gate 和新 campaign，原规则下一次运行并保留全部终态。两份新终稿冻结后再独立复核接口格式、数字全量及遗漏、证据可达性与有效不同解释；不返回评分或修稿建议给本次研究器。若再次格式失败，它仍是一次失败结果，不据此开放第三 campaign。无论新结果如何，旧两失败、实际供应商身份 unknown、`execution_valid=false` 及无架构稳定性结论均保留。
