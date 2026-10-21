# v16 一次纠错入口 001

新增 scripts/run_transport_correction.py，明确区分 status、inspect 和 execute。没有 prepare、resume、retry 模式。每个入口均要求原 campaign 路径、许可文件及其完整 SHA、准入文件及其完整 SHA；None、空值、bool、短值和非法十六进制均在读取文件前拒绝。准入还绑定此入口文件 SHA，防止外层文件 hash 正确却换了实际执行脚本。

准入原 JSON 字节直接传入 controller，不重新序列化为另一种 hash 口径。只有 execute 构造 Gateway，且要求显式原生 executable 路径及准入中对应字节 SHA；timeout 取 controller 绑定的原单次预算。status/inspect 不构造 Gateway，也不消费一次纠错许可。真正同库预算、许可消费、原 unknown 承接及停止由 transport_correction controller 验证，不能用这层入口测试代替。

本次 6 个离线入口检查实际 6 passed in 2.91s，回执 transport_cli_validation/001_author/receipt.json SHA 1e3a83e5d65d72a62f669c70afc804bacc92a7939515493b797fd4a30d538cfb。controller 与 Gateway 为测试替身，没有模型或真实 ledger 操作。覆盖两个必需 pin 的十种无效值、外层 hash 合格而入口 pin 错、准入字节保持、文件篡改、二进制来源漂移、只读命令无 Gateway，以及一次显式 execute 调用。stdout、stderr、JUnit 均保留。

自检一：这一步是否防止入口绕过具体准入？必需 pin、脚本身份和显式二进制有直接拒绝证据，但本轮用替身验证入口分流，未证明真实 controller / provider 路径；不计供应商兼容通过。

自检二：下一步及反证是什么？在 controller 最终冻结后，用一个新合成目录验证真实 controller 的只读入口与一次假进程整合，再组合独立会计/恢复/监控证据。任何无需完整 pin 就构造 Gateway、只读命令消费许可、实际二进制不符或进入多次循环的反例都停止准入。实际首请求失败和80000未知预留不因本入口创建而改变。
