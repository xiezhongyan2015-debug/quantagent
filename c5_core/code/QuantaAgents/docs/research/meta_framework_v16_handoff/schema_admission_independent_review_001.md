# v16 schema 准入独立复核 001

本轮结论：所审 v16 本地 schema 检查及其 Gateway / casebank 接线有界通过。修复后的唯一受影响回归为 **51 passed / 23.52s，0 failed、0 error、0 skipped**。这证明本地检查位置、公开 horizon 语义及机会账务的离线行为；不证明供应商已经接受新 schema，也不授权重发原 v15 调用或启动新付费阶段。

评审沿用 Astra / xhigh。新增研究 Gateway 调用 0，供应商 API 探测 0；使用公开算术 fixture 和本地假进程，没有市场读取、真实四题候选发现或回测。工程评审本身的模型 token 计量未知，未写为零。

## 已证根因与本次边界

原 v15 远端记录只给整份 `text.format.schema` 的 `invalid_json_schema`，未指明关键词位置。标准 JSON Schema 可接受原数组值 enum；它仍可能超出供应商实现。详细已证事实、假设和不确定性见 [原失败根因报告](../meta_framework_v15_handoff/reference_schema_failure_review_001.md)，SHA `ab63780423e7f70603ae281e0d6a29a802a42c3ac1447812b4eb4972ee8ffd97`。

本轮 `local_scalar_enum_v1` 明确是保守的本地检查：检查有限 JSON 和 schema 位置的 composite enum，返回 `provider_certified=false`，没有宣称完整 JSON Schema 校验或供应商编译器认证。此前成功请求已有的长度、数组长度与数值范围约束保留。普通名为 `enum` 的属性，以及 `const/default/examples` 中的实例数据，没有被误当 schema 递归拒绝。

## 接线与账务复核

1. `CodexGateway.run` 在任何 CLI `_preflight`、调用文件生成和模型 `Popen` 前执行检查。根代理反例同时把 preflight、Popen、事件入口设为禁止，原失败 schema 被拒时没有触及这些入口，也未生成调用目录。新检查只插入新调用入口，未插入保存回答验证路径。
2. `casebank_live.execute_task` 在原 dispatch lease 内生成完整本侧上下文，检查 schema，再调用 `reserve_call`。本地拒绝以 `admission_stopped` 暂停，调用列表为空、未结数和预留为零、Workbench 未登记动作。这是尚未启动的新准入拒绝，不是把原有未知调用改成免费或删除失败分母。
3. Gateway 的第二层检查仍在付费进程前。如果在已预留之后出现异常，原 `save_failure` / 暂停路径继续保留未结账务；本次没有增加释放预留、降低 usage、自动重试或换 campaign 的分支。原 80,000 未知预留不在本检查的释放权限内。
4. `structured_output_profile.py` 同时加入 campaign 与 Workbench 的 source 身份。Workbench 升至 v3，公开 overlay 给出精确有序 `[1,5,10]` 及失败仍消耗机会的规则，原题包不被改写。实际发送 schema、prompt、intent 和来源仍经原绑定链保存。
5. transport schema 允许三个整数项；本地动作语义只接受原有的精确 `[1,5,10]`。26 个其他三元组分别经过 transport 输出验证后，送往两个诊断动作，均形成保存的失败机会：合计 52 个动作、每项三个 requested sub-attempts，未进入 kernel。没有排序、去重或补齐。错误长度、bool、float、越界值，同槽恢复、改写同槽拒绝、新槽重复计数和正常 query / 两诊断 / final 链亦有定向覆盖。

对 v15 / v16 相关函数做 AST 比较，以下实现均相同：`verify_saved_completion`、`_validate_output`、`_intent_binding`、`reconcile_saved_only`、`reserve_call`、`mark_dispatch_started`、`apply_intent`、`apply_completion`。本次没有把新 profile 追溯应用于旧完整回答，也没有让恢复构造 Gateway 或执行未应用的动作。旧工作区仍须通过它原有的 source / scope 身份，不能因函数不变而跨版本冒充同一 workspace。

## 独立反例与最小修复

独立检查发现初稿按原 Python 值的 `type` 遍历：`enum=[(1,5,10)]` 会被 `json.dumps` 写成 `enum=[[1,5,10]]`，却绕过仅识别 dict / list 的 composite 检查。现有 Workbench 字面 schema 没有 tuple；这是本地检查面对等价发送 JSON 时的不一致，不能据此声称 v15 的真实调用来自 tuple。

先保存真实失败，再由作者修改 helper。独立反例 **1 failed / 4.31s**，包含原 helper 副本、输入 hash、JUnit 和 stdout。作者只把遍历入口改为 `json.loads(raw)`，检查实际序列化结构，原输入保持不变。原反例未经修改，在最终 51 项中通过。

| 证据 | SHA-256 |
| --- | --- |
| `experiment_traces/meta_ashare_revision16/schema_independent_validation/001_before_fix/receipt.json`，原 1 fail | `670e3e2184bf87373d47e06417087efe4479fa7306344428a5e39f0cb90183b6` |
| `experiment_traces/meta_ashare_revision16/schema_independent_validation/002_after_fix/receipt.json`，最终 51 pass | `6c43e30fe3430bae99c8bcbb0774d078b72c3dff80cea2a93f3ed0df7ee4417c` |
| `experiment_traces/meta_ashare_revision16/schema_independent_validation/003_readonly_review/evidence.json`，哈希及 AST 核对 | `860be5be88ab9e18c4213fd486e2eee9142fd919ee629591f6ffb036c0fafaa0` |

JUnit 实际分组为作者 transport **46**、root admission **4**、独立 wire-equivalence **1**。执行前后 5 个源码文件、3 个测试文件 SHA 全部稳定，JUnit / stdout / stderr 已逐项复核。

根代理早先的 `schema_admission_validation/002_after_fix/receipt.json` 为 **95 pass**，SHA `19660842e0a1deb437e3928dd60fb4072fb88e8c02e113ef4b1019fe6681054d`，其原 artifact hash 均复核通过。它使用旧 helper `19741…`；当前只有 helper 与该组已记录源码输入不同。此次没有重复运行其中旧 Gateway / terminal 的 91 项。验收可组合使用原 91 项及最终受影响 51 项，必须保留这种分次与依赖变化，不能称 95 项或 142 项在最终 helper 下同次运行。

## 最终所审源码身份

以下均位于 `experiment_traces/meta_ashare_revision16/src/quanta_agents/meta/`。

| 文件 | SHA-256 |
| --- | --- |
| `structured_output_profile.py` | `276481336f2b647717c83bb46be1def6a366fa5e1de072f61c58b1f78a8bdf38` |
| `casebank_workbench.py` | `f68e86e7d8b7fef9d1b6f246da9adf9e6078d5d70630bd36c0132318c206d3ca` |
| `codex_gateway.py` | `5afa3dcbbd8d8c6f2a649aebfc86f787cd83d54ddfb547ec2967e68c2e6b2b95` |
| `casebank_live.py` | `e76041a640fa871ebfc46e62f8b841ff61373e1d343a8496bb0e8c3970e52e3d` |
| `casebank_campaign.py` | `555d4e4d0ad0cb73979738745d9d17948c658e915573fde29e7ea42c63dfbfd7` |

原 v15 付费调用的 schema、events、request、intent、exit、prompt、stderr 七个原始文件 hash 再次匹配根因审计快照。本评审不改 v15 源码、原调用或账簿。原 HTTP 400 失败身份、usage 未知和预留继续保留；后续若另有跨阶段预算承接协议，应单独审查，不能由本报告推定已经完成。

## 两问自检

- 是否把标准合法、本地通过或假进程通过说成供应商兼容已证？没有。新 schema 的远端接受性与 actual provider identity 仍未独立验证；报告没有授权付费探测。
- 是否通过改写旧失败、少记机会或恢复重算得到通过？没有。原付费文件与独立修前失败保留，错误 horizons 仍记失败及子项，恢复实现未改；最终结论限于这一有界本地修复。
