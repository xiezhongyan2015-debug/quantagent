# v16 输出 schema 传输兼容修复：作者报告 001

原 horizons schema 使用数组值枚举 `enum: [[1,5,10]]`。它是标准有效的 JSON Schema；当前供应商 400 的唯一原因尚未证实。本修订移除此处复合枚举，保留本地精确期限语义，并增加供应商进程前可调用的保守传输预检。通过本地预检不代表通过供应商编译，也不证明下一次请求会成功。

## 文件责任与接口

本作者仅修改隔离 `experiment_traces/meta_ashare_revision16/src/quanta_agents/meta/casebank_workbench.py`，新增同目录 `structured_output_profile.py` 和专属 `tests/test_meta_output_schema_transport_v16.py`。根任务负责 Gateway、casebank_live 与 campaign 的预检接入/来源哈希；独立作者负责 tuple 反例及最终受影响回归。v15、main、原四包、历史 campaign 和付费记录均未改。

轻量 helper 只依赖 hashlib/json：

```python
class SchemaTransportError(ValueError): ...
validate_output_schema_transport(schema: dict) -> dict
# {"profile": "local_scalar_enum_v1", "schema_sha256": "...", "provider_certified": False}
```

函数不修改输入。它先生成有限规范 JSON，并遍历该实际 JSON 中的 schema 位置，包括 properties/items/anyOf/allOf/oneOf/$defs、条件和附加属性子模式等；schema 的 enum 中出现数组/对象值即拒绝。属性名称恰为 enum，以及 const/default/examples 的普通数据，不被当成 schema enum。它不是完整 JSON Schema 校验器，不解析外部引用，不测试远端支持，也没有移除长度或数量限制。

WB 的 `action_schema()` 返回前调用同一 helper。根任务另外在 `Gateway.run` 的 CLI preflight 之前和 live 的 reserve_call 之前显式调用，以覆盖绕过 WB 的调用者；这些边界由根任务和独立组验证，不把只有 WB 调用称为网关全覆盖。

## 本地研究语义保持

期限传输字段变为 `type: array, minItems: 3, maxItems: 3, items: {type: integer, enum: [1,5,10]}`。它允许 27 个传输层元组，但 WB 在持久登记 query/candidate/子尝试后只接受严格有序 `[1,5,10]`。其余 26 个元组拒绝；没有排序、去重、补齐或改变请求。bool/float、长度不为 3、范围外数值也留下失败机会，不能免费更正。

查询上限 16、候选上限 12、唯一 final、17 action slots、三个固定期限、所有内部子尝试、字节和字段长度限制保持原值。失败或新 slot 重复继续消耗机会；相同 slot 重放只读保存记录，不能改变请求或重算。有效 inspect → 两种诊断 → final 仍可形成完整本侧公开历史。

WB 版本升为 v3，普通 overlay 明确传输规则与本地精确规则的区别、失败收费语义、`provider_certified: false`；原题包不改。source pin 新增 helper，新的 scope/schema/源身份必须重新冻结。本修订不迁移旧 workbench 或为旧未知调用重派。

## 原始失败与通过证据

1. `experiment_traces/meta_ashare_revision16/schema_transport_attempts/before_001` 保留原 WB/测试副本、stdout、stderr、JUnit、输入哈希和回执。实际 **1 failed in 4.64s**，是原标准有效 schema 不满足本地 scalar-enum profile 的反例，**不是重现供应商 400**。回执 SHA：`5dd4573b6d93031a6e3bcb045a959ff1c501732287ebb620d812a35eee064198`。
2. `schema_transport_attempts/after_001` 作者首次实际 **46 passed in 21.83s**，执行前后七个适用源码/测试输入哈希未变。覆盖 26 个错误元组分别经过两种诊断动作（52 个实际失败请求）、五种形状错误、有效四步流程、失败保存/重放/新 slot 记账、嵌套 schema 与注解边界。回执 SHA：`c8e8f8c814b187529d304c41b7abe99706dcc16ba0af93fa76ad71abe96a1082`。Gateway 本地输出校验明确接受那 26 个传输形状；WB 全部拒绝且无 kernel，证明后置语义门实际生效。
3. 独立作者保存额外 **1 failed in 4.31s**：Python `enum=[(1,5,10)]` 编码后为复合 JSON 数组，但首稿 walker 对原 Python tuple 漏检。凭证 `schema_independent_validation/001_before_fix/receipt.json` SHA：`670e3e2184bf87373d47e06417087efe4479fa7306344428a5e39f0cb90183b6`。当前字面 WB schema 没有 tuple，不能夸大为当前普通请求仍失败。最小修复将 walker 起点改为 `json.loads(raw)`，检查真正发送的表示；原 helper 副本和两次原始失败记录保留。

最终 tuple 修复后的受影响回归由独立作者一次执行：作者 46 + 根任务 4 个入口边界 + 独立 1，**51 passed in 23.52s**，源/测试前后不变。`schema_independent_validation/002_after_fix/receipt.json` SHA 为 `6c43e30fe3430bae99c8bcbb0774d078b72c3dff80cea2a93f3ed0df7ee4417c`。首次 46 回执只证明当时 helper 19741ce6… 版本，最终 helper 27648133… 以本次独立追加凭证为准；作者未重复执行。根任务原网关/终态组的继承范围由其组合验证记录说明。

## 冻结指纹与风险

| 文件 | 最终 SHA256 |
| --- | --- |
| casebank_workbench.py | f68e86e7d8b7fef9d1b6f246da9adf9e6078d5d70630bd36c0132318c206d3ca |
| structured_output_profile.py | 276481336f2b647717c83bb46be1def6a366fa5e1de072f61c58b1f78a8bdf38 |
| test_meta_output_schema_transport_v16.py | ebbb7294f576b4b9083515d64c9b8c2d25b4b8ca19a81136a05d1826c03e3774 |

没有实测远端标量整数枚举支持；复合枚举仍只是优先兼容性假设，不能宣布根因闭环。旧 v12 成功 schema 已有长度、items 和数值边界，故本修订不泛化删除这些已用约束。CLI code-mode warning 也没有被视作本次失败原因。正式新派发仍须独立准入，旧未知费用与回执不得清零或隐藏。本作者子任务新增项目 CodexGateway 调用 0、真实市场读取 0、新策略回测 0；测试内纯合成诊断 kernel 调用已按机会登记，工程/评审模型用量非零另列。

**自检一：是否用弱化规则换兼容？** 传输表达放宽，但本地精确有序规则、机会预算、失败证据与恢复边界保留；52 个拒绝路径和有效路径形成直接证据。若任何错误期限组合被归一化、进入 kernel、未计机会或能同 slot 免费修正，该结论被推翻，停止准入。

**自检二：是否把离线通过当供应商成功？** 没有。预检只覆盖有限 JSON/复合 enum 风险，标准有效性与供应商支持严格分开。若实际供应商继续拒绝，应保留新完整回执、暂停并分析，不自动尝试其他 schema 或模型；若 profile 出现未检查的 wire 复合值、源漏 pin 或可越过 preflight，则先保存反例并修复，不能以旧通过记录放行。
