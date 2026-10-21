# RD-Agent 量化研究循环：源码核对记录

核对日期：2026-09-12。用途：比较自动 AI 量化迭代研发能力。没有启动付费模型调用、历史市场回测或交易，没有修改两个项目的运行代码。

**版本和证据范围**

- RD-Agent：本次通过远端 HEAD 核对的提交 `32b3d395e73d9db5eee3fe9063d69aec0fdc83bd`。`rdagent_source_subset.zip` 保留 104 个下载文件及其原始 MIT LICENSE；它是定向源码子集，不是完整仓库。`audit_manifest.json` 记录路径与 SHA256。
- Qlib 辅助参考：提交 `79633dd9506ea689e5400dea0197717b5b3d74b7` 的 `qlib/workflow/record_temp.py`，用于确认 SignalRecord 的测试标签来源。不是断言某次 RD-Agent 历史运行安装的恰好是这个 Qlib 版本。
- 我们的比较基线：当前本地 `meta_v9/study.py`、`range_study.py`、`ranges.py`、`batch_compute.py`、V8 修订接口及 V9.0.1 验收记录。主记录没有完整覆盖范围搜索的新增代码，故同时检查实现与验收材料。
- V9.0.1 有已保存的 18 个账户、4 次模型调用及拒绝候选记录；范围搜索代码存在，本次未找到其完整运行验收证据，也没有执行验证。不能把两者合并成一个已验收版本。
- 论文案例来自作者的 [NeurIPS 2025 论文 v2](https://arxiv.org/html/2505.15155v2)，没有在本轮重现论文结果。

**框架定位**

量化入口是 `rdagent/app/qlib_rd_loop/quant.py`。`QuantRDLoop` 将场景、假设生成器、任务转换器、因子/模型 coder、runner 和 summarizer 按配置组装。外循环决定实验方向，内循环用 Co-STEER 修复实现；训练和账户由 Qlib 承担。这里的自动迭代主要改变因子代码、模型结构和超参数，不等于自动修改 RD-Agent 自身的调度器、账户和验证政策。

关键源码导航，均基于上述固定提交：

| 文件 | 应读的责任 |
|---|---|
| `rdagent/app/qlib_rd_loop/quant.py` | 自动量化主流程 |
| `rdagent/scenarios/qlib/proposal/quant_proposal.py` | 方向选择、历史上下文、阶段先验 |
| `rdagent/components/proposal/__init__.py` | 假设和实验规格两次生成调用 |
| `rdagent/components/proposal/prompts.yaml` | 通用研究提示词模板 |
| `rdagent/scenarios/qlib/prompts.yaml` | 量化假设、输出协议、结果反馈 |
| `rdagent/scenarios/qlib/experiment/prompts.yaml` | 数据、接口、输出、执行环境契约 |
| `rdagent/components/coder/CoSTEER/knowledge_management.py` | 当前失败轨迹、类似成功实现、类似错误修复检索 |
| `rdagent/components/coder/factor_coder/evaluators.py` | 实现是否合格，与收益评价分开 |
| `rdagent/scenarios/qlib/developer/factor_runner.py` | 因子合并、相关性去重、基线与模型搭配 |
| `rdagent/scenarios/qlib/developer/model_runner.py` | 固定因子材料下运行生成模型 |
| `rdagent/scenarios/qlib/developer/feedback.py` | 实验比较、LLM 反馈和晋级决定 |
| `rdagent/scenarios/qlib/proposal/bandit.py` | 方向调度的指标与贝叶斯更新 |

源码链接前缀：[固定提交](https://github.com/microsoft/RD-Agent/tree/32b3d395e73d9db5eee3fe9063d69aec0fdc83bd)。

**已定位的实现与评价边界**

1. **默认 test 段参与外循环搜索。** 配置在 `segments.test` 对应时期运行 SignalRecord/PortAnaRecord；workspace 读取该实验指标；feedback 比较当前与 SOTA；下一轮 proposal 接收结果与反馈。因而这段数据在研究层是搜索反馈集，不能同时作为最终未见测试。要另设冻结后评价窗口。该结论针对当前默认路径，不足以单独判定所有历史论文实验的处理方法。
2. **年化收益字段名多了尾部空格。** `bandit.py:44` 查询 `annualized_return `，而 `feedback.py`、Qlib 标准指标使用没有空格的名称。对原函数输入标准名称、收益 0.12，实际读出 0.0；其衍生的 `sharpe` 也变成 0.0。
3. **回撤奖励方向有问题。** 对 Qlib 的负数 max_drawdown，`as_vector` 再取负数，奖励权重为正。其他指标固定为零时，-5% 回撤的奖励为 0.005，-30% 为 0.03。它对这一项奖励了更深的回撤。未测量此问题对完整研究结果的实际影响。
4. **后验均值更新顺序不符合所述标准共轭更新。** `LinearThompsonTwoArm.update` 先修改 precision，再把修改后的 precision 乘旧 mean。对一维单位先验、单位噪声、两次 x=1/y=1，原实现给出 0.833333；标准后验为 0.666667。这是孤立函数对照，未重跑方向选择序列。
5. **字段命名和口径还需统一。** `sharpe` 实际由 ARR / -MDD 得到，属于回报/回撤口径，不是通常的 Sharpe；研究历史模板展示 without_cost，summarizer 使用 with_cost，容易造成阶段间口径混用。
6. **SOTA 专用上下文槽位存在大小写错配。** quant/model proposal 返回 `SOTA_hypothesis_and_feedback`，通用生成器查询小写 `sota_hypothesis_and_feedback`，会让专用块缺省为空；不能扩大为 SOTA 信息完全丢失，因为它仍可能在历史轨迹中出现。
7. **模型反馈存在重复后端调用。** `QlibModelExperiment2Feedback.generate_feedback` 连续两次使用相同用户/系统提示词调用后端，第二次结果覆盖第一次。静态确认调用路径；实际额外费用取决于后端缓存及运行配置，本次没有发起请求验证。
8. **论文设计不能直接当作默认执行路径。** 当前 Co-STEER 多任务路径跳过成功/超限任务后并行实现未完成任务；不能仅凭论文的 DAG 调度描述，声称当前每次因子开发都执行完整拓扑排序。量化研究的 `RAG` 槽位主要是按轮次切换的人工先验字符串；编码阶段则有实质的图结构经验检索。两者应区分。

其中第 2—4 项已运行原始独立模块的小型数值验证，输出见 `audit_probe_results.json`；其余为定向静态源码追踪。没有提交上游 issue 或修改上游代码。

**对照与采纳建议**

我们已经具备材料冻结、参数/组合检验、账户与成本记账、失败记录和冻结后诊断。V9 的模型调用明确禁止写代码和调用工具，范围搜索也局限于已注册因子及预置组合选项，故运行更多账户主要扩大现有空间的搜索，不能自动补出新的算子或预测模型。

最有价值的增量是给我们增加受控的研究开发入口：模型提出因子/模型任务及可证伪预期，开发器在独立工作区生成和修复实现，通过时间一致性、数值、资源及接口检查后注册；现有批量引擎完成大部分实验。研究负责人只在新机制选择、失败诊断改变实验分支、最终冻结等关键节点参与。

优先借鉴 Co-STEER 的任务/实现/反馈经验结构和检索机制，其次是因子—模型替换接口与阶段提示词契约。Bandit 需要先修正并与固定预算、轮换或随机调度做对照，不应因为论文命名而优先接入。应保留我们的失败与曝光记录，且不把已有曝光窗口重新命名为独立留出。
