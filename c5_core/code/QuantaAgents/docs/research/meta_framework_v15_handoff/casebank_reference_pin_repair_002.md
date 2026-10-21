# 四题入口准备回执 SHA 修复 002

独立作者指出同一 optional-hash 根因还存在于 prepare 的 `verification_receipts[].sha256`：JSON null 会沿通用读取器跳过字节 pin。准备尚不放行付费，但不能把这种输入描述为原验证回执已核对。

先保存中间 `699ec8b6017a06730cd4dc2326ba577ca82787cb0f1e783898ccadb74ae730c4` 脚本，在原窄测试追加 prepare/null SHA 陷阱，实际 **1 failed in 4.21s**。`casebank_engineering_attempts/entrypoint_parameter_before_002` 保留原脚本、JUnit、pytest 原日志；之前 before_001/after_001 及报告均未改。

最小修复在 prepare 读取任何验证回执前也调用 `_pin`，统一非空 64hex 要求并规范小写。仅重跑原薄入口与窄参数组，**2 passed in 9.27s**；`entrypoint_parameter_after_002/receipt.json` SHA256 为 `025f330663fd11edb9fb19508efc3e2c41ffe6e04149e4b4e93e47bf175a50bf`，含完整 JUnit/log/input hashes。

最终脚本 SHA256 为 `d9796523fd2724491ce40a382981d24aafbb680de24e6de54989283fa6603cdb`；最终窄测试 SHA 为 `4b3d322d105cb1af902be5f9e3fab0e8db467af80d77ad77af5808b6e8410ce2`；原薄测试仍为 `365739069cf023af6e7cbf46c28ddfa2eb3524c4821053d06ad3a4e2e22c8170`。15+6 适用的十个研究执行依赖未变、未重跑，实际 prepare/run 与付费调用仍为 0。

这次关闭了 CLI 可输入 null 的准备材料边界；下一步由独立作者核对最终脚本/回执，root 冻结完整阶段预算与真实准入。若任何显式要求的外部哈希仍可因 None/null/空值省略，立即停止放行并保留反例。本补丁没有放行真实题包研究，也不授权使用测试中的模拟准入记录。
