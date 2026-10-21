# 保存原字段的持久会计接线 001

2026-09-07。Astra / xhigh 工程实现与根代理独立反例审查；当前完成的是有界保存数据接口和合成验证。未运行两股真实保存数据矩阵，未读取源 CSV、联网、调用研究 Gateway 或操作真实资金。工程模型用量不记作研究调用，也不冒称人工无模型审核。

**结果：可以先保存一次研究意图，再从固定哈希的保存原字段驱动现有原股数模拟；失败会保留已持久资金前缀。恢复只验保存记录，不重跑模拟。** 所有执行有效、历史实际到达认证与策略成功标志仍为 false。v14 原文件未修改。

## 最小改动与接口

根代理授权后，仅对 v15 的 `raw_portfolio_backtest.py` 增加默认关闭的 `checkpoint` 参数：包装既有 Ledger，在应用事件之前保存意图、之后保存 journal / snapshot，并保存日初、日末、普通失败和完成证据。回调失败立即停止；不会因为回调抛出 `LedgerError` 被原拒单分支吞掉后继续交易。未复制第二套交易业务核，默认费用、T+1、限幅代理、容量代理、买卖顺序和估值规则均未改。

新增 `raw_saved_research.py` 的 controller 接口：

```python
plan = freeze_saved_research_plan(
    identity=..., codes=..., calendar=..., targets=...,
    source_artifacts=..., obligations=...,
    corporate_actions=(), initial_cash="1000000.00", fixture_only=False,
)
job = SavedRawResearch.create(new_directory, plan)  # 只创建，不执行
job.execute_saved(expected_plan_sha256=plan["plan_sha256"])  # 必须显式派发
job.reconcile_saved_only(expected_plan_sha256=plan["plan_sha256"])
```

- 来源项绑定代码、controller 指定目录、`rows.json.gz` 与 `source_manifest.json` 的相对文件名及各自 SHA256。读取先核路径、文件大小和内容哈希，再限量解压；仅接受保存的 2017–2021 行，不跟随 manifest 的原 CSV 路径。保留无效字段、原始价格字符串、拒绝原因与 historical membership，不以当日 accepted 删除开盘机会。
- 目标必须使用明确前一交易会话的信号日，时间不早于该日 15:05、且不晚于下一会话开盘。总权重不超过全账户本金，末日不能新开正目标；清仓为显式零目标，失败不重分配。
- 每个代码和日期固定六项义务：公司行动、交易状态、容量、费率、可得性及 membership。未知、待查和不支持项在该日日初停止路径；不会把未知公司行动假设成没有事件。`fixture_complete` 只准用于明确合成夹具。来源范围说明和模拟假设均不升级旧认证标志。
- SQLite 以事务保存计划、唯一派发意图、输入验收哈希、检查点与终态；同目录非阻塞锁防止双实例重复派发。完整初始资金、现金、股份、费用、权利和拒单保留在原账本和进度中。

## 失败与恢复的精确含义

事件前写失败不进入 `ledger.apply`。事件应用后保存失败时，只能依赖已落盘的前缀；未结事件意图保留，不能推测它已提交或补做。若写入实际已提交后才报告错误，恢复可核到真实已保存的资金前缀，仍保持失败状态。日末缺价不删持仓，不把失败日伪造为全现金或完整 NAV。

`reconcile_saved_only` 仅读取本目录计划 / SQLite，并核对回放账本内核身份、保存哈希链和每个 ledger 前缀；不读取原输入数据、不调用 portfolio、不补估值。即便已保存 `simulation_completed`，缺少正式 terminal 仍返回 `interrupted_saved_only`，不自行升级完成。这里的 interrupted 是保存证据缺终态，不是进程存活探测。

显式重复 `execute_saved` 会重新核绑定来源哈希，然后只返回保存结果；外部行或 manifest 变化不能借旧成功放行。单独 saved-only 仍可查看原绑定的历史结果，并明确 `source_artifacts_reopened=false`，不宣称外部来源当前未变。变更义务、目标、源身份或引擎必须另行冻结新计划，原尝试不可覆盖。

范围冻结为最多 2 股 × 16 会话，64 个事件意图。单条保存记录最多 4 MiB、累计 payload 最多 128 MiB；下一事件准入先留出 4 MiB 的事后记录和 4 MiB 的终态空间。未结写入保留 4 MiB 预留，超限停止，不能重跑。完整 journal / progress 检查点仍有近似平方增长，以上小范围限制是明确取舍；当前不支持大型扫描。SQLite 文件自身的页和索引开销另于 payload 额度，不把该额度冒称精确物理磁盘大小。

## 保存的机械计划：尚未运行

目录：`experiment_traces/meta_raw_saved_research_v15/preparations/20260906T201624557622Z`。

- 计划内容哈希：`805137e60b36041f31f2958c2541c98daa52137b69b9bcbfd8fd6801e0ac6dc2`。
- `plan.json` 字节哈希：`dbe5c0ccbbf3bde5ca48167800af0e71a4acfa40e5fd4992a240cf68be95c318`。
- `preparation_receipt.json` 字节哈希：`20f33be64994a2cf6a29e30c7672581c5433c825afc5e48ff62c28d0bcd2f41d`。

父原计划前两股 `sh600004 / sh600006`，2019-06-20 至 2019-07-11 的既有连续 16 会话，第一日作滞后输入；2019-06-21 各 0.5 目标进入，2019-07-02 显式零目标退出，完整初始现金 100 万元。没有收益选择、替换失败股票或搜索参数。两股是公开机械接线分母，**不是已证明的 2019 CSI500 可选池**；平安银行的另一份现金分红对照未加入该计划。

脚本只读父计划及两股 receipt 来固定原件哈希，没有打开两股 rows 或 source_manifest，更未执行模拟。SQLite records 实际为 0；状态 `prepared_not_run`，**192 项义务全部 pending**。该 metadata-only 准备发生于 20:16:24 UTC，在根代理“准备脚本先不要运行”的消息送达前；已同步并接受保留，后续没有重 prepare 或 execute。这个计划不可被原地清空 pending，完成义务后须另冻下一计划。

费率义务仅参考 [historical_fee_evidence_001.md](historical_fee_evidence_001.md) 和其 `8bad75c1182f2415002c8ed38c35fc5475c8b3b92977d18ffbf9f547b8925471` 来源清单。2015 过户费公开表述支持单项费率，不能替代具体账户佣金、包含项目和分币政策，也没有把费用全项标 verified。

## 验证记录与冻结身份

测试均为合成数据。首次专组 20 通过、1 个测试定位 v14 文件的路径失败；修正测试路径后，21 项专组与 30 项旧模拟器回归同次 51 通过（5.50 秒）。随后新增资源和篡改反例，专组单独 24 通过（5.38 秒）。这些运行不相加成一次验收；`raw_saved_validation/001…003.json` 明确是原工具结果的转录，首跑源码哈希未单独捕获，未伪造原始 stdout。

根代理独立 6 项通过，保存 JUnit 为 `raw_saved_validation/root_independent_001.xml`，其中 tests=6、failures/errors=0、suite time=3.856 秒（CLI 汇报 3.90 秒）。独立覆盖真实双实例并发、pre-buy 写失败时零买入应用、post 写已提交后报错、缺 terminal 不补算、当日质量不作开盘门，以及退出容量拒单后缺价仍保留锁定资金。该 6 项单列，不混成前述 24 或 30 的一次执行。

合成 1 万元算术例独立核到买卖各 900 股，买价 10.01、卖价 9.99，费用 5.18 + 14.17，最终现金 9,962.65。缺持仓价例保留 900 股、985.82 现金和 5.18 已付费用，NAV 只完整到前一会话。它们是公式验证，不是缩小真实准备本金或策略表现。

| 文件（均在 revision15） | SHA256 |
|---|---|
| `src/quanta_agents/raw_saved_research.py` | `fbebcfa6a1e0bd288e4d736d9c903ccba8a1bcede401e178a11a6b34c70bc5cb` |
| `src/quanta_agents/raw_portfolio_backtest.py` | `cfdd9ae7da28014c0e3a2c4e89febce69ff5f73a803e5e047d763304e69cd23a` |
| `tests/test_raw_saved_research_v15.py` | `7db71b1844f5354c19999565b726dd033a509b2a3d058f099dc4b2bfa78aab4e` |
| `scripts/freeze_raw_saved_research_v15.py` | `9285e51780a3de06529bb55286086e637bf9b72225dbd8701d487e65bd4964ba` |
| 根代理 `tests/test_raw_saved_independent_v15.py` | `0f9be6aab591532e7f3a002857917ece42305acf57378ea622d363578d37c3ff` |
| 根代理 `raw_saved_validation/root_independent_001.xml` | `eeab13c84db1024490dd1bec8599f1d92a8a67f05a247d65b7ac5f6fa5cd2af6` |

两问自检：本轮实际补齐了保存输入身份、严格 next-session 目标和一次执行的持久失败边界，默认结果与 v14 完整 JSON 等价有定向证据；尚未完成的是 192 项真实范围义务与实际矩阵接线。下一步由根代理验收并另行授权统一一次执行，不能直接运行当前 pending 计划。若真实状态/容量、权益变化、成本合同或持仓估值否定现有假设，应停止该路径并保留失败，不更换样本或降低成本。模块只提供同目录 controller 串行和可核回放；全项目计划去重由上层负责，Python 对象、SQLite 哈希链也不构成 OS 沙箱、防篡改存储或外部来源绝对认证。
