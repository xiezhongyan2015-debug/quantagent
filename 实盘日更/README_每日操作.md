# 实盘日更：每天怎么操作（v1，2026-10-12）

这个文件夹是实盘唯一的操作入口。研究代码不动，所有每日输入、输出、日志都在这里。T 指"今天这个交易日"（信号日），文件给 T+1 执行。

## 一、目录

```
实盘日更/
  daily.sh                 每日一条命令（见第三节）
  tools/                   fetch_pcf.py 下载官方 PCF | make_prices.py 估值日收盘 | make_account.py 云机快照转格式 | log_day.py 写日志
  config/                  console_config_target_file.json（控制台正式配置草案 0.95/1200万）| calendar_2026.json（交易日历）| index_membership_20260917.parquet（成分并集）
  inputs/pcf/              563030_<T8>.xml（+ .sha256 回执）
  inputs/prices/           close_<估值日8>.csv（+ 回执）
  inputs/c5/               c5_state_<T8>.parquet   ← 队友日更链每天 16:00–16:30 产出，列 date,code,state,raw_score(,score_pct,...)
  inputs/account/          snapshot_<T>.json       ← 云机收盘后只读快照，用 tools/make_account.py 转成 {as_of,nav,positions,prices_now}
  outputs/targets/<T+1>/   target_<T+1>.json（控制台 target_file 格式，shares）| .csv | precheck_<T+1>.json | 或 rejected_<T+1>.json
  outputs/signals/         z_<T>_slot15.csv（GRU 中性化 z）| blend_<T>.csv（与 c5 混合，L2 用）
  outputs/logs/            daily_<T>.log（当天全部命令输出）
  outputs/daily_log.csv    每天一行：模式、是否发布、名字数、仓位、换手、剔除数、SHA256、备注   ← 比赛结束就靠它复盘
  参考/                    手册 v1.1、处理报告、GRU 链报告、给队友清单、云机联调结果（软链接）
```

GRU 链代码在 `../gru_live_extend_20261012/`（`run_chain.sh` 抓新浪分钟→小时→日频→推理→信号），闸门和目标生成在 `../pcf_overlay_20261012/live_targets.py`。daily.sh 只是把它们串起来。

## 二、每天的时间线（北京时间）

| 时点 | 做什么 | 谁 | 产物 / 失败处理 |
|---|---|---|---|
| 09:00 前 | 官方 PCF 自动可下：`tools/fetch_pcf.py T`（daily.sh 会自己做） | 本机 | `inputs/pcf/563030_T.xml`；没有 → 当天不换目标，沿用前一日 |
| 09:15–09:35 | 队友控制台载入昨天生成的 `target_T.json`，先卖后买；看回执 | 队友 / 云机 | 拒单连续 5 笔停止；任何异常当天不再发单 |
| 15:10 | **`实盘日更/daily.sh T`** 起跑：PCF → 估值日收盘 → GRU 链（约 40 分钟，新浪分钟抓取是大头） | 本机（Mac 不能睡） | 日志 `outputs/logs/daily_T.log` |
| 15:30–16:30 | 云机导出收盘快照 → `tools/make_account.py <快照> T`；队友 c5 文件放到 `inputs/c5/c5_state_T8.parquet` | 云机值班 / 队友 | 两样都到了 daily.sh 才能出正式文件；c5 没到 → 自动退回 P0（`--fallback`），账户快照没到 → 只能影子（`--shadow`） |
| 16:40 前 | daily.sh 结束：读 `✅ 目标文件` 一行；核对 precheck 里 names / stock_weight / turnover / csi500_share / removed_names；上传 `target_T+1.json` 到云机 staging，云机校验 SHA256 后放到 live 目录 | 本机 | `outputs/daily_log.csv` 自动追加一行 |
| 17:00 | 日报一行发群：模式、名字数、仓位、换手、剔除数、SHA256 前 8 位（从 daily_log.csv 直接抄） | 本机 | — |

如果 15:10 起跑时 c5 还没到，正确做法是等到 16:30 再跑（或先跑一次 `--mode V5_tilt`，c5 到了再跑一次，后一次覆盖），不要用昨天的 c5。

## 三、命令

```bash
# 标准（阶段 C，排雷 + GRU/c5 混合倾斜，k=0.5）：
实盘日更/daily.sh 2026-10-12

# 阶段 B（只排雷，不跑 GRU 链，10 分钟内出结果）：
实盘日更/daily.sh 2026-10-12 --mode V5

# 阶段 A（纯复制）：
实盘日更/daily.sh 2026-10-12 --mode P0

# 换层当天（P0→V5 或 V5→V5_tilt 第一天，换手会到 50% 左右）加：
实盘日更/daily.sh 2026-10-12 --switch-day

# 账户快照还没到、先看影子：
实盘日更/daily.sh 2026-10-12 --shadow

# GRU 链已经单独跑过（信号已在 outputs/gru_live_extend_20261012/signals/）：
实盘日更/daily.sh 2026-10-12 --skip-gru

# 云机快照转格式：
.pcf-research-venv/bin/python 实盘日更/tools/make_account.py /path/captain_snapshot.json 2026-10-12
```

默认参数：整手 `shares` 格式，死区 1 手（目标与持仓差不到一手不动），换手闸门 40%，中证 500 占比软闸门 70%，单票 ≤ 5% 且 ≤ 50 万，名字 ≥ 80，执行日必须是信号日的下一个交易日，估值日收盘价日期必须等于 PCF 的 PreTradingDay。任一闸门不过 → 没有目标文件，只有 `rejected_<T+1>.json`。

## 四、读结果

precheck 里最该看的六个数：`released`、`names`（约 105–140）、`stock_weight`（0.949）、`post_lot_stock_fraction`（≥ 0.90）、`planned_two_way_turnover`（平日 3%–10%，换层日 ~50%）、`removed_names`（c5 剔除，约 25–35）、`csi500_share_of_target`（约 0.88–0.92）、`signal_coverage`（≥ 0.8）。`fallback_from` 非空说明当天自动降了层，日报里要写。

## 五、回滚与异常（照手册 v1.1 第三部分）

- 任一数据源当天缺失：daily.sh 自动降层（V5_tilt → V5 → P0）；PCF 缺失则不出文件，控制台沿用前一日目标。
- 连续 3 个交易日 V5 对 P0 的影子差 < −1.5%，或单日异常回执：改回 `--mode P0`，日志备注写原因。
- 新浪分钟只保留约 8 个交易日：GRU 链一天都不能停；停超过一周要重新补分钟数据。
- 不要手改目标文件；要改参数改 daily.sh 或 config，并在 daily_log.csv 备注。

## 六、历史记录

- 2026-10-12（执行日）：V5 文件 08:50 交付（SHA 48754121…），c5 排雷 30 只、换手 46%；同日 09:35 生成 V5_tilt 记录版（`V5_tilt_record/`）供对比，未执行；10:11 的 `test_dailysh_V5_tilt_deadband1/` 是 daily.sh 的测试产物，未执行。

## 七、云机（2026-10-12 起）

| 项目 | 位置 |
|---|---|
| 免密 SSH | `ssh -F choice_cloud_prepare_20261011/ssh_config choice-executor-key`（密钥 `~/.ssh/choice_executor_20261012`） |
| 部署包 | `C:\ChoiceBridge\quant\current` → 精简包（冻结数据集最后 130 天 + 模型 + 代码），`cloud/bootstrap_quant.ps1` 解包 |
| 环境 | `C:\ChoiceBridge\runtime\gru-venv`（torch CPU，版本与 Mac 钉齐）、`c5-venv`（队友版本：lightgbm 4.6.0、pandas 2.3.3）、Git Bash |
| c5 | `C:\ChoiceBridge\c5_stage_20261012\payload\c5_core`，`install.py` 重定位后 `bash scripts/c5_daily.sh`（`PY=c5-venv`） |
| 每日任务 | `cloud/daily_cloud.ps1`（15:10，Task Scheduler `QuantDaily_1510`，`cloud/register_tasks.ps1` 注册）；日志 `实盘日更/outputs/logs/cloud_<T>.log` |
| 发布 | 通过闸门的目标文件复制到 `C:\ChoiceBridge\live\target_<T+1>.json`，控制台 `target_file` 从这里读 |
| 大文件传输 | **不要走 SSH**（跨境只有 20 KB/s）。走 OSS 桶 `csi500-transfer-20261012`，云机用内网端点 `oss-cn-shanghai-internal.aliyuncs.com` + `curl.exe` 下载（90 MB/s） |

注意：腾讯日线接口当天那根日 K 要到 16:00 后才有，链里对此已做"沿用前一日复权系数"处理，但 c5 的日线增量步骤依赖它，所以云机定时任务最好 16:30 起跑，先跑 c5 再跑 GRU，17:30 前出文件。

## 八、全自动模式（2026-10-13 起，云机）

人每天要做的事：**零**。每个交易日的流程全部由云机计划任务 `QuantDaily_1605`（Administrator 交互登录身份）完成：

| 时点 | 自动做什么 | 产物 |
|---|---|---|
| 09:30 | 前一晚预约的执行器进程自动开始：先 1 手试单（含费用 ≤ 1 万），试单成交并两轮核账通过后进入正式调仓（先卖后买，单笔 ≤ 5 万拆单，±3% 价格保护） | `executor\...\console\runs\<日期>\` 账本与回执 |
| 16:05 | 流水线：只读账户快照 → 当日 PCF 与估值价 → c5 状态（我们自己跑队友的链）→ GRU 链 → 闸门出 T+1 目标 → 封存发布（科创板整手适配、成分证据、配置、清单）→ **自动预约明早 09:30** → 日报 | `C:\ChoiceBridge\live\`：`release_receipt_<T+1>.json`、`reports\report_<T>.md`、`logs\pipeline_<T>.log` |
| 16:30 前后 | 日报推送到群（钉钉/企业微信机器人，`C:\ChoiceBridge\live\notify.json` 里填 webhook） | 一条消息：净资产、当日净值与超额、成交笔数与金额、费用、明日计划 |

失败处理：流水线任一步失败就停在"预约"之前，明天执行器没有计划，**不会下单**，日报会写"没有发布目标"。人工介入只在这种情况下需要。

前提只有一条：云机的 Administrator 保持登录（远程桌面可以断开，不要注销；重启后需有人登录一次）。

代码：执行器补丁 `cloud/executor_daily/patch_executor_dates.py`（把一日专用的日期常量改为读 `live/active_day.json`，回归已通过）、封存发布 `release_day.py`、预约 `arm.py`、流水线 `cloud/daily_cloud.ps1`、日报 `tools/daily_report.py`。

### 八.1 监控、审核与恢复（2026-10-12 晚补）

| 任务 | 时点 | 做什么 |
|---|---|---|
| `QuantMonitor` | 交易日 09:20–15:20 每 10 分钟 | 读执行器状态与账本：状态变化、异常（stopped_review_required / login_required / 进程退出 / 09:25 仍未启动）立即推飞书；15:05 推收盘小结 |
| `QuantDaily_1605`（实际 16:30 触发） | 交易日 16:30 | 流水线；**封存前先跑 `tools/data_audit.py`**：PCF 日期与成分数、估值价齐全、c5 当天行数/状态分布/原始分、分钟库当天完整度、腾讯日线与行情、GRU 信号只数/尺度/与前一日秩相关、目标闸门。任一硬项不过 → 不发布、不预约，日报写明 |
| `QuantBootAlert` | 系统启动（SYSTEM） | 推送"云机已重启，需登录" |
| `QuantLogonRearm` | Administrator 登录时 | 若有已发布的下一交易日目标且无运行中的执行器，自动清陈旧锁并重新预约，推送结果 |

已修：周五可预约周一（按日历取下一交易日）；GRU 分钟数据持久累积（`gru_research/trading_data/minute_store/`，新浪 8 日窗口滚动不再丢日）；GRU 链 `set -o pipefail` 并在末尾校验信号；日报推送三次重试并校验飞书返回码。

未修（已知）：基本面增量需 `FD_TUSHARE_TOKEN`（否决名单约 10-16 起逐步失效）；执行器一笔委托回执持续不明会停止当日全部交易（`isolate_unknown_orders` 被试单规则锁为 false），靠监控告警 + 人工；试单未成交则当日不调仓。

### 八.2 运维加固（2026-10-13 晚）

| 任务 | 身份 / 时点 | 做什么 |
|---|---|---|
| `QuantMonitor` | 交易日 09:20–15:20 **每 2 分钟**（原 10 分钟） | 立即告警：停止需审核、需登录、进程消失、09:25 无运行记录、核账/恢复卡住 ≥6 分钟。每天各一次：开始下单、目标全部完成、11:30 午间小结、15:05 收盘小结（含未完成名单与原因）。午休、短暂核账不再推送；执行器自己已报的停机不重复推。 |
| `QuantDailyRetry` | 交易日 17:50、19:20，`daily_cloud.ps1 -IfNotDone` | 主流水线仍在跑 → 跳过；明日目标已发布且已预约 → 跳过；已发布未预约 → 只重新预约；未发布 → 整条流水线重跑一次。失败告警按原因去重。 |
| `QuantDeadman` | **SYSTEM**，全天每 10 分钟 | 不依赖 Administrator 登录：Administrator 掉线、每分钟告警任务停摆、盘中监控停摆、16:50 流水线没启动、20:30 明日目标未发布或未预约、C 盘 < 3 GB → 直接推飞书（工作时间每小时最多重复一次）。 |

日报新增：调仓完成度（计划/到位/未完成名单及原因）、执行器当日异常停止次数与原因、费用占成交额 bp、数据审核提醒单独一行；推送失败进持久发件箱由每分钟任务重发。数据审核新增 c5 基本面新鲜度倒计时（基本面截至 09-17，前向填充 15 个交易日，**10-19 起否决名单清空且不报错**，需 Tushare token）。

代码：`tools/monitor.py`、`tools/deadman.py`、`tools/execution_status.py`（新）、`tools/notify.py`（`send_or_queue`）、`tools/daily_report.py`、`tools/data_audit.py`、`cloud/daily_cloud.ps1`（`-IfNotDone`）、`cloud/on_logon_rearm.ps1`、`cloud/register_ops_tasks.ps1`。核验记录 `outputs/ops_hardening_20261013/`。云机备份 `C:\ChoiceBridge\live\backups\ops_hardening_20261013_190745`。执行器目录、部署清单、风控配置、今晚预约进程均未改动。

消息分流（2026-10-13 19:37）：`notify.py` 按内容分两路。日报（`【北国银行 日报`、`【日报更正】` 开头）发团队群 `feishu_webhook`；其余全部（报警、盘中小结、看门狗、部署通知）发私人告警机器人 `alert_feishu_webhook`（可选 `alert_feishu_secret`），写在云机 `C:\ChoiceBridge\live\notify.json`。没配私人机器人，或它连续发送失败时，退回发到群里，消息不丢。

### 八.3 c5 基本面更新（2026-10-13 晚）

- 数据源：Tushare 兼容的第三方转售接口 `tl.kaixin8.top`。凭证只存在云机 `C:\ChoiceBridge\live\secrets\tushare.json`，只有 Administrators 和 SYSTEM 能读，不写进日志。
- 每日链路 `c5_daily.sh` 的改动：
  - 建好实时面板后，运行 `scripts/fd_update_reseller.py --since-last`，从上次成功的日期接着抓。时间上限 20 分钟；不抓日线和复权因子，基本面特征用不到这两张。
  - 然后在实时面板的日期上重建 `A_Layer_Live/fundamental_grid`，再用它组装基本面字段。
  - 任何一步失败，都退回原来的研究网格，链路继续跑。
- 10-13 已补齐 9 月 19 日到 10 月 13 日的数据，原始数据备份在 `C:\ChoiceBridge\live\backups\fd_raw_20261013`。10-13 重算的否决名单是 48 只，和原来的 48 只重合 45 只。
- 数据审核会显示：网格截止日期；连续 3 个交易日以上没有成功增量时给出提醒。回购和业绩预告两张表常因接口繁忙失败，不影响否决名单。
- 代码与核验记录：`outputs/fd_reseller_20261013/`。

### 八.4 小单门槛（2026-10-13 晚上线，10-15 执行日起生效）

- **规则：** `daily.sh` 调用 `live_targets.py` 时加了 `--deadband-cny 10000`。已持仓股票，如果目标与持仓的差额（按当前价）不到 1 万元，或不超过 1 手，就保留原股数不调。
- **始终执行：** 清仓（目标为 0）和新开仓不受门槛限制。
- **保护：** 保留之后，股票仓位和中证 500 仓位与完整目标的偏差都不能超过 0.5 个百分点；同时仓位不得超过 94.6%，股票不得低于 90.5%，中证 500 不得低于 80.5%。超出时从差额最大的股票开始照常调仓。
- **记录：** precheck 里会写保留了几只、为满足约束放开了哪几只。
- **验证：**
  - 历史回放（2024-06 至 2026-09）：每年约 +60 bp，t = 4.0，前后两个半段都为正。
  - 真实输入回放：10-13 和 10-14 两天，要交易的股票分别从 54 只降到 19 只、从 51 只降到 24 只，闸门全部通过；旧规则重跑出的 10-14 目标与实际发布的逐行一致。
- **记录与备份：** 研究报告在 `outputs/trade_threshold_study_20261013/`，上线核验在 `outputs/deadband_10k_20261013/`，云机备份在 `C:\ChoiceBridge\live\backups\deadband_10k_20261013_224641`。
- **回滚：** 把 `daily.sh` 里的 `--deadband-cny 10000` 删掉即可。

## 2026-10-19 起的实盘口径（10-18 用户批准）
- **信号与回填：** 十五模型集成混合信号（`--ens all15`），c5 剔除的权重按申万一级同行业回填（`--refill industry`）。
- **beta 倾斜：** 按 S7 持有方向做（`--beta-tilt`）。规则见 `reports/P3_beta倾斜影子登记协议v2_2026-10-17.md` 补充 2、3。
- **云机命令：** `daily.sh T --mode V5_tilt --skip-gru --ens all15 --refill industry --beta-tilt C:/ChoiceBridge/live/timing/beta_shadow --beta-cache C:/ChoiceBridge/live/timing/cache`。
- **已经 arm 之后要重新发布：** 先在旧 run 目录放 STOP（09:30 前停止，不会查账户或下单），等进程退出，跑 clear_stale_lock，再 arm。arm 的升级通道在这种情况下会报"正式目标或成分文件已变化"。
