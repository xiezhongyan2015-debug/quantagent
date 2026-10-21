# v17 策略开发：公开合同补充只读评审 002

结论：本次补丁只把已执行的合法性条件加入普通调用者可见的公开合同；未改变策略计算、预算、时点、机会登记或恢复实现。核对补丁和作者窄反例回执后，001 的有界工程结论可结合本记录使用。没有重跑既有九项独立检查，没有改变旧报告、旧结果或实际研究状态。

001 报告 SHA256 `fe274d11733afa0bd3887eecaf5cf62ed4a2d7aca6cb25ec4b6dd72f3b1cc2e3` 保留其当时源码 `0d578f7e15b7780025f32bcfaa637fb748142351c2a5ff674c821c950c2399ff`。当前 v17 `strategy_development.py` SHA256 为 `4cf15ec03e2551b0acc3670127089b116fcb053a55bd8a1d82c2d10411de0543`；纯计算模块仍为 `0ce1ee91ade853a52755eb044b309f4a3d3e22d3275bd1b11c5f0fe0302a1da4`。

只读审计保存于 `experiment_traces/meta_ashare_revision17/strategy_independent_validation/004_contract_metadata_readonly`：receipt SHA256 `080eb93c3262d06563657083ba66ca182694a141e63e292523b9c7fe53f25e8c`，source.diff SHA256 `acde5be0a863f664b63e91e1f1d5a64db8dbb4415f648fdc5d4b710413bce48b`。AST 对照确认仅顶层 `public_contract` 函数变化；完整 `StrategyDevelopmentWorkbench` 类及其他函数 AST 不变。独立九项测试文件仍为 `8ee0f7ef2474f23498bbb5c04cc156320cd4aa2091fb3c505f34a0580cbc688c`。

补充公开的条件与现有执行代码逐项对应：程序版本和精确字段、因子数量 / 名称 / 前向引用限制、hypothesis 长度、条件列表个数和每条长度、表达式长度 / AST 限制及整体 UTF-8 请求字节上限；记忆 claim / explanation 长度、来源 evidence 数量和本 scope 已结算来源约束、已有 memory_id，以及四种 invalidation reason 枚举。作者的窄反例先使 2,001 字符 hypothesis 实际消耗候选并被拒，再检查公开合同是否事前显示 2,000 上限。旧版因缺少合同字段真实失败，补丁后通过。

对应作者回执位于 v17 `strategy_development_validation`：

- `003_contract_before/receipt.json` SHA256 `e3bef1fe5bd08fdf40b4e8efd08d0bfbca58746656ccb7ee4a0c035eb54c90a6`：1 failed，pytest 输出 4.27 秒。
- `004_contract_after/receipt.json` SHA256 `60076467b2e5a48ca2dd7ab273666c4ba7b4da6fb95d491e7a38138ac8e9dccc`：1 passed，pytest 输出 4.00 秒。输入前后 hash 相同，回执列出的所有当前输入和输出文件 hash 已独立逐项核验。

这一个通过是作者新增合同检查，不冒充独立复跑九项。新公开合同参与新 plan 的 contract/source 身份，因此旧合成 scope 仍按其旧 source pin 留档；本记录不授权重建旧 scope、重置预算或延长截止时间，也不回写原四题账本。

两问自检：是否删失败或改算法取得通过？没有，差异只有公开元数据，before 留存。是否把接口工程通过当成策略 / 收益 / 执行认证？没有；新增研究 Gateway 调用 0，实际市场和 private 读取 0，工程评审模型 token 未单独核定。
