# 实际纠错 permit 的独立只读范围核验 001

结论：**实际准备材料满足本轮 73 项静态检查，未发现因实际 schema、保存事件形状、路径或 ID 差异而导致当前构造器读取失败的阻断。permit 仍只是准备材料，不是批准或可直接派发的准入。** 本核验没有构造 Controller / Store，没有调用其 API，没有新建 correction 表、请求或 ready admission。

机器可读结果为 [transport_permit_scope_report_001.json](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v16_handoff/transport_permit_scope_report_001.json)，SHA256 `ee587d6bbd90304c352e40244c4722ce1a263a674bfd00431826378574568093`。73 项是实际元数据核对项，不是新增 73 个 pytest 测试，也不是供应商验收。报告记录全部读取文件的哈希，结束时再次核对均未变。

## 范围与身份

- permit 文件 SHA256 `5af20e57af525105fff9484d2c763282e9edc377282f3bf80f874c76e6a1198c`，规范内容 hash `963bd640f0241ed30902e6bbaba500b1a08c1ff31a772dba2a17791c9ae3a6eb`。
- preparation receipt SHA256 `117e8116a749658a1ea213d3468ae5a64ecf8d583abc71757ee0d252f9bdbefc`；原 scope 文件 SHA256 `349eddbd9771ccb2ebd860d3454ef35104a53f39582ec0aaddaacf618a42c970`。两份准备材料均明确 paid=false；permit 准备目录只有 permit 与 preparation receipt，没有 ready 文件。
- 原 run 为 `reference_v15_original4_001`，plan hash 为 `3cda519a83180b6b5b4fe96c447a406efee82b5df73b31cc450fd10555437173`。唯一适用 call 为 `reference_v15_original4_001/reference_v15_original4_001_case_01_reference_round_0-1`。
- 原 registry / active owner、campaign 包装文件、run / call 内容 hash、原 v15 十文件 source manifest 均一致。新五文件 source pins 与当前文件一致，其中 `transport_correction.py` 为 `5fa687acd947870d4b4fd2896a1a693e6f1e3de9c8a28614336e82ffbff0d3f1`。

核验对照 [transport_correction.py](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_ashare_revision16/src/quanta_agents/meta/transport_correction.py:221) 的构造入口、`_check`、`_reserve` 与单次 `execute_once` 路由。未调用这些函数；实际数据库只用标准库 SQLite 的 `mode=ro` 和 SELECT 读取。

## 实际材料与合成夹具的差异

实际有五条事件：`thread.started → item.completed → turn.started → error → turn.failed`。额外 item 是已知 Code Mode unavailable 警告，类型为 error，符合当前读取器允许的特例；没有研究回答或工具结果。两条错误均为保存的 HTTP 400 / invalid_request_error / invalid_json_schema / text.format.schema。现有 process_exit 与 request 的 invocation ID 对齐，process_started / exit_observed 均为 true，退出码为 1；所有事件均无顶层 usage。这支持该本地请求的明确拒绝终态，不能证明供应商零账单或当前全部进程均已停止。

13 份原件共 94,443 bytes，全部 hash 匹配，单文件与总量均低于读取上限。原调用目录恰有七个必需文件，无额外或缺失文件。原 `prompt.txt` 为 9,919 bytes，UTF-8、无 CR；原 request 的逻辑 prompt hash、permit 的原始字节 hash 与 scope 的提示词副本三者分别核对一致。没有导出提示词正文或添加研究指导。

四个工作台**实际位于原 preparation 根目录** `experiment_traces/meta_casebank_reference_preparations/reference_v15_original4_001/workbenches/<task_run_id>`，不在 campaign 根目录之下。它们正是原计划 `controller_paths` 冻结的位置；当前代码按原计划读取，因而不是构造器阻断。每个 `frozen_public.json` 只核字节 hash；对应 SQLite 状态 hash / scope / task 顺序全部一致，均 ready、actions 为空、query / candidate / final_attempts 为 0。

审计第一次在内存中误加“WB 必须在 campaign 内”的路径假设，于 `Path.relative_to` 抛错并在产生报告前停止。随后改为核对上述原固定 preparation 路径。该错误属于审计脚本假设，不是产品失败，也没有引起实际文件写入；机器报告保留了这段说明。

## 准入范围与预算没有扩大

新 wire 与原 schema 的全部差异仅限 `/properties/candidates/items/properties/horizons`：删除原复合 `enum: [[1,5,10]]`，增加 `minItems=3`、`maxItems=3`；已有 scalar item enum 及其余 schema 原样保留。原精确 schema 继续作为保存响应的语义校验器；本次没有重写普通合同、排序或补齐模型返回，也没有增加别题上下文。已有最终 51 项 schema 修复验收可支持这一本地边界，不能证明供应商一定接受新 wire，原 400 的唯一根因仍未证实。

| 项目 | 当前实际 | 若未来另行批准并完成原子预留 |
|---|---:|---:|
| 同一任务 / 阶段调用 | 1 / 1 | 2 / 2，分别计入 17 / 68 上限 |
| 原未知费用预留 | 80,000 | 80,000 保留 |
| 新纠错预留 | 0 | 80,000 |
| 名义暴露 | 80,000 | 至少 160,000 |
| task / stage 名义上限 | 600,000 / 2,400,000 | 原值不变 |
| semantic round | 0 | 0 |
| query / candidate / final | 0 / 0 / 0 | 0 / 0 / 0，响应不会自动应用 |

原费用是未报告，不能写成实际费用为零。原 `max_attempts_per_step=1` 保留；本准备明确承认 `one_explicit_second_transport` 是一次另行批准的政策例外，并以 transport ordinal 2 标识，没有伪称从未重发。例外仅适用于上述唯一 call，最多一次新传输；四题描述性分母不变，正式成功分母增量为 0。

原 created_epoch `1788728483.6826499` 与 budget 在 run、plan、permit、scope 四处一致。六小时截止仍为 **2026-09-07 03:01:23.682650 UTC**；本次快照时仍未过期，暂停和工程修复耗时没有退还。这个时间核对不能作为未来派发时的可复用准入。实际同库尚无 `transport_corrections_v16` 表，指定纠错目录也不存在。

成功、schema 拒绝、未知或其他异常都只能保存并停止。新调用完整回执可以结算其自身预留；原 80,000 不释放。即使原 JSON schema 校验通过，也只是待进行 Workbench 交叉字段 / 机会校验的保存动作，`public_action_applied=false`，不等于题目 final 或策略完成。

## 尚缺的门与两问自检

**第一问：是否因为希望继续而降低标准、删去原失败，或给原任务新开预算？** 没有。该 permit 只绑定原 run / call / task、原提示词与语义 schema、原同库额度和原时间起点，原未知费用与第一次失败继续计入。实际路径差异已按原计划核对，未迁移或重建工作台。

**第二问：这份核验是否已经足以允许真实派发？** 不足。root 仍须在实际派发前重新确认没有在途请求和唯一控制权，再独立冻结精确 admission 字节及外部 SHA。当前报告既未检查进程存活，也未取得 lease，更没有创建 `ready=true` / `real_dispatch_authorized=true` 的材料。哈希一致不是真实时间或外部账单认证。

下一步仅是由 root 结合最终工程验收、实际 GUI 与新鲜控制权证据决定是否给出一次具体准入；本子任务不执行它。任一原件 / 源码 / 计划 / 工作台身份变化、原状态不再是当前暂停且零动作、出现第二次 correction、原截止已过、存在未核在途请求、wire 或 prompt 有额外改动，均推翻本快照的可准备性。供应商是否接受、真实模型身份和费用只有未来实际保存回执才能说明。

本取证子任务新增项目 Gateway 调用 0、工作台动作 0、回测 0、市场 CSV 读取 0、密钥读取 0、实际 SQL 写入 0。Astra/xhigh 工程与资料核验用量非零，不能把这一局部零调用声明扩展为全项目零用量。
