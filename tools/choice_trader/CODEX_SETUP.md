# 给 Codex 的接手流程

用户希望你完成安装和配置。请实际运行下面的工具、修复可处理的问题并打开控制台，不要只把命令清单交回用户。所有命令从仓库根目录执行；所有私人配置和产物放进已忽略的 `tools/choice_trader/runtime/`。

## 先确定代码与运行环境

1. 检查 `git status` 和当前分支，保留已有修改及运行账本。当前功能在 PR #3 的 `feat/choice-trading-console` 分支；若 main 已有本指南，直接使用 main。否则从远端取得该分支；不要在有运行程序或未提交修改的目录强行切分支。首次克隆可用 `git clone --branch feat/choice-trading-console https://github.com/xiezhongyan2015-debug/quantagent.git`。
2. 必须在装有 Choice 的本地 Windows 上执行。若当前任务在云端或其他系统，继续做代码/依赖检查，并说明需要在该 Windows 电脑上的 Codex 本地任务接着运行；不要声称远端能访问本机登录会话。
3. 有 Python 时先运行 `python tools/choice_trader/onboarding.py inspect`。这是纯本地检查，不读 Cookie、不登录、不下单；JSON 中的 `needs_input`/退出码 2 表示需要继续配置，并不等于代码损坏。若没有 Python，由你查找已安装解释器或按官方安装流程处理，不要求用户手写命令。
4. 运行环境安装器：

```powershell
python tools\choice_trader\bootstrap.py
```

解释器不在 PATH 时使用其绝对路径调用本脚本，或传 `--python-executable "解释器绝对路径"` 指定创建环境的解释器。它复用或创建仓库 `.venv`、安装最小依赖、运行离线测试，然后打印检查结果。已安装环境可用 `--skip-install` 只做离线检查。入口采用 Python，不依赖 PowerShell 脚本执行策略；不要更改系统级执行策略、删除其他环境或重置已有账本来解决安装问题。

## 收集最少的信息

先复用本机已有绑定、设置、用户消息和团队提供的数据。只对确实缺少的信息向用户集中询问：

- 当前 Choice **比赛模拟账户**归属及 ID；不要索要密码、登录令牌或完整 Cookie。
- 要使用默认 PCF、队友目标文件还是其本地策略脚本。
- 尚未确定的目标仓位、总投入、单股/单笔限额，以及是否允许卖出。

路径探测、Python 依赖、JSON 编写、股票池转换、交易日历处理、测试和打开窗口由你完成。不要把默认 550 万等示例值当成新用户批准的交易限额；已有明确设置和授权时沿用，不重复确认。

## 账户绑定与已有账本

`inspect` 返回的 `choice_profile_candidates` 只来自标准安装位置。自定义位置可检查 Choice 快捷方式/安装目录，或使用用户明确给出的路径；多个登录配置无法确定归属时才询问用户。

让用户在 Choice 正常登录并进入金融实验室；验证码由本人处理。已绑定账户跳过初始化。只有尚无绑定、确定为用户自己的比赛模拟账户时，使用：

```powershell
.\.venv\Scripts\python.exe tools\choice_trader\setup_account.py --account "用户的模拟账户ID" --simulation-account --choice-profile "确认过的Choice缓存目录"
```

该命令只读初始化，目前仅支持无持仓、无历史成交、无当日委托、无冻结的账户。已有成交/持仓或旧绑定时，保留原目录和所有记录，优先查找该用户已有可用控制台。确需迁移的，先检查其完整原始账本并制定独立迁移方案；本仓库没有自动迁移器。不能复制其他人的账本、填零、修改身份摘要、忽略历史订单或清空 runtime 来让检查通过。

绑定文件与账本只能留在本地；报告仅显示账户尾号。不要将其原文、环境变量或日志全文发到 GitHub。

## 由你准备日历、股票池和配置

- 在用户指定的项目/数据目录中查找已有交易日历和股票池，验证覆盖日期及来源，优先复用可靠输入。
- 没有日历时，由你查询上交所/深交所对应年度的官方休市公告，生成有来源依据的完整交易日列表；不能只排除周末，也不能把 `calendar.example.json` 的示例当成持续有效日历。将来源 URL、获取时间、有效范围保存在 `runtime/data_sources.json`。
- PCF 策略需要符合团队规则的当前股票池，至少有 `symbol` 列。把可信来源的成分数据转换为 Parquet，保留来源和成分日期；不能用 README 的两只示例股票替代完整名单，不能自行扩大比赛股票池。无法取得有效数据时记录具体缺口，继续完成不依赖它的安装工作。
- 队友目标文件/本地脚本模式不需要 PCF 股票池，仍需有效日历。只运行用户指定或团队已审核的本地策略；示例脚本不能自动选为正式策略。未列出的已有持仓目标为零，需要卖出才能减仓。

把用户确定的设置写成 `runtime/onboarding-config.json`。这是配置补丁：未提供字段会保留已有配置；首次设置才继承代码默认值。相对路径相对于这份 JSON 所在目录，推荐写绝对路径。字段使用 `console_core.DEFAULTS` 中的名称，例如：

```json
{
  "strategy": "pcf_563030",
  "calendar_path": "calendar.json",
  "calendar_valid_through": "2026-12-31",
  "membership_path": "membership.parquet",
  "target_fraction": 0.5,
  "capital_limit": 5500000,
  "max_security_value": 500000,
  "max_order_value": 500000,
  "price_guard": 0.03,
  "spacing_seconds": 5,
  "allow_sells": false
}
```

上面只演示字段，日期、路径和限额须换成该用户已确定的值。然后由你执行：

```powershell
.\.venv\Scripts\python.exe tools\choice_trader\onboarding.py configure --from-json tools\choice_trader\runtime\onboarding-config.json
.\.venv\Scripts\python.exe tools\choice_trader\onboarding.py inspect
```

`configure` 会校验输入、备份原设置并保存配置，不访问账户或启动交易。运行中的账户不能修改配置；先使用已有控制台的停止流程，等待核验结束。周末可完成安装，交易程序仍只在有效交易日执行。检查未来某个执行日可传 `--date YYYY-MM-DD`，不要修改系统时间。

## 只读核验，然后打开控制台

已有运行进程时直接显示其状态，不并发核验、不启动第二个进程。否则在用户已授权连接自己的模拟账户后执行：

```powershell
.\.venv\Scripts\python.exe tools\choice_trader\onboarding.py verify-account
```

该命令采用只允许账户 GET 的传输层，需要时尝试一次正常 SSO，完成两轮核账，最多容忍 90 秒快照不同步。它不提交/撤销委托，不生成交易授权，不更新原始账本。`account_verified` 表示账户核对通过；不表示 PCF 已下载、策略计划已完成或可以绕过启动时检查。返回 `blocked` 时根据 `error_type` 和源码定位，不打印秘密字段；登录问题请用户正常登录，账本问题查原始记录，不能放宽误差。

环境、配置与账户通过后，打开 `tools/choice_trader/Start-Console.cmd` 或用本地 Python 启动 `console_app.py`。如果用户只要求安装/配置/打开窗口，不附加 `--start`，让用户点击当天的启动按钮。用户明确要求开始当天模拟交易且策略和限额已确定时，按当前会话授权和工具权限继续处理，不重复询问同一授权。部署到队友的电脑不是替本机用户开始交易。

不要把“窗口已打开”“configured”“account_verified”和“今日交易正在运行”混为一谈。启动后的 PCF/目标检查由执行器完成；通用卖出仍默认关闭，其真实比赛模拟账户成交联调尚未完成。

## 交付给队友

完成后简短说明：安装位置、配置的策略与限额、账户核验结果、控制台是否已打开、是否确实开始交易、以后双击哪个文件。只留下真正需要本人处理的登录/账户/数据问题；不要再输出长篇人工安装步骤。

开发验证：`python -m unittest discover -s tools/choice_trader -p "test_*.py" -q`。新增检查应保持离线测试，不为验证安装而下测试单。

项目入口使用 Codex 的 [AGENTS.md 指引机制](https://learn.chatgpt.com/docs/agent-configuration/agents-md)。根目录负责引导阅读本流程，不需要队友安装额外 Codex 插件。
