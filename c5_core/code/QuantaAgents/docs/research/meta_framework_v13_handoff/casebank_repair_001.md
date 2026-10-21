# v13 四题生成器最小修复 001

2026-09-07，Asia/Hong_Kong。这是 [原生成说明](casebank_generator_001.md)之后的追加记录，原说明及实际生成产物均未覆盖。**独立审查发现三类工程缺口，已修复；原专组 13 项及独立专组 10 项分别通过，待 root 联合验收。** 此次没有生成新的实际四题包，没有新种子、参考研究、Gateway 调用或回测。

## 原因、复现与最小修改

| 原缺口 | 独立反例 | 最小修复 |
| --- | --- | --- |
| `read_bytes()` 完成后才检查 4 MB，可能先无界分配内存；stat 后增长也不能由单次预检保证上界 | 伪造 stat 大小为 4,000,001，读取仍发生；模拟增长 stream，旧实现请求 `read(-1)` | `casebank_public.py` 先核 stat，随后仅 `open('rb').read(4_000_001)`，实际返回长度超过 4,000,000 再拒绝。输入 manifest 和各公开文件共用同一上界。 |
| Windows junction 不一定属于 `is_symlink()`；原代码把已经重定向的 `public.resolve()` 当允许根 | 模拟 public junction 指向 evaluator_private 内复制的公开树，原实现放行。反例没有创建真实 reparse point，也没有读取私有答案 | 每次读取先要求 `public.resolve() == root/'public'`，再核目标仍位于该固定 public 根和原有符号链接限制。此为路径读取检查，不是 OS 隔离或任意并发文件系统攻击的保证。 |
| 消耗 generation_started 后，保存 generator_source.py 位于 try 之外 | 注入 source archive 写入 OSError；旧实现保留了 consumed 槽，但没有 generation_failure.json | 把源码归档写入移入既有 try；失败记录保留 completed_cases=0、原异常、单次生成和不换 seed 标志，随后仍拒绝自动再生成。 |

先完整读取独立反例再修复。本实施者复现结果为 **4 failed, 6 deselected in 4.85s**，退出码 1；作者此前独立复现记录为 4 failed in 4.08s，两者不是两个新的研究样本。原 13 项机械专组未覆盖上述边界，因此原通过结论应限于其已测生成、网格、身份和失败路径，不能据此推断旧 public loader 已满足所有读取上界/重定向承诺。没有证据表明实际原小包曾发生答案泄漏；这是通用读取与异常边界的可复现缺口。

修复后的原专组 **13 passed in 6.54s**；其公开文件读取守卫从 Path.read_bytes 改为实际使用的 Path.open，继续拒绝私有命名空间并新增明确 16 次读取断言。独立审查者使用自己的 10 项专组复核为 **10 passed in 3.08s**，其中包含原四项反例。两组结果不相加冒充一次正式联合验收；其他六项的中间定向运行也不累计。

## 保留的实际包与不同源码身份

唯一实际目录仍为 `experiment_traces/meta_casebank_development_v13/attempts/20260906T184928Z`。只读逐项核对：receipt 登记的 **20 个 private 文件**、**12 个公开 case 文件**、公共 manifest 和 receipt 本身均与原哈希一致。包括 seed/plan/commit、源码归档、draws 和 QC；没有重生成、换 seed 或改旧计划来匹配新源码。

| 身份 | SHA256 |
| --- | --- |
| 实际目录归档的旧 `generator_source.py`（原 plan 继续绑定此源） | `d2a5990b2ec2906ba0010bba8c59fa48134fb9538625d4c629593037f5f7f0a7` |
| 原 `generation_plan.json` | `f656d8b06ca808602d16ac006e83de051c48a14dae196d4d2c33ea3fcd5844db` |
| 原 `public/manifest.json` | `e728fa67c052d8af1f4133a5f9cc29dfe9b18772dbd4f244d088d0b08396b797` |
| 原 `generation_receipt.json` | `6e3ee10a4d378ea55c1eeec07d4bf78442511bcd2d1c65fe85f247061ff174a5` |
| 修复后 v13 `casebank_generator.py` | `1b6c300fb1dd2416de1841502f5a8008f0aeb67cec6191f6c9ffc6ced7e4daed` |
| 修复后 v13 `casebank_public.py` | `44387f637d7bfbb78a11c6cf415037d2f2b7624e8c976b8d13b5de56d3dc5695` |
| 修复后原专组 `test_meta_casebank_generator_v13.py` | `c4280e8ab81d9f1f9bdf08cbcace3a9c0d21e124839922535740626d194809e7` |
| 独立专组 `test_meta_casebank_independent_v13.py` | `fdc4ff8505c6644a9cd33da4b217a34814db5a67ccd33a6570e074bcf0e9c405` |

当前 generator 源 hash 与旧 plan 中的源 hash **明确不同**。旧实际包的生成来源依然是目录内保留的旧源码；新 reader 核验并读取该旧公开包，不等于用新 generator 重生成了它。原辅助脚本、原生成说明、P2 controller 源/Store/独立审查报告保持不动。临时 pytest 工程样例不计入实际四题生成次数。

## 结论与下一步

三类可复现工程缺口已由独立反例关掉，修复没有改变随机机制、数据、分母、费用合同或公开任务语义。四题仍是已暴露 synthetic development 输入：2 个正机制候选、1 个相对明确基准的有限范围负控、1 个公开开发分块失效候选。尚无独立同信息、同预算参考复现，不能称为合格隐藏正控或真实策略成功。

接下来仅由 root 汇总冻结身份与联合验收。本轮新增研究 Gateway、真实行情读取、回测、实际生成/参考重放均为 0；Astra/xhigh 工程审查与实现用量非零且另列。若后续出现有界读取仍分配无界、固定 public 根之外可被读取、已消耗生成槽发生无记录且被自动重试，或旧 plan 被改写匹配新源码，应撤销相应工程通过结论并保留失败证据。
