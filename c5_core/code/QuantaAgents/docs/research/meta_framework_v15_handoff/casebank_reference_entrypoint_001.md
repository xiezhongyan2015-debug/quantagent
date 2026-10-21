# v15 四题参考入口工程 001

新增薄入口 `scripts/run_casebank_reference.py`，分开 prepare/run/status/reconcile。它复用已通过作者 15 项和独立 6 项的 CasebankResearchCampaign、CasebankWorkbench v2 和研究循环，没有修改 src、旧协议或题包。仅执行一个四份公开算术包的 fakeGateway 入口测试，**1 passed in 9.04s**；没有对四份实际原包运行 prepare/run，没有创建真实研究阶段或调用真实模型。

## 四个命令的实际边界

| 命令 | 必要输入和行为 |
| --- | --- |
| prepare | 新 preparation_root、固定 project registry、原 public package_root/manifest SHA、新 run_id、外部完整 budget JSON 和带原 SHA 的验证回执列表。固定 case_01 至 case_04，每题一个 baseline 主研究者，独立 task_id/key/Workbench/scope；新建目录不可复用。没有 Gateway 构造。 |
| run | 必填 campaign_root、expected-plan-sha256、独立 admission-receipt 及其 expected-admission-sha256。核对原计划、当前入口源码、固定 source 和准入身份后，才显式构造 CodexGateway，交给现有唯一 registry dispatch lease/原子准入。 |
| status | 必填 campaign_root 和 expected-plan-sha256；仅读现有新协议控制器状态，不构造 Gateway。 |
| reconcile | 同样强制原 plan SHA；仅调用既有 saved-only 恢复，不构造 Gateway、不执行任何新工具动作/模型。未知边界立即保留并停止。 |

主程序使用明确子命令，不在 import、读取状态或恢复时派发。run 只访问这份既有阶段的固定任务列表；遇到不明确/暂停/不足预算即停止，不调用 create，不创建下一 campaign，也不自动换任务补跑。已终态阶段再执行 run 返回空新增结果，不能重复研究。

准备过程保存 `controller_keys/`、`workbenches/`、`verification_receipts/`、`prepared_plan.json` 和 `readiness.json`。keys 使用独占新文件，计划只记录路径和 SHA，公开研究 prompt 不出现密钥；文件 mode 参数不等同于独立 Windows 用户 ACL 认证。receipt 记录保存所提供原文件路径、原字节 SHA 及解析正文；原验证文件本身仍应按既有 append-only 规则保留，不能把解析副本声称为原字节归档。

准备部分失败时保留已产生文件和 `prepare_failure.json`，不自动删除或重新准备。成功也不会自动获得派发许可：readiness.status 为 `prepared_only_pending_independent_admission`，`real_dispatch_authorized=false`；这份准备记录不能作为 run 的准入回执。

## 预算与独立准入

脚本没有默认阶段总 token 额度，没有写死 240 万或别的未经审阅阈值。`--budget-json` 必填，原样交给已验证的新协议校验：每 task 固定 17 call/16 query/12 candidate/1 final/attempt 1，stage 的 max_calls、hard_tokens、per_task_hard_tokens、reserve、soft、两个 runtime 和完整上下文 byte cap 均须完整提供。四题实际预算仍由 root 后续独立冻结与审阅。本轮测试中的数字是临时算术 fixture 的测试输入，不是下一真实阶段的审批结果。

验证回执列表 JSON 形状为 `[ {"path": "原验证回执路径", "sha256": "原文件字节SHA256"} ]`。prepare 校验原字节 SHA 和 passing 状态并将其纳入计划；它不自动推断任意来源 passing 记录已经完成整个阶段审批。独立审查须核对实际 src/tests/scripts 冻结、验证范围与预算一致性。

run 所接受的独立准入回执至少包含：

```json
{
  "kind": "casebank_reference_dispatch_admission",
  "plan_hash": "已准备的完整计划content hash",
  "ready": true,
  "real_dispatch_authorized": true,
  "entrypoint": {
    "version": "casebank_reference_entrypoint_v1",
    "script_sha256": "这份脚本实际SHA256"
  },
  "source_hash": "计划中的十模块执行来源hash"
}
```

同时必须从命令参数给出这份回执本身的原字节 SHA。完整 plan_hash 已绑定任务、key fingerprint、普通包、预算、source、验证记录与入口脚本身份，不是仅核对批次名称。该文件机制提供内容和范围核对，不是操作系统权限隔离或对人工授权来源的自动证明；真实付费仍必须有用户/root 的明确授权。测试用 admission 文件明确标记为模拟，不能拿来运行实际研究。

入口调用形式如下，全部路径和 hash 都应在独立冻结后填写；这里没有执行这些命令：

```text
python run_casebank_reference.py prepare --preparation-root <new-dir> --registry <fixed-project-registry> --package-root <frozen-public-attempt> --public-manifest-sha256 <manifest-sha> --run-id <new-id> --budget-json <reviewed-budget.json> --verification-receipts-json <pinned-test-receipts.json>
python run_casebank_reference.py status --campaign-root <prepared-campaign> --expected-plan-sha256 <plan-sha>
python run_casebank_reference.py run --campaign-root <prepared-campaign> --expected-plan-sha256 <plan-sha> --admission-receipt <independent-admission.json> --expected-admission-sha256 <admission-file-sha>
python run_casebank_reference.py reconcile --campaign-root <prepared-campaign> --expected-plan-sha256 <plan-sha>
```

## 来源变化和本次测试

新脚本作为 plan.entrypoint 单独冻结并在每个命令核验。十模块 source_hash 和每个工作台 scope 的构造规则未变；新增 entrypoint 和 controller_paths 等计划元数据会改变新阶段的 plan_hash，不能复用旧阶段的审批 hash。原 15+6 的 src 适用范围保持不变，未重复执行。

一个薄入口 fixture 验证：四份中性合成输入、四个 baseline 身份与不同 scope、prepare 新目录独占、错误 plan hash 在 Gateway 构造前拒绝、prepared readiness 不可许可 run、独立模拟 admission、四个实际 fake process 事件/回执各一次弃权 final，随后 status/reconcile/再次 run 零新增模型派发。它调用真实 Gateway 文件验证器，不是四份真实题包的参考发现。网络与真实进程调用被测试禁止。

`casebank_engineering_attempts/entrypoint_author_001/receipt.json` SHA256 为 `3e7e91eca743a53f5058f06f0d3aaa70afd9cd9fe1318dc3314608b9d88ff13d`。记录来自已完成 exec 24685/chunk 8bccb6/exit 0，明确未另捕独立原 pytest 日志；仅测试该薄入口，没有把之前 15+6 说成重跑。本子任务实际题包 prepare 0、实际研究 Gateway 调用 0、策略回测 0、真实行情读取 0，fakeGateway 调用 4。工程/审查模型用量非零，不能泛化为全项目期间零调用。

| 文件 | SHA256 |
| --- | --- |
| scripts/run_casebank_reference.py | `28d2e5d667d3f004ce2e172a46a3969057ca1e769ff929caf4fb07a7a61738c8` |
| tests/test_meta_casebank_reference_cli_v15.py | `365739069cf023af6e7cbf46c28ddfa2eb3524c4821053d06ad3a4e2e22c8170` |
| meta/casebank_campaign.py（未改） | `4e56a278715b672d422c4aebe7468217c58920640c8f0b820efcb11c84e5d02a` |
| meta/casebank_live.py（未改） | `8bd36896ed1445cae29addf3f7decabad05c171367ee60240c4f9a0603074a60` |

## 两问与下一步

**这一步做得怎么样？** 补上了可实际审阅和调用的四题参考入口，避免停在类 API。准备、运行、状态与恢复分离；默认没有真实路径、预算或授权，错误计划不能进入 Gateway，未知状态不能自动补任务。普通输入与公开 final 合同继续由冻结工作台负责，没有向 prompt 加私有答案。

**下一步该做什么，如何改进？** 独立作者只读核对脚本门与当前 hash，root 联合冻结整个 v15 与实际阶段预算。之后按新的明确授权执行 prepare，复核生成的具体计划/密钥存储/来源/任务列表，再由独立准入回执允许真正 run。本次没有跨过这一步，也没有任何同预算参考发现结果。

若入口在错误 plan/admission/source hash 时构造或派发模型、status/reconcile 触发 Gateway/execute、准备被当作付费授权、四个任务共享对方轨迹、预算获得未经显式输入的默认值、未知状态自动新建 campaign 或重派，则停止放行并保留反例。已有类测试通过不能豁免入口缺陷或现实的阶段审阅门。
