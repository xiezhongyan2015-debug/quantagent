# v18 截止竞态独立失败证据 001

状态：**真实合成反例已失败，尚非修复通过。** 在补充窗口内完成预留和授权检查后，仅将合成时钟推进至窗口结束后 0.01 秒，再执行既有 `dispatch_started` 记录边界。没有改变业务返回、预算或保存事件内容来制造结果。

`test_meta_supplemental_deadline_race_v18.py` 实际 1 failed / 23.39 秒；源码和测试前后 hash 一致。回执：`experiment_traces/meta_ashare_revision18/supplemental_independent_validation/001_deadline_before/receipt.json`，SHA `842da9ad445c322f8092f8d27643e6aa993633049af8eeb489984bf832398d97`。同目录保存完整 JUnit、修前源码、输入清单及 `race_observed.json`；合成临时目录 `D:\q18d_3120de55` 保留。此测试未打开实际账本/市场/题库，也未启动模型进程。

失败源版本：core `6e38e602a1850930cd5bb2fe3d43f7f6a7b35e4c4fe62953506e759de04f9919`；supplemental `30517106bf9156c6c684abbd0794fa9c8387e902f29085932cd0785774025749`。独立测试 SHA `ca8b112a6cfa62d9033ac9f96e2df7e0b542d9c18e1fdbb55f986cebc3a14fc9`。

观察结果：fake Gateway 的 callable 进入一次，但其 preflight 立即关闭，模拟模型进程尝试数为 0。新调用保存为 failed；已知 11,235、未结预留 160,000、名义 exposure 171,235、unsettled_calls=2 全部保留。随后 `run_remaining` 异常处理和只读 `summary()` 都被 `saved dispatch is outside its explicit phase window` 拒绝，治理记录仍 ready，未完成预期 pause。

根因是 `_call_window` 对未完成调用也要求记录的 dispatch 时间严格在窗口内。该规则被读侧 `_verify_rows` 使用，导致合法保留的跨截止失败记录使 `_check`/投影不可读，而暂停又依赖该校验。问题属于 P1 账务可见性与停止状态缺陷；现有反例未显示额外派发或费用释放。

最小修复验收条件：该失败与其预留不得删除或改成零；未完成调用可如实保留供投影显示，后续新调用继续因截止/unknown 停止；窗后本地应用仍要求本阶段窗口内准入、派发且已有完整完成回执，不能将宽容展示变成执行许可。根代理批准修后仅运行这一反例，并用精确差异/AST 与已运行的其他定向证据组合；不将修前测试改名为修后通过。

两问自检：**是否声称发生了重复付费？** 没有，模拟进程尝试为 0，缺陷是保存未知记录无法读取和暂停。**是否为通过测试删费用或放宽窗后执行？** 没有，此处只保存修前证据，修复须同时保留账务与严格应用资格。
