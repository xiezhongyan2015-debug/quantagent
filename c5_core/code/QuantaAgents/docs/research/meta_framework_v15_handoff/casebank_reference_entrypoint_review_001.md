# v15 四题参考入口：独立复核 001

2026-09-07，Astra/xhigh。**最终脚本在本次有界工程范围内通过。两处显式哈希参数可为空的问题已关闭，原失败证据保留；未发现新的派发、旧登记册混用或保存恢复阻断。** 这份报告不批准实际阶段的预算或模型调用。实际四题计划、组合验证凭证及绑定该具体计划的独立准入文件仍由 root 另行冻结。

本轮只读代码、差异和已保存的测试证据，没有执行实际 prepare/run，没有创建真实四题工作台，没有调用研究模型、读取真实行情、发现原四题候选或回测。作者运行的四个 fake final 是合成运输与控制链检查，不能算参考发现。工程评审模型用量未在此独立计量，不能记为零。

## 实际审查边界

审查 `experiment_traces/meta_ashare_revision15/scripts/run_casebank_reference.py` 的 prepare/run/status/reconcile，以及它调用的新协议 campaign/live/Workbench、来源固定和 registry 租约入口。已通过的核心作者 15 项及独立 6 项未重跑；核对其记录的当前来源和测试哈希均匹配。新增监控登记册扩展另行复核，不借本文提前通过。

| 项目 | 核对结果 |
| --- | --- |
| 固定任务与新身份 | prepare 固定 case_01 至 case_04，各一个 baseline 主研究者；独占新准备目录，为每任务新建 32 字节随机 key、独立 task_id、工作台 root 和 scope。task_binding 只接受未使用工作台。没有从旧两题协议或旧槽创建任务。 |
| 外部计划 | run/status/reconcile 均要求 expected_plan_sha256；同时与 campaign.json 的 plan_hash、完整 plan 内容 hash 比较，并核对 prepared_plan 副本。此参数是完整计划的规范内容 hash，**不是 campaign.json 文件原字节 SHA**。任务、公开包、预算、key fingerprint、入口、执行来源、验证记录均在该完整计划内。 |
| 外部准入 | run 另核准入文件原字节 SHA，以及 kind、同一 plan_hash、ready=true、real_dispatch_authorized=true、当前入口身份和 source_hash。准备生成的 readiness 为未授权，不能满足此门。参数修复后，Python API 与正式 CLI 均不能用空值跳过这项显式 pin。 |
| 来源和材料 | 当前入口脚本原字节 SHA 单独纳入 plan.entrypoint；十个研究模块逐文件 hash 和 protocol hash 由 campaign 复核。准备材料要求有效 receipt SHA、匹配原字节和 passing 状态，解析正文与原路径/hash 纳入计划；保存的解析副本不被声称为原字节归档。 |
| registry 与 key | campaign.create/load 要求新 casebank marker、kind、run 路径和完整原计划；旧两题登记册不能收编。已有未结或未完成阶段阻止新建；active 所有者、跨进程 lease 和原子预留约束实际派发。工作台/key 路径必须在准备根内对应子目录，key 长度/hash 和 scope 再核对。 |
| budget | 脚本没有默认 token 总额或真实路径。外部 budget 必须包含全部冻结字段，四个任务共享 stage 调用与 exposure，不为每题重置。每题 17 calls/16 queries/12 candidates/1 final/attempt 1，四题至多 68 calls 是结构上界，不是本文批准的实际调用数。未知调用继续占原预留。 |
| 派发与恢复 | 只有 run 构造 Gateway；构造本身只解析本机可执行文件配置，实际调用仍在已有租约、原子预留和唯一 dispatch 标记之后。status 无 Gateway/工作台 execute；reconcile 只走 saved-only 验证和原应用结算，不构造 Gateway、不执行新工作台动作或计算核。保存恢复可能写回原结算状态，不能把它描述成所有文件均只读。 |
| 停止与重复入口 | paused、unknown、等待应用、预算不足、非当前 active 等状态在调用前阻止推进。既有终态阶段再次 run 没有新任务或调用；run 不创建下一阶段。准备部分失败保留现场，不自动删除、重建或换任务。 |
| 公开上下文 | 仍由冻结的 casebank live/工作台公开合同生成完整本任务历史，不套旧 submit_diagnostic 协议。新计划内的 key 路径、验证材料、准入信息不因存在于控制计划就被加入研究者 prompt；没有另一任务历史或私有种子、标签、参考解注入。 |

模型请求固定为 Astra/xhigh。保存请求、回答和来源绑定不提供供应商实际后端身份认证；actual provider identity 仍未独立确认。该控制范围不覆盖其他账户进程或旧 registry 外的调用，也不构成操作系统权限认证或绝对供应商账单上限。

## 独立发现与最小修复

**原问题 1：直接 Python run 的空准入 hash。** 初版的 `run(..., expected_admission_sha256=None)` 把 None 传给通用 `_json(expected=None)`，后者把它解释为不要求字节 pin。正式 CLI 的必填字符串没有这一入口，但函数 API 的显式外部 hash 合同过宽。作者在改源前保存了原脚本和实际失败的窄反例；反例在控制器加载哨兵处失败，不是发生了一次真实付费绕过。

**原问题 2：准备材料的 null receipt hash。** 同一默认语义也作用于 verification_receipts 的 `sha256:null`，能跳过所提供验证回执的字节核对；该材料自身不能授权 run。第二个反例先对只修 run 参数的中间版本保留失败，再收口准备入口。

最终修复仅增加 `_pin`，要求字符串和 64 个十六进制字符，规范成小写。run 的 plan/admission pin 在控制器加载前检查；_load 本身检查 plan pin；prepare 在读取显式验证回执前检查 receipt pin。未改变预算、固定任务、Gateway、Store、恢复协议或研究模块。

| 已保存批次 | 真实结果及用途 |
| --- | --- |
| entrypoint_author_001 | 初版完整 fake 入口场景 1 passed，9.04 秒。作者回执明确来自已完成 exec，未另存原 pytest 日志；不将它描述成审查者重跑。 |
| entrypoint_parameter_before_001 | 对初版运行参数反例 1 failed；原脚本、input_hashes、JUnit 和日志保留。 |
| entrypoint_parameter_after_001 | 第一次最小修复后，原入口场景加参数反例 2 passed，9.50 秒；中间版本和凭证保留。 |
| entrypoint_parameter_before_002 | 对中间版本加入准备材料 null 反例，1 failed，4.21 秒；原脚本、JUnit 和日志保留。 |
| entrypoint_parameter_after_002 | 最终版本原入口场景加完整参数反例 **2 passed，9.27 秒**；审查者解析 JUnit 确认 2 tests/0 failures，并核验输入及日志/JUnit hash。 |

最终入口场景实际检查四个独立 baseline、准备目录不可复用、错 plan 在 Gateway 构造前拒绝、准备记录不能授权、显式合成准入、四个 fake final 各一次、status/reconcile/完结后再 run 不新增调用。参数反例覆盖 plan/admission 的 None、空字符串、错误长度、非 hex、非字符串，以及准备回执 None，在控制器/材料读取哨兵前拒绝。无需因这几处参数检查重跑此前 15+6 项长组。

## 最终来源与证据

下列路径均相对 `experiment_traces/meta_ashare_revision15/`。报告编写时逐项核对当前文件及回执引用的 artifact SHA。

| 文件 | SHA256 |
| --- | --- |
| scripts/run_casebank_reference.py | `d9796523fd2724491ce40a382981d24aafbb680de24e6de54989283fa6603cdb` |
| tests/test_meta_casebank_reference_cli_v15.py | `365739069cf023af6e7cbf46c28ddfa2eb3524c4821053d06ad3a4e2e22c8170` |
| tests/test_meta_casebank_reference_pins_v15.py | `4b3d322d105cb1af902be5f9e3fab0e8db467af80d77ad77af5808b6e8410ce2` |
| src/quanta_agents/meta/casebank_campaign.py | `4e56a278715b672d422c4aebe7468217c58920640c8f0b820efcb11c84e5d02a` |
| src/quanta_agents/meta/casebank_live.py | `8bd36896ed1445cae29addf3f7decabad05c171367ee60240c4f9a0603074a60` |
| casebank_engineering_attempts/entrypoint_parameter_after_002/receipt.json | `025f330663fd11edb9fb19508efc3e2c41ffe6e04149e4b4e93e47bf175a50bf` |
| casebank_engineering_attempts/entrypoint_parameter_after_002/junit.xml | `7b0090e51b339c3e5851dcb468f35ffded28786611b9c3fc41e9bf7e69a0c132` |
| casebank_engineering_attempts/entrypoint_parameter_before_001/preserved_run_casebank_reference.py | `28d2e5d667d3f004ce2e172a46a3969057ca1e769ff929caf4fb07a7a61738c8` |
| casebank_engineering_attempts/entrypoint_parameter_before_001/junit.xml | `f6c91e5caf28fd8e0486a4e1b3134b049e408d32448e968b6e8b18d0aa4f6e17` |
| casebank_engineering_attempts/entrypoint_parameter_before_002/preserved_run_casebank_reference.py | `699ec8b6017a06730cd4dc2326ba577ca82787cb0f1e783898ccadb74ae730c4` |
| casebank_engineering_attempts/entrypoint_parameter_before_002/junit.xml | `60bb88b9fdc54003d8cb1bb0440e7c70c3ec2a01e7b84880caa1a32b976642bf` |
| casebank_engineering_attempts/independent_001/receipt.json（此前独立 6 项） | `cc2cd378f519552793652a16746635636f739b3b33e18be6040f003993532847` |
| casebank_engineering_attempts/author_001/receipt.json（此前作者 15 项） | `cbad490d188871a7e543c9c21d6932874bb505d84d67a8f1b8ed2c0808b406ed` |

## 两问自检及实际准入边界

**这一步做得怎么样？** 独立阅读发现了两个同源的显式 hash 默认值漏洞，保留真实反例后由作者最小修复；核心付费、预算和保存恢复实现保持原样。最终通过是有界入口工程结论，真实研究调用新增 0，原失败未删除。

**下一步该做什么，如何改进？** root 用新的组合验证回执固定实际来源、四题公开 manifest、预算和唯一新身份；准备后检查具体完整计划，再保存绑定其 plan/source/script 的真实准入文件。原作者 15 项回执没有 passing status 字段，不直接作为 prepare 的材料，应通过组合凭证准确纳入其验证事实。实际运行保留失败、弃权、预算停止和未知分母，不自动替补任务或另开阶段。四个强参考者的启动与合法报告，也不能直接证明候选发现、架构稳定性或收益优势。
