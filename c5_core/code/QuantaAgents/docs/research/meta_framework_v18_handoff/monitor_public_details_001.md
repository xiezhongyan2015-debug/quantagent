# v18 调用费用与主控公开自检展示 001

2026-09-07。仅完成两个有界只读展示缺口，源码、专属测试和 JS 检查脚本已冻结。没有连接 v18 新阶段控制器、增加控制按钮、操作 8776 服务、修改实际账本或改变付费门。本子任务新增项目 Gateway 调用、真实网络访问、实际工作台动作均为 0；工程模型用量不据此记为 0。

`experiment_traces/meta_ashare_revision18/src/quanta_agents/meta/diagnostic_monitor.py` 的调用行现在显示保存的 `receipt.duration_seconds`、输入/输出 token、已知小计及未结清名义预留。真实零值保留为零；缺耗时明确未知，不以派发/完成时间相减补值。非法保存耗时（负数、布尔、字符串、非有限数）标记保存值无效。部分费用和未知费用继续保留名义预留，采用未改动的 `Store.call_budget_usage` 规则；旧记录既无调用预留也无预算预留时显示未知，不凭默认值伪造其额度。完整结算仍可确认预留为 0。名义预留不是供应商账单上限。

`public_calls` 保留原字段，新增 `accounting`、`reservation_availability`、`duration_availability`；费用由本次页面内各保存调用独立计算。`snapshot.controller_self_check` 只公开最新 checkpoint 的 `assessment`、`defects_uncertainty`、`next_step`、`falsifiers` 四个普通文本字段，以及 checkpoint 名与记录时点。每字段最多 4096 字符，截断单独标明；错误类型或缺失显示未报告，不退回旧 checkpoint 的说明。未知附加字段与嵌套对象不被序列化到该投影。页面使用 `textContent`，明确标为“主控公开说明 · 非模型隐藏推理”；其内容不代表新的验收、派发许可或进程存活证明。

保存的证据分属不同运行，没有复跑 v17 的 52 个后端检查、GUI 1 项或 JS 15 项：

| 路径（均位于 v18/monitor_public_details_validation） | 结果 | 回执 SHA256 |
|---|---|---|
| `001_before` | JS 4 个真实失败；Python 在进入 pytest 前因强制 UTF-8 与现有 Windows venv 路径文件编码冲突而启动失败，Python 测试执行数为 0 | `d05bf69448b83b61ba6dd337b202f208b2d201e4b3bcb6a5f69becd107b61258` |
| `002_before_python` | 恢复原环境后，新增 Python 10 项全部真实失败 | `80a58a33d7f10579f322f753ed410150321ea3bd2234920aa61d816c689dcabb` |
| `003_after` | 同组 Python 10 passed / 4.59 秒；JS 4 passed，输入执行前后不变 | `9ffc96f66d5ea768b53756bff03fea10114394cbdfac8b487e814ab602cb3932` |
| `004_source_proof` | 只读源码组成核验，未执行旧测试 | `2b3be56b3ae06f2366164e8087544bf781b5d397ef552d5b158c7b0a16f05030` |

反例包括：同页真实零、未报告、部分报告及显式 90000 预留；24 小时两时间戳不能填入缺失耗时；旧记录没有名义预留；非法耗时；最新自检缺失不能沿用旧说明；私有附加字段/嵌套对象不出投影；长文本有界；HTML 样式文本只作为普通字符显示。临时 SQLite 读取经过 SQL authorizer 限制，仅允许 SELECT/READ/FUNCTION，前后字节不变。JS 使用 literal API 形状及假 fetch，不等于真实浏览器或实际控制器整合验收。

最终身份：

- monitor：`87d236c7cab3a6073fd1e5c1291f51c4263904651fb5873daace606705e948a4`
- `tests/test_meta_monitor_public_details_v18.py`：`1cc61ebe909c2ed0770e2bb7b14391884182000593c363a0649b2d9cce34023c`
- `scripts/check_monitor_public_details_v18.js`：`91ea61ad8afba57c8e3976bf3b7f04df2c8759d794e5ed64a335a9e6709afd24`
- 最终展开 PAGE 的 JS：`95992a8e93a7c3fedab9b46e0859f349e524ee5f2cf72ff2d139997c123de05f`
- `003_after/source.diff`：`94f22b77abf6f353c4992560256fd323d24b017ee3558b1b6d10e1ae8643445a`
- `003_after/page.diff`：`f92d4558f7d79b178fed4996321ad957d4b75af4dc320baa7d86c30f11e30221`

AST 核对既有 Python 定义只改 `public_calls`、`snapshot`，新增三个投影 helper；Handler、注册路径边界、模型回答/工具结果公开路由及其余函数完全不变。展开 JS 的 `loadAnswer`、`getSaved`、`clearAnswer`、`loadCampaigns` 函数体逐字相同；`refresh` 只新增显示自检调用，`loadCalls` 只新增调用标签文本，去掉这两处插入后函数体与 v17 完全相同。原 v17 的 273 个复制来源文件仍全部匹配清单 `de8c84c0e7b41fcd5393869204df3a7f69e888634b9f995199fa477bb0d03c28`。没有把源码比较算成旧 15 项 JS 的再次通过。

两问自检：**是否把缺失计费或耗时包装成已知零，或以展示修改释放未知费用？** 没有；字段缺失明确未知，已知小计与未结清名义预留分列，预算和 Store 未改。**公开主控自检是否被误称模型隐藏推理、独立验证或实际运行许可？** 没有；来源与边界直接显示，缺失时不生成说明。

尚未完成：实际浏览器检查、服务部署、新阶段投影和批次按钮接线均不在本次范围。下一步由 root 在最终 v18 来源与新接口冻结后决定这些独立事项。若出现耗时推断、未知预留归零、旧自检冒充最新、跨调用/内容分页串线、公开字段包含私有结构，或源码/证据 SHA 不一致，应停止采用本展示增量并保留反例。
