# v12 监控异步身份修复独立复核 001

2026-09-07，Asia/Hong_Kong；Astra / xhigh。复核范围仅为 v11→v12 `diagnostic_monitor.py` 对异步显示身份的修复，以及同一个独立反例检查器的保存结果。没有修改源码、测试、服务、账本或原模型输出，没有启动研究调用。

**结论：所审 monitor 字节的源码修复和 7 项离线乱序反例通过，原 v11 GUI 竞态已在这一有界检查范围内关闭。** 真实浏览器是否已经加载该新源码、顺序与快速切换的实际画面检查，由主控另行保存证据；此报告不预先声称服务已替换或真实浏览器乱序已测。本结论不覆盖 v12 其他数据/研究改动，也不授权新付费。

## 原问题与修复

[v11 review_001](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/docs/research/meta_framework_v11_handoff/review_001.md) 保留了原 209 项验证通过，以及之后发现的异步 GUI 失败。原 JS 没有区分过期请求，导致旧 A 正文可以覆盖 B，并用当前 B 标识显示；换 campaign、分页和旧错误也存在对应问题。原 v11 不改、不追认通过。

本次 diff 只改变 PAGE 中的前端调度：

- status/calls/answer 各自有递增请求代际；成功和异常分支都检查代际，旧结果不得覆盖新状态。
- 调用列表请求捕获 campaign/offset，回应必须与当时请求和当前选择一致。
- 回答请求捕获 campaign/call/offset，回应必须同时匹配；标签使用捕获的 call，而不是后来变化的全局选择。
- 切换 campaign 或重新加载调用列表会作废 pending 回答、清除旧正文/标签并隐藏过期翻页按钮。
- 读取新回答时清空旧正文，避免请求等待期间继续把上一份内容留在当前选择下。

后端读取路径、JSON 字段白名单、SQLite `mode=ro`、分页上限、GET-only 和 localhost 绑定未变。没有新增 worker、gateway、回放、提交或任何账本写入。提交契约接受集属于已审 v11 的独立改动，此 GUI 修复未触及它。

## 独立反例复用与证据

原检查器 `docs/research/meta_framework_v11_handoff/review_artifacts_001/check_monitor_async_identity.js` 未修改，SHA256 `c450f4336fc8985c9c52cbc4ca21ad8dc932c078ce0520178d9332a89c01f636`。主控对 v12 PAGE 提取的 JS 原样运行它；独立复核读取结果并再次检查提取 JS 与当前 PAGE 文本完全一致。

| 检查 | v11 | v12 |
|---|---|---|
| 旧回答不能覆盖新选择 | 失败，A 正文标 B | 通过，保留 B |
| 旧回答错误不能覆盖新成功 | 失败 | 通过 |
| 旧 campaign 调用页不能覆盖新列表 | 失败 | 通过 |
| 旧调用页错误不能覆盖新状态 | 失败 | 通过 |
| 切 campaign 作废旧 pending 回答 | 失败 | 通过，正文保持清空 |
| 旧页不能覆盖更晚翻页 | 失败 | 通过 |
| 旧 status 错误不能覆盖新成功 | 失败 | 通过 |

结果文件：[async_identity_result_001.json](/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/experiment_traces/meta_diagnostic_monitor_v12/async_identity_result_001.json)，7/7。该实验使用虚拟 DOM 与手动延迟 fetch；忽略 abort 以直接核验代际防护，无真实网络、真实 campaign 读取或模型调用。它证明指定乱序场景，不能代替真实浏览器检查。status fixture 未提供真实时间，示例状态含 `Invalid Date` 是简化 fixture，不是实际服务时间观测。

| 冻结对象 | SHA256 |
|---|---|
| v12/src/quanta_agents/meta/diagnostic_monitor.py | c9fab3683cf6f5a6ad48ef428a57e43124a5e4b9e40b8df3d74c6a876b787bea |
| monitor_candidate_001.js | 0e47163f7c9ef7e2852b76e22d7d84ba69d3e00aa522ed45909b1f978011f1be |
| async_identity_result_001.json | 7e07c2c6042be96a8374094b79bc4cb4a295afac8d2e0315eab22ccfaa20ecd6 |

提取文件采用 Windows 文本换行，以上为实际 CRLF 文件字节哈希；读取后的 JS 与 PAGE 内的规范换行文本相等。没有用换行差异冒充源码变化或忽略实际代码变化。

正式展示下一步只需主控冻结这个 monitor 版本，替换本任务自己的只读服务，确认真实浏览器加载的新源及回答/运行切换行为，并保存调用数、费用和原失败状态没有变化的证据。不需为此次只读 UI 修复重新派发两题或重跑市场研究。

两问自检：**是否为了关闭问题更改反例或仅看通过数字？** 没有，检查器哈希未变，逐段复读了实际修复与 7 项结果，原失败 artifacts 均保留。**是否将离线模拟包装成真实浏览器、供应商或研究成功？** 没有，结论只覆盖指定 GUI 竞态；两题原 `failed`、13 调用及 238797 I/O token 不能因此被重写。工程评审自身模型用量另列。
