# 余题监督入口：独立静态审查 001

2026-09-07，继承 Astra/xhigh。**更新后的入口通过有界静态审查，未发现剩余实质阻断。** 未执行 wrapper、CLI、控制器或数据库操作，也没有运行模型、工作台或占用 fixture；仅读源、核 SHA 和解析 AST。该结论不等于真实派发、进程退出或研究成功已经发生。

最终 `continue_remaining_original_cases_001.py` SHA 为 `6db92725742d3fd112a00ed2286d7ba6c8c4f8e903533c08e64e7845be677ca5`；管理 wrapper SHA 为 `4ea0d6b5206a38f9195ec6e7eb61e77b64856fd94ec9fb1a7576682aa7c08f0f`。

入口要求独立 gate 原字节 SHA，并核 gate 的 wrapper、原 v18s1 scope/admission、管理 outcome、准确管理后态、Python/native/原 CLI、来源证据。首题必须 budget_stopped、current_task 必须 case02、全域仍为 15 calls 与 491,954 已知 + 80,000 未知；保存证据必须完整。固定 `v18_worker_002` 的排他目录与 x/xb 写入阻止同入口重复消费。代码只含一次 resume 和一次 Popen；真实 run 仍经过原 registry lease、累计 cap、窗口及 case 顺序检查。它没有替换题、改模型 schema/历史、重置费用或为首题补 final。

初读 `18e4ccda…` 时发现两处监督缺口，主控已经最小修正：子进程非零退出现在由 `SystemExit(code)` 向上传递；Popen 后 start.json 写入失败仅暂存异常，仍等待**同一个**子进程，保存真实 process_exit 后再报告监督失败，不启动替代进程。正常 stdout/stderr 原件、哈希和实际返回码分别保存；退出记录明确不认证研究结果。其他异常只记录已有 child PID 与 poll 可观察到的退出码，None 不冒充已停止，不自动重试。

管理 wrapper 的 prepare/apply 分开，配置、管理器、scope/admission 和精确原管理前态绑定；实际 apply 后核只读 committed readback、phase 保存证据、paused 后态、首题预算终态及原账务。失败分支只做 read_committed，不再次 execute；明确警告本地验证失败前管理事务可能已提交。

边界：固定 OUT 只排除同 wrapper 重复，原 lease 排除同时研究派发，不是 OS 禁止任意另起程序。准确 poststate 在 resume 前检查，仍须主控维持唯一 worker 控制；原 kernel 会复核当前状态和准入。监督者被强杀或持久存储整体不可用时，退出证据可能继续未知，不能凭目录、ready 状态或日志尾部猜退出码。启动失败后恢复必须先只读核进程、lease 和账务。

短回执：`worker_restart_static_review/002_final/receipt.json`，SHA `f7d08b7ce833d5daf814b79022e474f950d630b85057f02703735ed082f487be`。`001_before` 的源码拷贝与主控修复发生交错，实际已是 6db9；其发现文字属于此前读到的 18e4，最终回执已明确纠正该标签，不把它当运行失败或当前代码仍有缺陷的证据。

两问自检：**是否扩大原许可或隐式恢复研究？** 没有；只审查主控将独立冻结的 gate 和既有固定窗口衔接，审查本身没有创建许可。**本审查保证了什么？** 只保证所列源版本的静态控制逻辑与边界一致，不替代真实启动后的进程/退出证据和最终研究评价。
