# 四题入口外部 SHA 参数修复 001

独立审查发现直接 Python 调用 `run(expected_admission_sha256=None, ...)` 会沿用通用 JSON 读取器的可选 expected 语义，从而跳过准入文件字节 SHA 校验。正式 CLI 必填参数没有这条传 None 的路径，但函数契约也应强制外部 pin；不能只在文档中把它缩成 CLI 保证。

在改动前保存原 `28d2e5d667d3f004ce2e172a46a3969057ca1e769ff929caf4fb07a7a61738c8` 脚本，再执行一个没有任何实际文件/模型访问的参数反例。`casebank_engineering_attempts/entrypoint_parameter_before_001` 保存原源码、input_hashes、JUnit 和 pytest 原日志，实际 **1 failed in 4.24s**：无效 admission pin 在拒绝前到达控制器加载陷阱。旧薄入口 1 项通过记录和原文档均未修改。

最小修复新增 `_pin`：expected_plan_sha256 和 expected_admission_sha256 都必须是非空字符串、恰 64 个十六进制字符；允许大小写并规范为 lowercase。run 在 `_load` 之前检查两者；`_load` 也检查 plan pin，status/reconcile 的直接 Python API 同样不能传 None 绕过。没有修改 campaign/live 的 10 个执行依赖或增加任何默认预算。

仅重跑原薄入口 1 项和新窄参数 1 项，结果 **2 passed in 9.50s**，原作者 15+独立 6 项未重跑。窄测试逐一覆盖两种 pin 的 None、空串、错误长度、非 hex 和非字符串，Controller/Gateway 均有禁止陷阱。原薄入口仍验证四个假进程唯一 final 和 status/reconcile 无新增调用。本次真实 prepare/run、真实 Gateway、市场读取、策略回测均为 0。

after 目录 `casebank_engineering_attempts/entrypoint_parameter_after_001` 保存完整 input_hashes、JUnit、pytest 原日志及 receipt；receipt SHA256 为 `483ab750c33a262cf34a83f004479368a5aa021500fd6fa574803f9d7b924061`。

| 文件 | 最终 SHA256 |
| --- | --- |
| scripts/run_casebank_reference.py | `699ec8b6017a06730cd4dc2326ba577ca82787cb0f1e783898ccadb74ae730c4` |
| test_meta_casebank_reference_pins_v15.py | `0b7539526f19ef346f928acc77c4c47ae28497fbe7d9ee63830b816ab4105aee` |
| 原 test_meta_casebank_reference_cli_v15.py（未改） | `365739069cf023af6e7cbf46c28ddfa2eb3524c4821053d06ad3a4e2e22c8170` |

**这一步做得怎么样？** 关闭了真实 CLI 之外的直接函数参数缺口，并保留失败到修复的证据。**下一步做什么？** 独立作者只读核对最终来源和 after 回执，root 冻结实际阶段预算与准入；此补丁本身不授权付费。若无效 pin 还能到达控制器加载或 Gateway，立即撤销该边界判断。脚本 SHA 已变化，任何未来计划必须绑定新 SHA，不能复用旧入口审批；当前没有实际准备的阶段需要迁移。
