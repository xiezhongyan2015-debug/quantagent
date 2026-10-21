# v14 CasebankWorkbench 超长请求修复附录 001

独立审查复现了真实的预算登记缺陷：超长有限 JSON 在原 `_copy` 的字节检查处被提前拒绝，final_attempts/query/candidate 均没有登记。因此公开字段各自合法但总长超过 65,536 字节的 final 可以留在 ready 状态免费更正。原报告关于该入口的表述不满足唯一 final 和失败保留机会的要求；本附录撤销这一边界的原通过判断，原报告与失败证据保持原样。

最小修复已完成，作者原 25 项和独立原 8 项一次合并回归 **33 passed in 90.31s**。源码停止修改，供 root 联合验收；不等同于整个 v14 已经封板。

## 原证据与根因

独立审查目录 `experiment_traces/meta_ashare_revision14/independent_review_attempts/casebank_workbench_001_before` 保留 input_hashes、pytest.txt、junit.xml、receipt.json。其结果为 **2 failed / 6 passed，36.32s**，receipt SHA256 `22b53f90112cc3a1aeeff8dfb3525043c3e5f2693191e4712effbf0b69b595dd`。

第一个反例的 final 含 12 条各 4,000 字符 claim、12 条各 4,000 字符 limitation 和 8,000 字符 summary，每个字段/数组均可通过普通 action_schema，但整份 JSON 超过全局 64 KiB。第二个反例给有限候选 JSON 添加超长字段，同样在预算登记前返回。两者都没有运行工具，但前者 final_attempts 仍为 0、后者 query/candidate 仍为 0，这会让后续付费控制器误判还有原轮机会。

同一 before 目录另以独占新文件保存原作者源码与测试：`preserved_casebank_workbench.py` SHA `f16bd56fb15e6f103cea59a780f2cf2857509e97762095a7793895bf723c616e`；`preserved_test_meta_casebank_workbench_v14.py` SHA `086af15b914a9de2cd0c03d04e331d8193f61ce03d9b612b12abca6a36401716`。原报告 `casebank_workbench_001.md` SHA 仍为 `45cee313ca95dd17636209fadd093ab9b6f8410ee5d32ec86edc51b427c9c5af`，未覆盖。

## 修复后的精确合同

公开 overlay 版本为 `casebank_workbench_v2`。公开 `request_bytes_rule` 明确所有字段上限与完整请求的 **65,536 canonical UTF-8 JSON bytes** 上限同时成立；规范序列化使用 sorted keys、compact separators、保留非 ASCII、拒绝非有限值。字段各自合格不表示总字节合格。

入口对已经给出的有限 JSON 逐段计量并计算完整原请求 SHA256。正常有界请求照旧保存完整正文；超长请求仅保存有界 rejection envelope，包含已知动作类别、原 canonical 字节数、原 SHA256、上限和 `body_retained=false`。整份超长编码不合并保存或返回到下一轮 prompt。调用方已经提供了内存对象，JSON encoder 的单个 token 仍可能随字符串长度增长；这里没有承诺传输解析器或 OS 内存沙箱。

在拒绝超长请求前，仍通过原 Store 原子登记相应机会和全部子项：final 消耗唯一 final 并写 failed/terminal_invalid_final；普通查询消耗 query；候选消耗 query、candidate、3 requested horizon 和其输出子项。工具、plan、期限核、匹配核均不执行。普通失败之后若仍有预算，只能用新的顺序槽提出另一请求，不能免费修改原槽。超过已耗尽预算的请求继续沿用已公开的终态拒绝规则。

原请求身份和存储身份分开：`request_hash`/`request_identity.sha256` 对应完整原普通 JSON；`stored_request_hash` 对应保存的完整正文或拒绝 envelope。`request_identity` 另记录完整 canonical 字节数和 body_retained。恢复会核对这两层，不能误把 envelope hash 当原请求 hash。同槽重放同时比较原 hash 与字节身份；改一个字符或提交 envelope 自身均不匹配。新槽重复同一超长请求仍占新机会，并可引用原已保存失败。

不能规范序列化、非有限值等对象仍在 ordinary JSON 入口边界拒绝；这与已经取得有限 JSON、已知动作但超过大小限制的预算失败分开。未增加模型重试、工具成本折扣、候选额度、补跑或付费派发入口。

## 本次受影响回归

实际执行仅一次作者原 25 项与独立原 8 项的合并回归。作者原有重复/恢复测试补充超长中文 candidate/final：原 SHA 和字节数与独立完整序列化核对、存储 envelope 小于 1 KiB、同槽改正文/伪造 envelope 拒绝、重开后原请求 saved-only、final 终止及计数不变。独立原 8 项未改；前述两失败转为通过，其余独立时序、两条件分块 Decimal、控制不替换、最后 query/final 并发和部分文件停止反例继续通过。

after 目录为 `experiment_traces/meta_ashare_revision14/independent_review_attempts/casebank_workbench_001_after`，保存 input_hashes、pytest.txt、junit.xml、receipt.json。结果 **33 passed in 90.31s**；包含进程启动的控制器耗时 92.641 秒。receipt SHA256 为 `843ef8f6c3138e228211a2638ed5d993a15b1f0e7fadf4fad61a68406be95013`。未重跑无关完整项目套件，历史 before/after 数量不相加为独立研究样本。

| 文件 | 最终 SHA256 |
| --- | --- |
| v14 meta/casebank_workbench.py | `ea2941fc5776e72c32a47f67b01c169b94a754945b0ce85ee8a3af8b04fb9ff3` |
| 作者 test_meta_casebank_workbench_v14.py | `1f8acf6872f4411f2fe3dc67526985cf590601d6b383539b1cd902c15248de2e` |
| 独立 test_meta_casebank_workbench_independent_v14.py（未改） | `ab81f77936e8c816855dda4d109a361f7bba11afec1adc6ddfb4ac578037979d` |

再次按复制清单核对 228 个继承文件：v13 全部无变化；v14 仅 root 正在独立维护的 `src/quanta_agents/meta/diagnostic_monitor.py` 不同。该文件不属于工作台冻结的五个执行模块，不改变本次 case scope。本作者没有改它、v13、原题包、Store、旧记录或原报告。

## 两问与停止条件

**这次做得怎么样？** 独立反例推翻了“超字节输入属于预算外噪音”的实现假设。修复在保留资源边界的同时登记有限 JSON 所属机会，并把原请求身份与有界证据身份分开；两项失败及更正后结果都有原始回执。它仍是 exposed synthetic 离线工程验证，不是付费 harness、参考发现或策略成绩。

**下一步该做什么，如何改进？** 独立审查者只读核对最终源码和 after 回执，随后由 root 合并其他明确受影响检查。若未来真实 harness 解析出有限 JSON 却在调用工作台之前丢弃，仍可能存在外层计费与机会遗漏，必须在那里保留 call_id/原回答身份并遵守唯一 final，不能借本修复声称已覆盖未接入层。

若再发现有限 JSON 能不记机会免费更正、拒绝 envelope 可以冒充原请求、同槽恢复重新计算、超长正文进入公开历史、某侧获得额外预算，或旧原包/失败记录被覆盖，则立即撤销对应边界结论并停止扩大。本修复子任务新增项目 CodexGateway 调用 0、新策略回测 0、真实行情读取 0；工程/审查模型用量非零，不能推广为全项目期间零调用。
