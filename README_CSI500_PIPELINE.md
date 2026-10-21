# 中证500比赛完整代码链路

本分支保存北国银行小组截至 2026 年 10 月 21 日的本地源码：数据获取、特征加工、GRU 和 c5 信号、PCF 组合、目标审核、云端调度及执行器代码与补丁。以主仓库 `main` 的 A18 提交为基础，保留原项目结构，新增本地研究和日更目录。

这是源码交接版。历史行情、训练好的模型、账户快照、交易账本、登录凭据和通知机器人密钥不随代码公开上传。**从空目录克隆后，需要补齐数据和模型才可完整重跑，不能直接连接原比赛账户。**

## 比赛与当前进度

比赛为 2026 年第十五届“兴证全球杯”模拟证券投资大赛量化赛道，交易期为 2026-09-18 至 2026-12-04。第一阶段报告记载：只做多，中证500成分股市值占比不低于 80%，以中证500为基准。旧研究中的 70% 软闸门不等于比赛规则；导师要求以 ≥85% 硬约束复核历史结果，不能将旧参数视作已完成该复核。

截至本地记录，主线为易方达 563030 PCF 骨架，c5 低分剔除及基本面否决，叠加 GRU/c5 混合信号。10-13 起云端自动日更；10-19 切换十五模型、同行业回填及 beta 倾斜；骨架 v5 加入报告持仓 K1 回填与 √b 倾斜，配置中首个信号日为 10-20。10-20 对话记录确认了历史委托分页修复及高价股整手例外部署。

最新对齐目标训练与热启动微调代码在 `gru_aligned_20261019/`，本次上传不声称这批实验已经训练完成、通过上线门槛或改善了比赛收益。现有实验中 VWAP 标签、分钟统计、PATH4、SIZE_RANK 等未获准直接替换实盘的结论，见 `reports/` 及训练协议。

## 代码入口

| 环节 | 目录或入口 | 作用 |
|---|---|---|
| 历史数据获取 | `a股全数据/`、三个 iFinD 数据目录的 `scripts_backup/`、`scripts/tushare_sync.py` | 下载、分页、续传和来源审核；不含历史数据本体 |
| 分钟与日频日更 | `gru_live_extend_20261012/fetch/`、`实盘日更/tools/fetch_pcf.py`、`make_prices.py` | 新浪分钟、腾讯日线与行情、官方 PCF |
| 特征与冻结面板 | `gru_research/minute_data.py`、`slow_features.py`、`assemble.py` | 分钟聚合、因果标签、日频/PIT特征与训练样本 |
| GRU 训练 | `gru_research/`、`gru_market_attention_20261007/`、`gru_walkforward_20261010/` | 基线、市场上下文、滚动训练及集群任务 |
| 最新训练研究 | `gru_aligned_20261019/` | 对齐损失、SAM、热启动微调与预注册协议 |
| GRU 推理 | `gru_live_extend_20261012/run_chain.sh` | 日更、十五模型推理、中性化、平滑和混合信号 |
| c5 因子与日更 | `c5_core/code/QuantaAgents/` | 因子 DSL、396 因子、LightGBM、排雷与基本面 |
| c5 外部公式 | `c5_core/data/ext/`、`data/TushareData/` | Alpha101/191、因子日历、下载客户端；此处 data 中保留的是源码与规则 |
| 组合与约束 | `pcf_overlay_20261012/live_targets.py`、`skeleton_k1.py` | K1、√b、回填、整手取整、死区和发布闸门 |
| beta 与研究 | `timing_research_20261017/`、`barra_cne5_local/` | beta 倾斜、择时记录、风险模型与归因研究 |
| 日更总入口 | `实盘日更/daily.sh`、`cloud/daily_cloud.ps1` | 串联数据、信号、目标、审核、封存与次日预约 |
| 审核与运维 | `实盘日更/tools/` | 数据审核、日报、监控、重试、核账和通知 |
| 执行器底包 | `choice_cloud_prepare_20261011/full_executor/` | 本地 10-11 底包及完整依赖源码 |
| 执行器后续代码 | `choice_cloud_prepare_20261011/executor_sync_20261012/`、`实盘日更/cloud/executor_daily/` | 本地 10-12 同步副本，以及后续封存、公司行动、隔离、整手等补丁 |

## 执行器版本边界

**本地执行器底包与后续补丁是不同时间的材料，不能把它们拼成“已核验的当前云机镜像”。** 10-20 分页修复在对话记录中已确认，但本地 10-11/12 源码快照仍有从第 0 页开始的旧实现；本次没有从云机回拉最新文件，也没有修改云机。高价股一手例外的补丁、部署脚本和五个测试已包含在 `实盘日更/cloud/executor_daily/`。

执行器代码保留真实的核账、身份绑定、清单哈希和风险保护逻辑。公开副本中的账户编号、身份绑定摘要和运维地址已替换为示例值，原部署清单和交易证据未上传。重新部署必须另行配置身份及凭据并重新生成、审核清单。`实盘日更/config/console_config_target_file.json` 是历史配置草案，其中单笔限额不能当作当前生效参数；当前逻辑以执行器及补丁为准。

## 环境与数据准备

建议为 GRU、c5、Windows 执行器分别建环境；本次语法及测试检查使用 Python 3.12。原项目 `pyproject.toml` 负责基础包；`requirements-csi500-core.txt` 列出链路依赖；`docs/CSI500_LOCAL_ENV_OBSERVED.txt` 记录本次离线检查实际观察到的核心版本，供定位差异，未将其声称为跨平台安装锁文件。c5 原 Windows 环境版本另见 `c5_core/code/QuantaAgents/requirements-c5-freeze.txt`。

```bash
python -m pip install -e .
python -m pip install -r requirements-csi500-core.txt
python scripts/prepare_csi500_layout.py
python c5_core/install.py
```

`prepare_csi500_layout.py` 为历史代码需要的 `quantagent/src`、`scripts` 等路径建立本地兼容目录（可链接则链接，否则复制）。`c5_core/install.py` 会重定位 c5 的原机器绝对路径。集群路径、云机 `C:/ChoiceBridge` 布局、解释器位置和私有配置仍需按实际环境配置。

c5 日更的本地交接版是 `scripts/c5_daily.sh`；后续云端基本面增强版额外保存为 `scripts/c5_daily_cloud_20261013.sh`，对应 `fd_update_reseller.py`。增强版默认使用历史云机目录，使用前先调整部署路径。两版分开保存，避免覆盖后丢失来源信息。

主要外部资产及其预期位置：

- `gru_research/artifacts/dataset/` 的数组、冻结小时分区、历史成分与 PIT 基本面数据；仓库只附结构元数据。
- `gru_market_attention_20261007/context.npz` 与 `outputs/gru_beta_neutral_20261008/risk_context.npz`。
- `outputs/gru_walkforward_20261010/T2025/`、`T2026/` 的五种子 checkpoint 和分数；`gru_live_extend_20261012/models/oldrank/` 的队友五种子权重和 MODEL_CARD。
- `outputs/gru_live_extend_20261012/scores_frozen/` 的历史分数与 EMA 热身数据。
- c5 的日线源、成分与公司行动、基本面网格和 `models/m396_allA_turnlabel_through202605/`；396 成员表、冻结规则和外部公式源码已包含。
- `gru_aligned_20261019/aux/aligned_aux_20261019.npz` 及对应训练集、基线模型；可按 `build_aux.py` 和协议重新生成辅助数组。
- 执行器自身的账户配置、授权与部署清单，以及运行时生成的目标、回执、账本与锁。

`实盘日更/config/efund_holdings/` 是公开基金报告持仓输入，供 K1 回填使用，**不是本人的账户持仓**。日更缓存、账户目标与实际交易记录均排除。

## 本次上传与验证

本分支只上传源码、必要规则配置、公开基金回填输入与说明；没有执行交易、预约、计划任务注册或云机部署。原始本地工作目录及其现有 Git 分支未改动；打包工作在独立检出目录完成。

检查内容及结果见 [验证记录](docs/CSI500_UPLOAD_VALIDATION.md)。逐文件哈希与来源说明见 [上传清单](docs/CSI500_SOURCE_MANIFEST.json)。`gru_aligned_20261019/SHA256SUMS` 保留的是原训练上传包的清单，包含未公开的外部资产；本分支内容以新的上传清单为准。旧文档中的绝对路径、历史任务和收益数字属于各自时期的记录，不代表本次重新运行结果。第一阶段 Word 报告和私人对话原文仅用于理解背景，没有直接发布到公开仓库。
