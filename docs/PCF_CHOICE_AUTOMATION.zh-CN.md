# 563030 PCF收盘归档及Choice次日执行

本程序沿用前一个交易日PCF策略。T日16:00从易方达官方接口归档T日PCF；T+1交易日09:35开始生成目标并执行限价委托。例如周五的PCF用于下周一，而不是寻找周日清单。

**交付状态：已在获授权的Choice比赛模拟测试中确认提交、查询、撤单及`Data.orderID`回执字段；测试订单已撤。交易时段撮合、通用可卖字段和成交唯一编号关联尚未验证。** 默认模板仍为本地paper演练，不能因复制配置而开始账户交易。另提供受资金上限约束的初始买入测试模式，见`PCF_INITIAL_BUILD_TEST.zh-CN.md`。

## 与已有程序的关系

- `factor_lab_a.pcf`仍只输出目标持仓CSV/JSON。
- `factor_lab_a.pcf_automation`新增收盘归档、前一交易日选择、目标生成、定时入口和持久订单日志。
- `factor_lab_a.choice_pcf`实现Choice比赛HTTP查询、限价委托与成交确认，不是券商实盘SDK。
- 原LightGBM日任务不变。Choice执行要求独立PCF账户，不能把五个策略共享的总账户当作PCF子账户；统一子账执行器未在此PR实现。

新脚本09:35才开始，逐笔成交后继续；此前历史回测是假设次日开盘成交。历史7.26%半年超额收益不能当作该执行器收益，需另收集真实成交再评估。

## 运行准备

安装项目及研究依赖，或设置`PYTHONPATH=src`并安装`research/pcf_follow/requirements.txt`。将`config/pcf_automation.example.json`复制为同目录`pcf_automation.local.json`，后者已被Git忽略。所有相对路径及数据更新命令以配置文件所在目录为工作目录。

程序不会虚构缺失行情、当前指数成员、可卖数量或交易所日历。以下数据须由现有日更/行情程序提供：

|文件|必需内容|
|---|---|
|calendar.json|交易所实际交易日数组，如`["2026-09-17","2026-09-18","2026-09-21","2026-09-22"]`；需覆盖PCF前一交易日至未来交易日，例子不是全年日历|
|close_quotes.csv|code,date,close,reference_date,reference_close,member_csi500,member_union,is_st,tradable；date是PCF日期，reference_date是该PCF的PRETRADINGDAY，两个价格均为不复权价格|
|live_quotes.json|以SH600001格式代码为键，每行含带时区timestamp、bid、ask、last、lower_limit、upper_limit、tradable、is_st|
|paper_account.json|仅paper模式使用，含cash、nav、frozen、positions、asof；真实Choice模式读取服务器账户|

实时行情示例（虚构数据，不能用于交易）：

```json
{"SH600001":{"timestamp":"2026-09-22T09:35:00+08:00","bid":10.0,"ask":10.01,"last":10.0,"lower_limit":9.0,"upper_limit":11.0,"tradable":1,"is_st":0}}
```

paper账户初始示例：

```json
{"asof":"2026-09-21","cash":1000000,"nav":1000000,"frozen":0,"positions":{}}
```

行情时间必须为源行情真实时间，不得以文件写入时间掩盖陈旧行情。当前默认30秒后拒绝下单。运行期间可由独立行情服务原子更新live_quotes.json；也可配置`quote_command`在每次读取前刷新，命令中的`{code}`会替换为当前证券。新增`python -m quanta_agents.factor_lab_a.choice_quotes --code SH600001 --out live_quotes.json`对接Choice公开行情，按f59缩放价格、保留f86源时间戳，不使用账户凭据。`close_command`和`open_command`负责操作者接入的数据更新。命令为argv数组，失败停机；指数成员和日历仍需提供有来源的快照。

## 命令与定时任务

```powershell
python -m quanta_agents.factor_lab_a.pcf_automation --config config/pcf_automation.local.json close
python -m quanta_agents.factor_lab_a.pcf_automation --config config/pcf_automation.local.json execute
python -m quanta_agents.factor_lab_a.pcf_automation --config config/pcf_automation.local.json daemon
```

默认execute/daemon都使用paper。paper会修改本地假账户，按限价立即全部成交，仅验证流程，不模拟真实撮合、滑点或市场收益；买卖费用各0.001，新增持仓当日不可卖，下一交易日才解锁。

Windows任务安装入口（操作者手动运行；默认收盘归档+paper）：

```powershell
./scripts/Install-PcfTasks.ps1 -Config ./config/pcf_automation.local.json -Python C:/Path/To/python.exe
```

脚本注册每天16:00和09:35两个任务，由Python按交易日历跳过休市日。Windows时区要求UTC+08；仅当前用户登录时运行，电脑必须开机且有网络。任务不补跑错过的开盘、不在失败后自动重试。已有同名任务时拒绝覆盖；不要同时启动daemon和任务计划。日志在output/pcf_automation/tasks/。安装脚本本身不立即启动任务。Windows实际任务注册未在本次环境中执行，仅完成语法解析。

## Choice适配器配置边界

从用户提供的2026-09-20调研结论出发，重新核查了Choice公开api.js、index.js、http.js、config.js。来源和SHA256在`research/pcf_follow/choice_frontend_manifest.json`。仅提交来源清单，不上传网页完整源码、账号、登录状态或用户调研中的资产信息。

已从前端确认：基础地址`https://choicelabapi.eastmoney.com//emlab-api/`；查询SpoBalInfo、SpoHold、SpoOrders、SpoDeal；下单POST AguTrade/SpoOrder，撤单POST AguTrade/SpoCancel。买入orderDrt=1、卖出=2，固定限价orderType=1，沪市mktCode=1、深市=0，stkType=1。没有发送遥测请求。撤单入口需要单独验证和显式启用，公开前端字段不是服务器实测结果。

2026-09-20获授权的模拟诊断明确了下单响应字段：`Data.orderID`，注意最后两个字母大写；委托查询列表仍是`orderId`。默认配置及适配器已修复，禁止把查询字段大小写套用到下单回执。以下其他字段仍需独立验证：

- `sellable`：持仓记录中可卖股数的点分路径，不能用总持仓count代替。
- `deal_order_id`：成交记录中关联委托号的字段。
- `deal_id`：成交记录唯一编号的字段，用于发现重复明细。

旧测试中的canSell/orderId/dealId仍是**虚构契约**；新增回归使用已观察到的Data.orderID响应结构和虚构编号。一般调仓模式须验证可卖数量与成交关联等完整契约后再启用`validated_with_authorized_test`。服务器字段变更时停止，不按股票、价格、数量猜测缺失委托号。

账户凭据只接受进程环境：CHOICE_ACCOUNT_ID、CHOICE_JTOKEN、CHOICE_UTOKEN、CHOICE_CTOKEN、CHOICE_UID。由操作者现有登录/会话管理程序注入，不放入配置、命令行参数或Git。程序不从浏览器提取Cookie，也不处理自动续期。任务计划进程是否继承有效会话需要操作者部署时验证。

完成契约与数据接入后，操作者可设置enable_choice_orders=true、exclusive_account=true，通过`execute --mode choice`或`daemon --mode choice`运行。任务安装脚本的`-EnableChoice`切换09:35任务到Choice模式。**提交此PR与通过离线测试均不等于已获服务器验证或已部署自动交易。**

## 执行及故障处理

1. 前一交易日PCF归档必须存在，日期、哈希和抓取时间必须匹配。允许周末保存周五PCF供下周一使用，但必须保留真实抓取时间且早于执行日；不接受执行日补抓后冒充已提前归档。重复归档不覆盖首份版本；修订触发REVISION_BLOCK。
2. 按现有PCF算法生成目标，买卖各0.001预留费用，并在现价下复核现有比赛持仓限制。PCF组合不满足限制时停止，不擅自改造成另一个策略。
3. 先卖后买，每笔提交前检查实际现金、可卖数量、账户持仓变动、未完成订单、行情时效、涨跌停范围、整手及金额上限。按卖一买入、买一卖出发限价单，价格相对上一收盘默认变化超过3%则停止。
4. SQLite先持久化SENDING再调用HTTP。POST不重试；超时或缺少委托号记为UNKNOWN并停止整批。整个账户同一日期只允许一个PCF批次，改变目标也不生成第二批。
5. 委托状态4、累计成交数量等于委托数量，并且按委托号关联的去重成交明细数量吻合，才记整笔完成。部分成交实时更新本地订单日志；等待整笔完成前不继续后续委托。超时或STOP时停止整批；默认不自动撤单，服务器上仍可能有未完成订单。
6. 登录过期、业务码错误、分页截断、缺字段、库存冲突均停止。不会把查询失败当作空账户。

在state_dir创建名为STOP的文件可以阻止后续新委托；默认不会撤销已发送订单。进程崩溃留下.lock时先核对进程与服务器订单，不能为重跑而直接删除SQLite日志。已知委托号可以只读对账：

```powershell
python -m quanta_agents.factor_lab_a.pcf_automation --config config/pcf_automation.local.json reconcile --date 2026-09-22
```

该命令只查当日订单并更新本地状态，不猜测缺失委托号，不发送或补发任何订单。不完整批次保留待核对状态；部分成交账户按服务器库存为准。五个策略共用账户时须先接入已有统一执行/子账系统，不得把exclusive_account开关当作已经实现账户隔离。

## 未成交与撤单预案

`cancel_on_timeout`默认false。只有独立验证撤单响应及状态转换后，才能把`choice_contract.validated_cancel_with_authorized_test`设为true，并启用`cancel_on_timeout`。缺少验证时，执行器在任何新委托前拒绝该配置。模板仍关闭两个开关；目前只完成离线验证。

启用后的执行行为：

- 超时或STOP出现时，先持久化撤单意图；按本批次返回的委托号，核对证券、方向、数量及限价后至多请求撤单一次。已成交、已撤、废单不再发撤单，正在撤单时只查询。
- 撤单接口Code=0仅代表请求反馈，不能解除订单风险；继续查询委托和关联成交明细。撤单超时也先查询，不重复POST。
- 确认全部成交，或已撤/部撤/废单且关联成交数量吻合，才保存终态。撤单期间新增成交计入实际数量，不按原始未成交数量补单。默认最多等待30秒，未确认则保存REVIEW_REQUIRED。
- 无论撤单结果如何，整批停止，不自动追价或补发剩余数量。同日未完成批次仍被日志拦截。独立只读对账不自动解除该限制。

当前没有自动改价补单。后续若增加，需要先确认原单终态、实际成交和资金/持仓，再计算剩余量，并同时约束总滑点、总费用、次数和执行截止时间；撤单失败或未知时禁止新单。买卖费用仍按各0.001预留，不将追价成本排除在评估外。未完成组合应按实际持仓计算超额，不能按目标持仓假设已经成交。

无法保证委托一定成交。对手盘不足、停牌、涨跌停排队、价格限制或连接故障均可能阻止成交；用对手方报价和有上限的价格容忍度只能提高成交机会。市价申报也受其申报类型、对手盘和保护限价约束。周末模拟提交/撤单测试只能证明接口链路，不能证明交易日撮合速度或整篮子执行能力。

## 验证

```powershell
python -m unittest discover -s tests -p 'test_pcf*.py' -v
python -m unittest discover -s research/pcf_follow -p test_pcf_backtest.py
```

新增测试覆盖收盘与休市、PCF修订阻断、模拟整篮子执行、重复运行、陈旧行情、下单超时不重试、部分成交停止、登录失效、默认关闭网络委托及成交明细关联。所有交易测试使用虚构账户/内存HTTP响应或本地paper账户；无真实委托。旧历史回放测试继续保留。
