# 目录说明（2026-10-12 整理）

**每天操作只看 `实盘日更/`**，入口 `实盘日更/README_每日操作.md`。其余目录是研究、数据、队友材料，不要在里面放每日文件。

| 目录 | 性质 | 说明 |
|---|---|---|
| `实盘日更/` | **实盘** | 每日命令、输入、目标文件、日志、参考文档软链接 |
| `gru_live_extend_20261012/` | 实盘代码 | GRU 公开数据日更链（新浪分钟 → 小时 → 日频 → 推理 → 信号）；结果 `outputs/gru_live_extend_20261012/` |
| `pcf_overlay_20261012/` | 实盘代码 + 研究 | `live_targets.py` 目标生成与闸门；其余为 PCF 骨架回放与优化器研究（v1–v4） |
| `gru_research/` | 冻结 | 小时 GRU 数据管线与冻结数据集 `artifacts/dataset/`（到 2026-09-23），勿改 |
| `gru_market_attention_20261007/` | 冻结 | control / market_attention 模型代码与 context |
| `gru_walkforward_20261010/` | 冻结 | 滚动重训；`outputs/gru_walkforward_20261010/T2026/control_seed*/best.pt` 是实盘在用的五个模型 |
| `gru_beta_neutral_20261008/` | 研究 | 中性化、指数增强回放、风险暴露 `risk_context.npz` |
| `gru_dualplatform_20261011/`、`return_head_20261009/` | 研究 | 双平台算力线；队友的收益头实验 |
| `quantagent/` | 队友代码 | GitHub fork（已合并到 A18），c5 因子公式与日更脚本；模型文件和数据不在仓库 |
| `给队友_c5分数_20261011/`、`给队友_c5状态_20260929_20261009/` | 队友数据 | c5 历史分数、每日状态（实盘用的拷到 `实盘日更/inputs/c5/`） |
| `choice_cloud_prepare_20261011/` | 云机 | 连接、只读快照、试单准备记录 |
| `outputs/` | 结果 | 各研究与实盘链的输出；`outputs/cluster_sync_20261011/` 是队友上传的同步副本 |
| `中证500_历年成分股_*`、`中证1000_*` | 数据 | iFinD 日频后复权、PIT 基本面（到 2026-09-24 / 09-04） |
| `A股分钟k线数据.tar.gz.*` | 数据 | 原始分钟档（冻结数据集的来源，4.4 GB） |
| `2000-2025*`、`2026年*` | 已归档 | 只剩 iCloud 归档指针（见 `数据去向索引.txt`） |
| `research_v3/`、`acquired_data/`、`a股全数据/`、`训练回测证据与复核/`、`中证500研究计划_V3_*`、`research_notes/`、`reports/`、`doc/`、`tmp/`、`下载归档_2026-10-09/` | 旧研究 | 历史轮次，保留不动 |
| `归档_杂项/` | 杂项 | 书籍 PDF、旧 zip 包（10-12 从根目录移入） |
| `.pcf-research-venv/` | 环境 | 实盘用的 Python（torch CPU、cvxpy、lightgbm） |
