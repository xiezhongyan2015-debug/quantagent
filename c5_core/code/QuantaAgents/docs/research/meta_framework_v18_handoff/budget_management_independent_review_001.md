# 已知单题预算停止管理器：独立工程收尾 001

**结论：冻结的外部管理模块通过本次有界工程验收；实际管理未应用，本报告不授权管理、resume、付费派发或启动后三题。** 六个事前逻辑场景、八个 pytest 参数例一次修后全部通过。原第一题无 final 的结论、所有成本与失败分母保持，不把行政 `budget_stopped` 当成模型 final 或弃权。

本次按既定 Astra/xhigh 独立评审分工进行。没有研究 Gateway 调用、真实行情读取或 actual SQL 写入。工程平台模型用量未在此独立计量，不能记为零。

## 冻结对象与实际执行证据

- 外部模块：`experiment_traces/meta_casebank_budget_management_v18/budget_management.py`，SHA256 `fad68b9f70fcc9584a4c993c51b324dad6252429c592cb75aa9acdc9a2a6c53c`。未修改冻结 v18 runtime。
- 独立测试：同目录 `tests/test_budget_management_independent.py`，SHA256 `e1f3e6ae3da31345d84ecb5e801b0cae64a9d6e1001ba6b359c9d3d38244f8ad`。
- 唯一修后运行：`experiment_traces/meta_casebank_budget_management_reviews/independent_after_001/receipt.json`，SHA256 `5629f131e62ee3f41e604f1d2456bb5e379ff39348e96d8f2caf2c4ba19c0ad0`。真实 JUnit 为 **8 passed，363.78 秒**；包装耗时 367.688 秒，exit 0。实际导入模块路径与 SHA 已由测试记录；源码、测试、包装和缓存 manifest 前后哈希全部相同。
- 沿用作者已经完成的 225 文件合成缓存，manifest SHA256 `547baadcbfe0d872687dbbf0850bc2c30748ec7ce5625c016ca3ccb36d3cb661`。运行器在启动前强制该哈希及存在性；没有重新构建 13 个 fake completion 的链。共享缓存仅恢复它原有的合法 TEMP 文件，持有独占 fixture lease；作者组与独立组未并行还原缓存。
- 事前判据原件 `known_task_budget_management_independent_predictions_001.md`，SHA256 `8c91e9aa3ec3574e55b5253f3aac4c94d860e44624d7d2ff9d55cd0f5e0b6d97`，保留不改。

## 八项通过具体证明了什么

| 场景 | 实测范围与结果 |
| --- | --- |
| M1，1 项 | 完整保存证据夹具执行一次管理。治理仅将第一题 `ready→budget_stopped`、current task 移到原第二题；stage/phase 仍 paused，第二题仍 not_started。原 calls/actions、四 WB、固定 scope/admission、计数与费用不变。原生成器能构造第二题的原公共 prompt，自己的历史为空、剩余 16/12/1 完整，无第一题普通输入标记或管理评语 canary。未执行该 prompt。 |
| M2，3 项 | **隔离的数学谓词测试**：明确 stub 掉保存证据前提，调用原费用计算与管理谓词。本题加下一预留恰等于 600000 不能称耗尽；全域加预留超过 2400000 拒绝；全域恰等于 2400000 可过数学门。这三项不是伪造供应商事件，也不声称构成三份端到端可准入账本。 |
| M3，1 项 | 在完整临时夹具上分别制造新 unknown、未 applied 动作、原公共 artifact 缺失。均拒绝管理，不创建管理记录，不改变治理，旧 80000 预留保留。 |
| M4，1 项 | 两个真实 Python 子进程通过同一 barrier、同一 registry lease 和同一管理 key 竞争。仅一次迁移、一次 audit 增量，两个进程得到同一个记录哈希；换 key 不能再跳到第三题。子进程只有合成时钟和合成路径，网络/模型调用被陷阱禁止。两进程正常退出。 |
| M5，1 项 | 在 intent 后、commit 前注入异常，两处均回滚。commit 后模拟外部回执丢失，已提交结果可读取；随后用原控制器追加一次真实的行政 pause 审计，再把合成时钟推进至窗口外，仍能只读重放同一历史结果。没有第二次写治理、Gateway、WB execute/recover 或自动 resume。 |
| M6，1 项 | 陈旧 source/pre-state、别题 request identity 均在迁移前拒绝。另在有效管理提交后，把最后 inspect 的 limit 50 改为 49，同步 call.receipt、action.response 和各 self-hash，而 usage、治理、原保存文件不变；继承 summary 仍通过其内部一致性检查，但修后管理 read_committed 拒绝被改的历史前缀。 |

合成夹具的费用为 466235 known + 80000 old reserve = 546235，不能与实际账本的 491954 known 混用。所有场景保持原 17/68 次、600000/2400000 token、80000 单次预留和 16/12/1 工具机会规则；没有增加第一题的模型或工具机会。

## 真实失败、修复与证据保留

独立修前运行早已在暂停前自然结束，恢复工程时只读取其结果，没有重跑。`meta_casebank_budget_management_reviews/prefix_before_001/receipt.json` SHA256 `306257e931361fbcfcc8a75cb5e50a4514e2f04bf686df93a68d9a2e26c8fe5f`：实际导入归档模块 `9f26991b…7f523c`，1 failed，pytest 91.15 秒、包装 95.11 秒，源前后稳定。失败精确发生在上述 limit 50→49 双行自洽改写后，`read_committed` **没有抛出拒绝**，并非夹具未准备好。

根因是原 `_check→_projection` 核对 call/receipt/action 的内部身份、哈希、同值关系和计数；phase 治理链只绑定 state，阶段起点保存的前缀也不包含后来 13 calls/14 actions。管理恢复没有重新核对自己 scope 已承诺的完整历史内容。

9f→fad 的 AST 差异仅为新增 `_verify_old_prefixes` 与 `_verify_committed` 中的调用。修复按冻结的 N 行及原 schema、相同序列化规则复核历史表；保留 calls/actions/events 和 phase.AUDIT 的原前缀，仅排除两个可变治理表与 sqlite_sequence。后来合法追加不要求整表重回旧 hash。M5 实测的追加正例是行政 pause 产生的 audit/event；管理后第二题新模型调用的追加只属静态兼容分析，本次没有执行。

作者另有两项原稿缺口的真实 before：过期未提交管理与自洽伪造 post-state。其保存于 `validation/002_corrected_fixture_before`，receipt SHA256 `dcdd78406daa9092d11bd81df90743deba266c230e46b55546c4bade836f3acc`。修后作者证据是 `004_author_after` 的 **5 passed + 1 fixture failure**，再由 `005_author_fixture_after` 的 **单项 passed** 补齐；后者 receipt SHA256 `a8369f93070632f194853a4e059b162ababcc752f531421e4a4a880f2fcfce19`。005 AST 仅改变该测试函数，其余五项及 helper 不变。不能把它们写成六项同次全通过。

作者最初 37000/调用导致不足 13 次的算术夹具失败，以及 004 向不存在的 `events` 表写入的失败，都已保留并明确属于测试缺陷，不能计作这两个产品漏洞的复现。作者索引 `validation/006_author_evidence_index/receipt.json` SHA256 `27c5a6ade2ed0578626a692947f5fd7601dd55931a77b9963df2dfe433d05296` 逐项保存这段历史；独立最终八项不替代或抹去任何旧失败。

## 实际状态与未执行范围

本轮一次字节核验保存在 `meta_casebank_budget_management_reviews/actual_unchanged_001/receipt.json`，SHA256 `6ac697a98477b7cd7e88b5d8ec78f6c556af7114da4299833cda979b84a28eca`。实际 ledger 和四 WB 的 SHA/bytes 均与此前独立五库全逻辑验证 `a4edc4c3…de1235` 相同，五份 backup 各自 SHA 也相同，没有非空 WAL/journal。该核验未连接 SQLite、未重扫全表；由相同文件身份承接已有全行验证，不冒称又做了一遍 SQL 审计。本轮未重哈希数据库以外的全部公共 artifact 文件。

因此在该核验时，实际仍 **未管理、stage paused、15 transports、491954 known + 80000 reserve = 571954 exposure**；第一题仍为治理 ready、14 queries/12 candidates/0 final，后三题仍为原零动作 not_started。已冻结第一题评审 `actual_case01_final_review_001.md` SHA256 `624aab03b83a3d10be6ba9f83bf2a351e520c656be73308ecc036c1608eca7fe` 未改变，无 final 不能被行政管理改造成完成或弃权。

原四题包、量表、旧 unknown、旧时限和补充窗口都不被本报告重置。真实 provider/model 身份不因离线管理测试获得认证，正式成功与独立研究样本增量均为 0。

实际包装 `manage_original_budget_stop_001.py`（SHA256 `4ea0d6b5206a38f9195ec6e7eb61e77b64856fd94ec9fb1a7576682aa7c08f0f`）只做了静态检查：prepare/apply 入口分开，apply 异常先嵌套读取固定管理 key 的 committed 记录并保存读取结果及原始 traceback，没有重 execute/resume/dispatch；本次没有运行包装。也没有实际管理 scope/admission 准入、actual DB 注册/迁移或管理后的研究 loop 测试。

两问自检：**这是否给首题补发一次机会或把无答案变成成功？** 没有；实测仅是合成治理迁移，实际尚未应用，旧无 final、费用和失败分母保留。**什么仍不能由本次通过推出？** 不能推出实际管理获授权、后三题可以启动、真实执行/净收益成立、供应商身份已认证或框架稳定性已经证明。当前用户授权仅限已有工程与记录收尾。
