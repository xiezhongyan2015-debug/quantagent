# v10 两题付费诊断接入：独立工程审查 001

审查角色：独立 Astra / xhigh；未参与本次 campaign / live / actions / inputs / runner 实现。审查日期：2026-09-07（Asia/Hong_Kong）。只读实现及已保存开发材料，仅新增专属反例测试与本报告。未调用研究模型、网络、回测或封存市场数据。工程审查自身模型用量未由此研究账本计量，不能写成零。

## 结论与准入条件

**对下列冻结代码给出有界工程通过。** 审查中发现的原题身份绑定 P1 和调度状态显示缺陷已由文件负责人修复；最终差异已复读，没有其他已确认的重复付费、预算绕过、丢失已观察用量或跨题上下文泄漏阻断。

这份代码结论本身不启动付费。正式 `prepare/run` 前必须已有同一源码、脚本清单的 `status=passed` 离线验证回执，覆盖本报告的 8 项独立反例；主控再生成 `accepted_for_two_exposed_diagnostic_cases` 的追加准入证，绑定该回执 SHA256、本报告 SHA256 与完整源码/脚本清单。runner 在 prepare 验证这些字段，在后续 load 固定验证回执和准入证的文件哈希。失败或哈希变化不能转为 ready，也不能沿用此结论启动变更后的代码。

本结论只覆盖两个已经暴露的开发诊断题、controller JSON 接口和禁工具的独立 CLI。它不证明模型已经发现诊断、不证明供应商身份已独立认证、不证明操作系统隔离、执行有效、策略收益或架构优胜。旧服务、其他账本、外部账户不属于此 registry。不得将这两题纳入策略成功率或隐藏机制测试分母。

## 实质发现及整改复核

### P1：原来只固定目录和当前自洽哈希，未固定已经验收的题目

原 `prepare` 验证 prototype receipt 文件本身的哈希，随后直接对固定目录调用 `input_manifest`。如果在准备前合法修改 packet 并重新计算 `public_package_hash`，新的自洽内容可被再次冻结，却仍引用旧题的语义验收。只修改 evidence 文件而保持 packet 不变也存在同类缺口。文件路径不等于已接受内容的身份。

现 `run_live_diagnostic_calibration.py:106` 的 `accepted_inputs(case_id)` 先核对已接受 receipt 原始 SHA256，再要求 `results` 唯一 case 对应的 `public_package_hash` 与当前 packet 相同；之后对包含所有公开输入文件 SHA256、字节数和总量的完整 manifest 进行独立常量核对。prepare 仅通过该入口取得冻结输入；恢复继续对照 plan 内完整 manifest。接受值为：

| 题目 | 已接受 public_package_hash | 完整公开输入 manifest 哈希 |
|---|---|---|
| calibration_01 | 58b06b2b84b858aa9255c97ecf185f02f6175f92f2b6bc1ca9d50c95cad83cf3 | c0bfc28333ef978a02743f01ba009c358e6eb3a82cc9c78aa0d83052c3bab385 |
| calibration_02 | 5c3731b1c8dab3e267a4626e8e69c83151056e00c7215d92cda700759691c977 | 309f8fb5fd631fc6006f64135fb80008f3a68c438d8f5361c948f590ba794af1 |

独立反例分别执行合法 packet 修改及重哈希、只对 observation 增加一个换行。两者先证明 `input_manifest` 自洽，再要求 `accepted_inputs` 拒绝；不会改原目录或原输出。整改静态复核已关闭此缺陷，最终动态关闭以同版回执中两项通过为准。

### 调度显示缺陷：剩余预算不足或已付费待应用时曾显示未阻断

早期 `summary.dispatch_blocked` 只考虑暂停、未知调用和运行时间，遗漏 completed 回答等待应用、pending 公共行动及不足以预留下一次调用的预算。实际原子 `reserve_call` 一直拒绝，未发现由此引发付费预算绕过；但上层不能把旧字段当成准确的 ready 许可。

现 `diagnostic_campaign.py:519` 同步报告已知阻断原因，包括当前题/round 状态、已占用槽位、pending 行动、未知用量、全局/题内调用数、下一次 80000 token 预留不足、暂停、运行时限与 registry 身份。独立反例用正常结算且应用完成的 query 推进到 round 1：已用 520000 时允许再预留；520001 时题目逻辑状态仍 ready，但调度阻断且实际预留拒绝。summary 明示这是快照，仍需独占 lease 与原子入口重新核对。

## 控制链核验

1. **预算和重复派发。** Store 相对 v9 仅增加可信 `admission_check` 参数，并在原有 `BEGIN IMMEDIATE` 内、全局预算与唯一槽位插入之前执行；没有放宽既有检查。campaign 的 registry 所有者、跨对象/跨进程 dispatch lease、题内轮次与唯一 attempt=1 共同约束调度。先持久化全身份 intent 和 80000 预留，再写 `dispatch_started`，最后启动新 CLI；已经占用的 round、未知结果、pending 行动不会自动重试。每题至多 16 次查询和 1 次最终回答，至多 17 个模型调用；两题 34 个调用、每题 600000/总 1200000 nominal token 准入上限，300000 为题内软提示。
2. **费用与回答。** `record_usage` 在事件回调中立即持久化已知非负计数，返回阶段再保存已观察用量，然后验证原始完成证据。失败合并保留已有最大计数；cached/reasoning 为子计数，不重复加到 I/O 总量。只要结果或最终 I/O 不完整，保留完整预留并阻断后续调用。最终 usage、JSON/schema、请求 intent、原始事件、响应与独立 process-exit 证据验证后才能结算。不可用不等于零；此为 nominal 准入及未知风险预留，不是供应商账单的绝对上限。
3. **崩溃恢复。** live 校验 paid round 连续性、action intent、状态哈希链及保存页；未解决的尾部不能后接新付费。`reconcile_saved_only` 不构造网关，不调用 query view，不重新查询；仅复核已有完成证据与保存页，或幂等导入已验证最终回答。完全未知请求没有恢复性重付；用量不足或证据变更保持暂停。无效外层行动/最终诊断仍保留已完成调用的费用记录，不能免费替换，也不会自动再问一次。
4. **在途暂停。** 正常暂停是下一次准入和回答应用的边界暂停，不主动杀掉在途请求；在途 usage/回答仍保存。保存的是 final 时，可由 saved-only 恢复幂等应用；保存的是尚未执行的 query 时，明确返回 `saved_query_not_applied`，保持零查询且不重付。已预留但未知的 query 返回另一状态 `query_result_unknown`。两者均不能冒称完成恢复；`resume` 仅改变许可，不能重放旧付费槽位。代价是尚未执行的 query 需要另行明确处置政策，当前不会自动继续该题。
5. **研究器上下文。** `public_model_prompt` 只拼接本题普通包及验证过的本题公开查询记录。actions 限定公开 table/observation/cursor/limit 与一项最终报告；无任意 Python、SQL、文件或网络输入。inputs 只读公开 allowlist，先验证字节、包、范围和 lineage，再创建全新 run/case 绑定和全新 workbench journal。两题共用中性 observation_01 不会覆盖独立 task/package/scope 身份。公开页引用外层 evidence_id，不能将其他题的 evidence 当作本题证据。
6. **CLI 与评审材料。** gateway 每轮启动全新临时目录的 ephemeral CLI，关闭用户规则/配置注入、项目文档、工具、MCP/agents/web；禁用或异常工具事件导致拒绝。请求只传上述公开 prompt/schema；不恢复父线程。runner 新增 `EVALUATION_DIMENSIONS` 和 readiness gate 保存在 controller 计划，未进入 public prompt 路径。评审不通过强制参考措辞判答案，也不把确定性脚本可达性写成模型已自主发现。供应商 model/effort 为冻结请求身份；本地 lineage 哈希不是独立供应商认证。

## 独立反例与验收证据

专属文件：`experiment_traces/meta_ashare_revision10/tests/test_meta_diagnostic_live_review_v10.py`，8 项参数化后用例，SHA256 `0b018b8f66ca878fdfbb298348608d3f82e27c3647e6e468be31e7c83f187e9e`。

覆盖：两个预留金额边界；已完成待应用及 pending 非调度就绪；重开 SQLite 后较小失败用量不抹掉已知 I/O/子计数；fake 进程在回执提交前崩溃仍保留事件用量及未知预留；在途 query 暂停与重复恢复/恢复许可不重付；两个原题身份变更反例。测试中的 provider 进程明确为 fake，合成账本回执不声称供应商真实性。

本独立代理未运行付费或大范围测试；已将冻结文件交主控统一运行。最终结果由对应追加验证回执及准入证记录，不在本报告事前捏造通过数。已有组件测试报告有助于定位审查，但不能代替上述当前版本的准入回执。Store 涉及共享原子入口，主控决定冻结后做一次 meta 联合验证；本审查不要求机械重跑无关市场研究或执行回测。

## 审查版本

以下 SHA256 为最终复读时文件字节；覆盖模块保持原来冻结状态，本次未改执行器、runtime、gateway 或任何旧输出。

| 文件（相对 meta_ashare_revision10） | SHA256 |
|---|---|
| src/quanta_agents/meta/diagnostic_campaign.py | 9ce41fff24d71099edfaa8f037b67906bc56c63878b2d61cadb9bf4ee7e29dfb |
| src/quanta_agents/meta/diagnostic_live.py | 9c84be10746f88b5ca854e561c03ea2fe6ac787ccc137e4cf341f190acfb9992 |
| src/quanta_agents/meta/diagnostic_actions.py | 0c143a37a64e64d182b793b82b8e8304adc4a426d2544976fefe90c388785b74 |
| src/quanta_agents/meta/diagnostic_inputs.py | eb443cb96b3b879c38e108616aee88fbfb233370f51955f72f7dd0df648fa864 |
| src/quanta_agents/meta/store.py | a722ca5256125f3eb10cedce51e3324f9eea8740672a6152597c795c3e2e9929 |
| src/quanta_agents/meta/codex_gateway.py | f4b7d865ce2de39765324cad1afcd2a2f42cd856514eac997c2845ebb9f20e99 |
| scripts/run_live_diagnostic_calibration.py | a864e38ac0f8f4b495f04045290a42a3875a4bd09e9bd4429f2c53184def04db |

## 两问自检

**当前判断是否把可运行、测试通过或本地哈希误当成更强证据？** 没有。结论限定控制器工程边界；反例通过由真实追加回执确认。本地请求身份与供应商独立认证分开；已知费用、未知预留和工程用量分开；两题诊断与策略成功分开。没有删除失败题、遗漏成本或缩减研究分母。

**下一步是否能取得必要新证据而不重复付费或放宽结论？** 可以。先用冻结的离线验证和本报告生成精确版本准入证，再在已授权范围内启动这两题；完整保存每个合法终止、失败、未知与预算停止，终稿冻结后做独立语义评审。若出现未知回执，只走当前 saved-only 流程；不能以恢复名义重复派发，不能把未执行 query 写成成功恢复，不能跳到真实执行有效或新 alpha。
