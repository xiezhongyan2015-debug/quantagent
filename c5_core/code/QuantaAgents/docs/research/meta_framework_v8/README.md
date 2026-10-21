# V8：有依据的接口演进

本轮入口：`docs/META_FRAMEWORK_V8_CONTINUATION_PROMPT_20260909.md`。独立判断与架构图见 [DESIGN.md](DESIGN.md)；原轨迹、因子与归因分别见 [trace_review.md](trace_review.md)、[factor_review.md](factor_review.md)、[validation_review.md](validation_review.md)。最终结果见 [ACCEPTANCE.md](ACCEPTANCE.md)。

V8 当前是现有程序中的可选 `research_profile="v8"`，状态版本 `meta_v8.0.0-dev`；包目录仍是 `meta_v7`，基础包版本保留 7.0.0。旧配置默认 V7，旧结果可读。新研究必须使用新目录，不向已经关闭的 V7 目录写配置或恢复研究。

## 实际实现

- `decision_evidence.py` 从保存报告生成角色视图，均值不重算。共享列名和样本表保留缺失、覆盖、秩亏、年度正负/极值及最小年度样本。全量 selection 和定向 `ids` / `roles` 接口不按 IC 筛掉弱因子。`detail="review"` 可读完整年度与离散度，须定向使用，不能把其全量直接塞进 32KB。
- `revisions.py` 绑定一条已完成的父运行与最新复盘，继承完整冻结规格，只接受复盘声明的字段变化，保存原/新规格身份和实际差异。`score` 的正缩放虽是语法变化，未必改变排名信息；差异域不代表因果识别。
- `controller.py` 和 `protocol.py` 将两项能力接入现有 API/CLI、阶段限制、恢复与模型入口。最新 revise 复盘后的自由新提案被拒绝；先用 reject 结束旧假设仍可提出独立新机制。这个契约约束已声明变化，不判断模型写的机制是否真实，也不保证没有过拟合。
- 风险控制提示标注先归一化再逐股截断造成的现金效应；批级共享解释避免重复上下文。没有改变分配器、成交、价格或原模型回答。

## 使用方式

在新研究的冻结配置中增加：

```json
{"research_profile":"v8"}
```

其余 split、项目目录、数据授权及预算仍使用现有完整配置合同。初始化和执行仍由 `scripts/run_research_v7.py` 提供；文件名不等于研究 profile。现有 `V7ResearchKernel` 类继续作为 API 入口。

复盘示意（ID 必须来自本次实际完成的批次；不是可直接运行的真实研究配置）：

```json
{
  "action":"review_batch",
  "reason":"在同一排名和持股范围下检验配置政策",
  "payload_json":"{\"batch_id\":\"<completed_batch>\",\"verdict\":\"revise\",\"conclusion\":\"当前风险增益与仓位变化尚未分离\",\"revision_plan\":{\"parent_run_id\":\"<completed_parent_run>\",\"allowed_paths\":[\"/allocation/weighting\",\"/risk_score\"],\"hypothesis\":\"比较两种权重政策的总体差异\",\"falsifier\":\"成本后差异不足以支持进一步检验\"}}"
}
```

后续 `revise_batch` 只提供 `parent_run_id`、`review_batch_id`、`changes:[{path,value},...]`、可选 name/controls。软件继承其余参数。允许路径是 `/score`、`/gate`、`/risk_score` 或明确的 `/allocation/<field>`；允许整个表达式根意味着允许其内部变化，软件不会把一段自然语言“仅改风险”误读成 epsilon 不许改变。该约束不是新的经济因果证明。

## 本轮可复现入口

在 `/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents` 下：

```powershell
.\.venv\Scripts\python.exe scripts/audit_meta_v8_saved_trace.py
.\.venv\Scripts\python.exe scripts/replay_meta_v8_context.py
.\.venv\Scripts\python.exe scripts/verify_meta_v8_release.py --output output/research/meta_v8_20260909/model_pilot_001
```

前两个命令只重放已保存证据，写入本轮新目录，不重算市场账户；第三个校验原始源码归档、保存结果、四次回执及明确记录的评估合同修正。原始评分已保存在report.json；未来pilot提示修正后，旧runner的严格源码pin检查会拒绝直接再运行旧目录，这是预期行为。`recover` 在未变更源码的研究中仅离线恢复保存会话，零模型重问。不要将旧pin失败当作重开模型预算的理由。

实际试验协议、prompt、schema、隐藏生成验证值、真实训练来源哈希、原模型会话和失败保存在 `output/research/meta_v8_20260909/model_pilot_001`。最初准备目录 `model_pilot` 在零调用时因上下文集成修改而废止，原件保留；它不是额外研究重复。

四次真实调用已关闭。生成题两模式同为4个预设来源、1个误报、3个正确弃权；V8总token增加15.90%。真实归因一致，但V8规格题因pilot遗漏路径语法约定而不适合做相对接口评价；原失败和修正证据保留。生产V8源码未因该评估缺口扩大动作权限。本轮未证明V8的发现或效率优势。

## 本轮暂缓与下一步判据

完整跨研究知识检索、持久化行为索引、时序状态诊断、更多日历/Alpha101/191适配和网页均未实现。当前真实 V7 只见一次研究，最明确的无价值查询发生在同一研究内；先修复该证据传递，再判断跨研究检索收益，避免仅因有共享账本就宣称知识复用已经完成。

若要继续检验弱因子的实际组合增益，应先冻结新研究的少量候选、对照、成本/仓位口径、预算和停止条件。不能按已曝光 2021–2024 的年度盈亏造规则再称样本外。2025 数值授权没有扩大。没有独立市场留出、真实执行认证和更广机制重复时，工程和接口通过不能升级成框架一般发现优势或金融成功。
