# v17 独立前置预期 001

范围：Astra / xhigh 独立评审；只用合成公开算术 fixture，不读取实际四题工作台、private、真实行情，不调用 Gateway / API。本文先于读取或运行 v17 新策略模块结果保存。预期由合同和手算确定，不能把作者测试复跑当作独立结果。后续测试仅增加实现适配，不根据实际输出改预期；若合同确有歧义，另存澄清记录。

事前依据：

- `saved_action_continuation_design_001.md` SHA256 `feb326df05463f9a0a8a2fc8063e4e0642015f994d3dfaf8f6a2901e8a80bc14`。
- v16 handoff `controlled_strategy_v17_interface_design_001.md` SHA256 `c39c72fbc8fee000c8fdda0eb94e14c629b156851825f0674ad73cf387efb35f`。
- `strategy_development_contract_001.json`：公开预算 24 actions / 4 candidates / 4 internal variants / 16 queries / 4 manual memory actions；每次 develop 请求固定 compile、target generation、raw mechanical execution 三个 requested subattempts。
- 已读继承 `factor_algebra.py` 的公开函数语义；未以其计算下列 expected。

## 续行七项独立预期

1. **真实并发的唯一性**：两个独立进程争抢同一新 transport 或同一 `(task, WB slot)`。最多一个取得可执行身份；另一方必须拒绝或只观察已有状态，不得产生第二 Gateway 派发或第二工具执行。只模拟进程和合成 SQLite。
2. **原预算与原时钟**：原 failed unknown 保留 80,000；原纠错 complete 11,235。开始是 2 次 transport / 91,235 token exposure；下一次预留后是 3 次 / 171,235 exposure。必须同时进入 task、stage 上限，原 deadline 不能由新 constructor / prepare / resume 重新起算。原阶段少于足够剩余额度或已过期时，零新增派发。不得从旧 run 中删除 failed 行绕过计数。
3. **历史回答与当前 WB 状态分开绑定**：原纠错 receipt / prompt / request / schema / intent / process exit / response 仍按原始身份验证；WB 前进后，同一已应用来源只能 saved-only 重放，不能因当前 WB state 与旧快照不同要求重新付费，也不能将历史来源绑定到新 slot。
4. **跨库崩溃**：intent 已保存且 WB 尚无 slot 时，纯 inspect 不执行；WB pending 且无完整产物则 unknown，禁止新 slot；WB 已有完整 terminal receipt 而 parent 未 apply 时，只读核验可恢复一次结算，不重新调用 WB.execute / kernel。保持原失败与 requested subattempt 分母。
5. **纯只读路径**：在 SQLite authorizer 拒绝 INSERT / UPDATE / DELETE / schema 写入、Gateway 构造与 WB.execute 均设陷阱时，inspect / summary 仍可读取已完成状态。不得间接调用旧 WB.prompt 或其写型 recover。
6. **本侧完整公开上下文**：下一请求必须包含本侧原公开合同、实际 saved correction action、实际查询页、随后每个公开 action / observation / 已知失败；不得凭空添加旧失败的 assistant 回答，不得读全量 backing table、其他 task / arm、private 或隐藏推理。超预算完整上下文应停，不截断历史。
7. **终态和未知分轴**：已知 query failure 消耗机会并按合同可继续；invalid final 保留 sole-final 终态；任一新费用未知 / pending action 停止全部后续派发。原已单独豁免的旧 80,000 不能扩展为豁免其他未知调用。

## 策略手算 fixture

符号固定顺序 `sh600001`, `sz000001`，简称 A / B。会话固定：

`2019-01-02, 2019-01-03, 2019-01-04, 2019-01-07, 2019-01-08, 2019-01-09`，简称 d1…d6。

公开 dimensionless 字段 x：`[(1,4), (4,2), (3,6), (8,4), (5,10), (12,6)]`。除特别说明外，每格显式存在，effective_at 和 available_at 均不晚于自身会话 `15:10:00+08:00`，资格均为 true。值和预期均为手算字面量。

所有有效候选目标恰为两股 × d2…d6 的十行：无 d1 订单，不删除 NaN / 不合资格行；d6 是最后 trade session，目标强制零，保留 d5 原 weight / reason。d6 的原表达式值单列 `untraded_terminal_signals`，不制造 d7。

### S1：不同程序及顺序依赖

程序 P1：`fast = rolling_mean(x, 2)`；`gate = where(fast > lag(x, 1), 0.25, 0)`；target `gate`。

原始 d1…d6：`[(null,null), (.25,0), (0,.25), (.25,0), (0,.25), (.25,0)]`。

最终目标 d2…d6：`[(0,0), (.25,0), (0,.25), (.25,0), (0,0)]`。

程序 P2：`delta = x - lag(x, 1)`；target `where(delta < 0, 0.4, 0)`。

原始 d1…d6：`[(null,null), (0,.4), (.4,0), (0,.4), (.4,0), (0,.4)]`。

最终目标 d2…d6：`[(0,0), (0,.4), (.4,0), (0,.4), (0,0)]`。两个候选不能被固定模板替换；前向引用 / 函数遮蔽失败。

### S2：自身截止与迟到值不回填

target `lag(x, 1) / 100`。B 的 d2 值 2 仅在 `15:10:01+08:00` available，永久屏蔽。A 的 d2 在恰好 `15:10:00+08:00` available，允许。

原始 d1…d6：`[(null,null), (.01,.04), (.04,null), (.03,.06), (.08,.04), (.05,.10)]`。

最终目标：`[(0,0), (.01,.04), (.04,0), (.03,.06), (0,0)]`。无时区时间戳为输入失败。修改未来格不得改变既往目标。

### S3：嵌套横截面资格与最终资格

target `lag(cs_rank(x), 1) / 2`。d1 仅 A 合资格；d2 两者；d3 仅 B；d4…d6 两者。

各日 rank：`[(1,null), (1,.5), (null,1), (1,.5), (.5,1), (1,.5)]`。

表达式 raw：`[(null,null), (.5,null), (.5,.25), (null,.5), (.5,.25), (.25,.5)]`。d3 A 最终目标权重为零，因为自身 d3 不合资格，即使 lag 值有限。

最终目标：`[(0,0), (.5,0), (0,.25), (0,.5), (0,0)]`。资格本身 available 晚于自身截止，同样按不合资格处理；不得用后来的资格补排历史 rank。

### S4：缺值不能压缩时间轴

A 的 d2 值 null；target `rolling_mean(x, 2) / 100`。

原始 d1…d6：`[(null,null), (null,.03), (null,.04), (.055,.05), (.065,.07), (.085,.08)]`。

最终目标：`[(0,0), (0,.03), (0,.04), (.055,.05), (0,0)]`。A 的 d3 不能绕过缺失 d2 使用 d1；重复坐标 / 缺失元数据应输入失败，不能删行维持成功比例。

### S5：权重边界与失败分母

两股常量 `.5` 恰好合计 1，有效。两股 `.5000000000000001` 的 `Decimal(str(float))` 合计为 `1.0000000000000002`，整个候选失败；没有默认容差、裁剪、重归一化或 raw 执行。`x / 0` 的非有限结果为显式零目标并保留原因。任何 finite 负数 / 大于一，包括本应被最终清仓覆盖或没有后续交易日的末行，不能被覆盖掩盖。

### S6：下一会话与末日清仓含义

两股常量 `.2`：d2…d5 各 `.2`，d6 各零，末日未交易信号仍各 `.2`。原 signal / availability 与下一 trade session 绑定；无同日成交目标、无末日后新会话。零 target 本身不证明订单一定成交或组合无库存；如 raw 合成 fixture 给出末日不能卖出，应保留库存 / 状态，不宣布 execution_valid。

### S7：事前机会登记与重复失败

forward reference 或有限超长 JSON 的已识别 develop action：登记一次 action、candidate、internal variant 与三项 requested subattempts，之后失败；target / raw 陷阱不得触发。相同 slot / 相同原始请求只 saved-only；改字节拒绝。新 slot 的 settled duplicate 再收机会、复用结果零新计算；unknown duplicate 无法换 slot 重试。

### S8：原价子任务完成后的恢复

合成 raw child 完整 receipt 已落盘而 parent 尚未 settle 时，设置 compile / build_targets / raw create / execute / simulator 陷阱；恢复只验证已保存全产物并恢复一次账务，不重新执行。缺失或 pending 的先前子任务必须停；不得把未完成转成零成本失败再重试。只在作者提供稳定真实合成接口后实施，不为接口尚不存在运行空失败。

## 两问自检

1. 是否通过变更样本、时点、权重归一化、删除失败或重置时钟使结果更漂亮？预期均明确禁止；本记录没有产生策略收益或修改实际分母。
2. 是否把工程可执行、合成 fixture 通过或本侧 hash 一致当成研究发现 / PIT 真实性 / 供应商身份 / 架构稳定性？没有。本文仅冻结可反驳的本地工程预期；实现与结果尚待验证。
