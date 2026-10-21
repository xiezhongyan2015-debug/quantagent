# v18 一次补充窗口独立反例预期 001

状态：**事前预期已冻结，尚未执行这些独立测试。** 本文依据 `post_deadline_research_design_001.md` 与 root 的补充边界编写：v18 可经资源 hook/同库 adapter 续写当前治理进度，但不得修改原 v17 源码或把旧 paid=false 改成 true；旧完整终态先冻结，每个治理事务保留 before/after 审计。新增绝对窗口是额外时间资源，不回填原六小时结果，不增加原四任务分母。

独立测试限定合成公开算术 fixture、fake Gateway、临时 SQLite；不打开 actual 账本/数据、不派发模型、不重跑作者全组。文件预定为 `experiment_traces/meta_ashare_revision18/tests/test_meta_casebank_supplemental_independent_v18.py`。等待作者提供稳定 API 与 fixture 后再运行，接口缺失不作为产品失败。发现真实反例时先保存失败、输入和源 hash，再通知作者修复。

| 编号 | 合成干预与手算预期 | 必须观察到的边界 |
| --- | --- | --- |
| S1 默认资源语义 | 不提供新阶段许可，原时钟已过；另测旧 admission paid=false。 | 默认 continuation 仍在 reserve/Gateway 前拒绝；不能仅靠 v18 导入或新版 hook 延期。原 created_epoch、21600 秒、旧 admission 原字节不变。 |
| S2 唯一累计预算 | 起点为两笔原传输：11235 已知 + 指定旧 unknown80000。新一次 reserve80000 后合计171235；设首题 cap=171234 必拒，171235 才能恰好容纳。构造累计第18/69次、或已消费全部原候选/终稿机会的边界，不把新阶段调用数从0当成累计。 | 原 17/68 调用、16/12/1 动作、600000/2400000 名义门与80000 reserve 按同一账域核算。新窗口不抵扣旧11235/80000；失败/重启不返还未知成本。 |
| S3 真实并发唯一性 | 两个独立进程共用同一临时账本，同时 prepare 相同新阶段，再尝试不同 id 的第二补充窗口。 | 至多一套阶段记录/关闭快照，没有重复扩窗；同一已提交 prepare 可幂等读回。第三时间窗口被拒，新 call/window/permit 不因另一进程更换 id 获得免费机会。 |
| S4 截止与 pause | 冻结固定 start/end；当前时间在 start 前、end 后以及 pause 后，分别尝试新的预留。 | 全部在新 Gateway 前拒绝；等待/暂停不移动 end。派发后普通在途推理仍按已冻结单次时限收尾，不将其假称截止前完成。 |
| S5 新未知全停 | fake Gateway 在新 intent/预留持久后中断，没有完整 usage/receipt；然后 load/recover，并尝试下一题或新 round。 | 新80000继续保留，当前补充全阶段停止；没有自动重发或工具重算。唯一旧 quarantine 不得泛化到新的 unknown。 |
| S6 保存来源先应用 | 原纠错 completion 已保存，但尚无其完整 WB/continuation 应用证据；分别尝试 public_model_prompt 与内部 reserve。 | 两入口均拒绝，不能跳过已付费回答。若 pending WB 缺产物也停；只有显式已完成的原保存动作可进入完整本题 history，不额外 WB.execute。 |
| S7 不可改写历史及两库中断 | 保存旧 scope/admission/calls/actions/关闭终态；在 WB 回执已落、治理尚未结算时中断。 | 旧模型与 action 前缀正文原样；可变治理进度变化必须逐事务 before/after 关联旧完整冻结终态。恢复只结算完整已存结果，不重算、不构造 Gateway；重复结算不增加 query 或费用。 |
| S8 新阶段身份与投影 | 完整新调用已保存后，单独替换 phase id/source manifest/admission hash/task identity，或把另一题同值 inspect 回答接到当前题；也尝试修改当前缓存的预算或窗口。 | 调用消费和只读投影均拒绝身份漂移；新阶段必须显式绑定 source/admission，不沿用旧许可。GUI 应计原2+新调用及所有预留，旧/新时间资源和后续终稿归属清楚。 |

上述是最多八个核心场景，不承诺每个都新增重复实现测试：作者已有直接证据可读验收，独立执行优先选择作者未覆盖或最可能突破资源/恢复边界的反例。有限参数化可用于同一数学边界，不能把多次同实现复跑冒充独立研究重复。具体 API 映射与实际执行数量将在后续 receipt 中据实列出，不改本事前预期。

两问自检：**新增时间是否被包装成原阶段成功或免费调用？** 没有，补充阶段明确新增时间，原费用、失败、上限和四题分母保留。**本文件是否提供了执行准入或宣称测试通过？** 没有，仅冻结反例与判据；实际 scope/admission、产品修复和工程通过仍待独立证据。
