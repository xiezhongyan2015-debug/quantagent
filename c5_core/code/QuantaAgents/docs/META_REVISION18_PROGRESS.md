# 第18版：一次明确的补充研究窗口

**工程收尾后继续暂停（2026-09-07）：** 用户仅授权完成现有工程与记录、不新增付费研究。预算管理器最终源码`fad68b9`已完成作者5项加修正测试单项、独立8项有界验收，原失败保留；管理器未应用，后三题未恢复，关闭策略仍仅离线验证。已有自动化a保持PAUSED，8776只读页面保留，原补充窗口不延长。最新checkpoint031；整体结论见[整波V2复盘](META_V2_WAVE_RETROSPECTIVE_20260907.md)。下文是此前各阶段事实和当时下一步，不能代替当前用户暂停决定。

当前结论：**未达标。补充阶段因首题累计预算门暂停，唯一研究worker已退出；首题没有最终产物可评。** 累计15次传输、14份完成回答（13份本阶段加原纠错）、491,954已知token及原80,000未知预留。首题14查询、12候选、0 final；后三题尚未开始。下一80,000预留将使首题暴露651,954，超过原600,000门，控制器正确拒绝派发。原六小时到期未完成、原失败和四题描述性分母不变；没有挑选中间最佳结果。

主控仍是 `01a0774d-4327-7701-948f-2796de6fccd3`，沿用已更新并回读确认的监控 `a`。第18版实际页面继续使用 `http://192.0.2.10:8776/`，监控parent80840/listener99764；原本任务的第17版服务已核身份后替换，受拒绝保护的旧8769/8773未操作。真实浏览器已核耗时、费用、主控自检，以及实际补充窗口/原截止/新旧许可分列。主研究worker于04:34:47 UTC唯一启动，parent59904/python91624；进程存活须重新观察，不能由保存PID推定。原第17版付费许可仍永久关闭。

## 这次改进与证据

新增的补充阶段保留同一四题、顺序、公开资料、完整本题历史、原量表和所有累计费用/动作配额。实际唯一窗口冻结为 **2026-09-07 04:16:47.524738–10:16:47.524738 UTC**（香港12:16:47–18:16:47），等待、准备和暂停均消耗窗口，未在04:34启动时重置。它明确增加了时间资源，不重置原截止、不形成新的独立启动、不授权第三个窗口。具体scope经过独立只读五库/来源核验，再另建新phase许可并唯一登记；政策文件自身仍不构成派发许可。

控制器作者16项与独立4项在同一次离线运行中全部通过，耗时272.172秒，运行前后源码与测试哈希一致。包含真正两个临时进程竞争同一准入、原费用预留继承、旧入口拒绝、完整保存历史、动作提交后崩溃不重算，以及使用冻结第17版真实源码和测试构造器的迁移。此组使用第18版核心 `6e38e602a1850930cd5bb2fe3d43f7f6a7b35e4c4fe62953506e759de04f9919`、补充模块 `30517106bf9156c6c684abbd0794fa9c8387e902f29085932cd0785774025749`。它不覆盖随后发现的截止时刻缺陷。

独立新增的唯一截止反例曾实际失败：窗内预留之后，本地派发时间恰越过截止。没有启动模型进程，11,235已知token与160,000累计未知预留都保留，但读取状态报错，治理状态未能暂停。现已作最小修复：时间违规保持账务可读，阻断新模型和新动作。原反例与正常窗内调用在截止后收尾的正例在002通过；独立新增完整但晚派发记录的用例因测试自身微秒表示断言先失败，保留原件后只改单项期望，004完整通过费用及新准入阻断。没有把这几次运行合成一次全套通过。最终phase为 `83e04e8875fe4ebf85ec5ab6ad931545cac8934e2f6cd90ae91d6993475aa392`，其四函数差异与其余不变部分有独立证据桥接。

第18版保存耗时、用量、未知预留和主控四项自检展示有新增10项Python、4项JS检查与独立复核。补充阶段接线另有4项Python、4项JS及独立复核：真实控制器保存调用→应用→监控、无phase兼容、坏phase不能回退、时间违规仍能显示费用均覆盖。最终monitor为 `038f660f7530d583673d389531a7999f0d3e1cf9351efe7694131ccf4e41860c`；107项源码集合哈希 `a44654292035a10cc380b90acaf24d56fb6da689de4fbab2fa16d3a00968885f`，原第17版273个继承文件未改。页面明确保存状态不等于进程存活证明；主批次控制按钮仍未完成。

2026-09-07 03:48:41 UTC，在原第17版只读核验后，已保存原campaign和四个workbench的五份完整SQLite快照，所有源库前后字节哈希相同；原来源、保存观察和治理状态再次闭合。未创建窗口、未增加模型或工作台动作。新快照是审计基线，不是新的研究样本。

## 有效边界与下一步

补充阶段起点累计两次传输：11,235已知token和原失败80,000未知预留，均未重置。第18版每题17次/600,000名义token、全域68次/2,400,000、每次80,000预留、16查询/12候选/1最终提交都从原账本继承；新unknown停整个补充阶段。在途预留与终态未知费用分开解释，阈值不保证供应商账单绝不超额。平台目标计数在04:00:14 UTC记录13,034,382 token，另存工程观察，不能冒称工程零成本或把不同口径相加充当供应商账单。

这些任务仍为暴露的合成开发材料。成本未实际应用、执行未合格、正式策略分母增量为零。真实PIT、股票池、交易状态、公司行动、实际费用和容量等覆盖缺口仍需补齐；原846股盘点和192项未认证义务不因此改变。

自检：13次新调用均完整结算和应用，未重问或重算，新unknown及工具pending为零，预算停止有效。独立评审冻结145个保存文件hash，按原量表判首题未完成；不把控制器停止冒充研究者弃权。保存元数据审计显示最终调用前已有明确余额83,978、候选余量0、final余量1，但schema仍同时允许查询与提交；模型选择查询，结算55,932后无法再准入。输入占已报告用量98.086%，末prompt的公开工具历史占字节92.224%；逐轮完整本题历史是原合同，不能偷删。控制器缺少提交专属名义额度，这是下一新版本的可证伪改进，不能反推模型必然会写出合格final。工程投入很高，当前没有最终研究质量或策略收益达标证据。

下一步按原scope明确许可，另行冻结“已知单题预算停止”的本地管理步骤。拟核验完整已保存证据后，将首题治理状态记录为budget_stopped并推进原序case02，阶段仍保持paused；不改WB、调用、动作、费用、旧许可、时间、评分或final。该模块尚未验收与应用，checkpoint029关闭当前主控派发门，原worker不得重启。独立核验通过后，剩余未开始题只能在同一v18s1窗口和原全部配额内继续；首题不再获得调用。收尾额度改进另作离线验证，不夹带进后三题当前研究合同。新unknown、pending、原记录漂移或债务释放均阻止恢复；不自动第三窗。

真实浏览器已核全部15/15调用、阶段paused与原expired标签。发现当前public API和banner遗漏具体预算原因，且首题治理ready显示待执行；此缺口已记录，不能声称展示完整。实际只读捕获002回执SHA `398d8bddc5cb0e90186a39ccfedd981d48bf7a3fbe7ef6fbc5bd609ccd3e5005`；001因错误假定API包含pause_reason而KeyError，原捕获和脚本保留，无实际数据库改动。最新checkpoint029 SHA `f9a2a5ed70496ef3ab7cbca98d4b7eabca20e85106893ead955fd56611a53bce`，已有监控a更新；保存PID不代表持续存活。

## 可追溯记录

- 政策：`docs/research/meta_framework_v18_handoff/supplementary_research_policy_001.json`。
- 独立设计：`docs/research/meta_framework_v18_handoff/post_deadline_research_design_001.md`。
- 联合20项：`experiment_traces/meta_ashare_revision18/supplemental_validation/root_joint_001/receipt.json`。
- 截止反例修前：`experiment_traces/meta_ashare_revision18/supplemental_independent_validation/001_deadline_before/receipt.json`，SHA `842da9ad445c322f8092f8d27643e6aa993633049af8eeb489984bf832398d97`。
- 五库保存：`experiment_traces/meta_casebank_supplemental_preparations/reference_v15_original4_001_preservation_001/receipt.json`，SHA `9dd35d09e7ba7202b96e6b2db9fc6d3b446ce87482bc4be47e8e790e4a8cabec`。
- 已验收展示组件：`docs/research/meta_framework_v18_handoff/monitor_public_details_001.md`及独立复核文件。
- 最终有界工程验收：`experiment_traces/meta_ashare_revision18/validation_artifacts/bounded_engineering_acceptance_001/receipt.json`，SHA `58ed0c0c061de7de8781004af6a32c9062ec0c0f9e889d67e23189a429195b75`。
- 实际scope：`experiment_traces/meta_casebank_supplemental_preparations/reference_v15_original4_001_v18s1_scope_001/scope.json`，SHA `8d60a4fdef3d94c4f952edb721f9d620c62a17f51c760e2b67a4e9ed9beef66b`；独立实际准入回执SHA `059620e3e117dd09c6c961704c34d752f754bd7ce6d6d9cdf25c4ad1d3f31c7d`。
- 新phase许可：`docs/research/meta_framework_v18_handoff/supplemental_dispatch_admission_001.json`，SHA `e57a5ed310ea7d8abd3fa43177fe0e07e1797d7665f00387c52cb8b5f230a163`。
- 唯一实际登记：原campaign的`v18_supplemental_registration_001/receipt.json`，SHA `74208e577ba4871486dd53a4f71b9ee7071836b23dbfdef7c4c06e878f6c7a04`；启动与动态观察保存在同campaign的`v18_worker_001`。
- 当前交接检查点028与已有自动化`a`已更新，正式目标保持未达标。

该文为持续更新的进度说明，不替代冻结范围、独立验收或付费准入文件。
