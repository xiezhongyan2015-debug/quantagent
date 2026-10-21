# v16 根任务格式准入边界验证 001

本轮在隔离 v16 增加两处进程前检查：Gateway 在本地 CLI 帮助检查之前拒绝复合枚举风险；casebank_live 在占用共享研究预算之前检查。同一 helper 进入冻结研究 source_files。保存完成回答的 verifier 不使用新预检，因此不把新格式政策追溯用于旧 P0 恢复。

根任务先执行三个会失败的反例，实际 3 failed in 4.83s。保存旧远端拒绝 schema 能越过原 preflight；绕过工作台的调用可占用预算并进入假 Gateway；新 helper 尚未进入 source pin。所有进程表面均用 trap 或假 Gateway，不曾新调供应商。原 stdout、stderr、JUnit 和回执保存在 schema_admission_validation/001_before_fix。

接入后根任务运行 4 个新边界与原 Gateway/P0 终态检查，实际 95 passed in 11.57s，适用源和测试前后 SHA 一致。schema_admission_validation/002_after_fix/receipt.json SHA 19660842e0a1deb437e3928dd60fb4072fb88e8c02e113ef4b1019fe6681054d。这是当时 helper 19741ce6 版本的结果。

随后独立作者真实复现 Python tuple 经 JSON 编码变复合 array 的遗漏。修复仅把 helper 遍历对象改为实际序列化 JSON。最终由独立组统一执行受影响的作者 46 + 根任务 4 + 独立 1，51 passed in 23.52s，receipt SHA 6c43e30fe3430bae99c8bcbb0774d078b72c3dff80cea2a93f3ed0df7ee4417c。根任务没有重复旧 91 个 Gateway/P0 检查；旧 95 与最终 51 有重叠，不能相加冒充唯一测试数量或声称旧 95 使用最终 helper。

最终 helper SHA 276481336f2b647717c83bb46be1def6a366fa5e1de072f61c58b1f78a8bdf38。完整语义只允许有序 [1,5,10]；另外 26 个传输层形状仍在工作台持久登记后失败，不能排序、去重、补齐或免费重试。

原 v15 的 246 个 src/tests/scripts 文件及首请求 12 个失败审计原件均重新比对不变。原 campaign 仍 paused，1 次失败未知、80000 预留、0 action。实际浏览器刷新已看到旧 28 个完成调用加此次 1 个失败，553230 已知研究 token 与80000未知预留；工程平台用量不包含在该研究小计里。

## 两问自检

这一步做得怎么样？本地准入前风险检查、预算占用边界和严格期限语义已有直接反例及修后证据。检查器只覆盖有限 JSON 和复合 enum 风险，原 schema 在标准验证器有效，当前远端400仍未定位到唯一关键字。本地通过不能证明供应商接受。

下一步如何改进，什么会推翻判断？完成同库一次纠错的原预算承接、崩溃防重发、只读监控和独立验收，再冻结具体派发许可。任何漏掉实际发送表示、在检查前启动进程、释放原未知费用、重置预算/时间或放宽期限语义的反例，都推翻对应安全性质并停止准入。供应商再次拒绝则保留新的完整失败，不能自动换格式继续试。
