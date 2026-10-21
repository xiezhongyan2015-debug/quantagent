# GRU 滚动前推（walk-forward）与 2019 小时修复面板重训，2026-10-10

本目录只准备脚本，不改动冻结包 `gru_market_attention_20261007/`，也没有在本地运行任何训练。两组实验都只跑 `control`（四头 GRU）变体，五个种子 11/22/33/44/55，模型、损失、Gaussian-rank 标签、两交易日隔离、标签成熟约束、按验证集 14:00 y4 RankIC 选 checkpoint、40 轮/耐心 8 等全部沿用原代码路径。

## 文件

| 文件 | 作用 |
| --- | --- |
| `experiment_wf.py` | 对 `gru_market_attention_20261007.experiment` 的薄封装：`import` 原模块后 monkey-patch（与 `gru_beta_neutral_20261008/residual_train.py` 同一手法），**没有复制 600 行**。只改四处：(1) 训练/验证/测试边界来自环境变量 `WF_TRAIN_START WF_TRAIN_END WF_VAL_START WF_VAL_END WF_TEST_START WF_TEST_END`（ISO 日期，右开区间）；(2) 拟合期断言改为“训练/验证行时间 < `WF_TEST_START`”（原来写死 2025-01-01）；(3) 输出根目录来自 `GRU_RUN_ROOT`（默认 `outputs/gru_walkforward_20261010`），避免写进 `outputs/gru_market_attention_20261007/`；(4) `export` 只导出一个 `test` 切片：`test_scores.npz`（键 `scores/indices/times`，布局同原 `test_2025_scores.npz`）、`test_metrics.json`、`test_sections.csv`、`export.json`，另写 `wf.json` 记录窗口。唯一重述的原代码是 `Data.split` 的六行（原函数把边界表写在函数体内，无法注入）。窗口、面板目录、面板 `hour_complete.npy` 的 sha256 都进入 run signature，因此续训/导出时不会把修复面板或别的窗口误当原始 run。 |
| `walkforward.sbatch` | SLURM array 作业，30 个任务 = 6 个测试年 × 5 种子，分区/账号/QoS/GPU/时限头部与 `train.sbatch` 相同。 |
| `repair2019.sbatch` | SLURM array 作业，5 个种子，原始固定窗口，但 `GRU_DATA` 指向修复面板。 |
| `make_repair_panel.sh` | 生成 `outputs/gru_repair2019_panel/`：冻结数据集的每个文件做符号链接（不复制 `X.npy`），只有 `hour_complete.npy` 换成修复掩码（复制一份并校验 sha256、形状、dtype、只允许 False→True）。幂等。 |

## 窗口方案（walk-forward）

array 下标 `id = 5*k + j`，`k=0..5` 对应测试年 `T = 2021+k`，`j=0..4` 对应种子 `(11 22 33 44 55)`。验证集 = 年 `T-1`，训练 = `2019-01-01 .. (T-1)-01-01`（验证年之前的全部年份）。

| id | T | 训练 | 验证 | 测试 | 本地核验的截面数（训练 / 验证 / 验证14:00 / 测试） |
| --- | --- | --- | --- | --- | --- |
| 0–4 | 2021 | 2019-01-01..2020-01-01 | 2020 | 2021-01-01..2022-01-01 | 963 / 959 / 240 / 972 |
| 5–9 | 2022 | 2019-01-01..2021-01-01 | 2021 | 2022-01-01..2023-01-01 | — |
| 10–14 | 2023 | 2019-01-01..2022-01-01 | 2022 | 2023-01-01..2024-01-01 | — |
| 15–19 | 2024 | 2019-01-01..2023-01-01 | 2023 | 2024-01-01..2025-01-01 | — |
| 20–24 | 2025 | 2019-01-01..2024-01-01 | 2024 | 2025-01-01..2026-01-01 | （与原冻结协议同训练/验证；原 4843 / 955 / 239） |
| 25–29 | 2026 | 2019-01-01..2025-01-01 | 2025 | 2026-01-01..2026-09-24 | 5811 / 959 / 240 / 708 |

输出目录：`outputs/gru_walkforward_20261010/T{T}/control_seed{S}/`。

**T=2021 只有 2019 一年训练数据**（963 个截面，约为原协议的 1/5，每轮约 120 个优化步）。默认仍从 2019 开始，以与冻结协议一致且避开 2018 年未经修复审查的小时完整性；若认为太短，可令所有窗口从 2018-01-01 起训练（面板起点 2018-01-02，80 根序列回看意味着首个可用训练截面在 2018-01-29；本地核验 T=2021 训练截面变为 1856）：

```sh
sbatch --export=ALL,WF_TRAIN_START_ALL=2018-01-01 gru_walkforward_20261010/walkforward.sbatch
```

## 修复面板实验（repair2019）

与 `outputs/gru_market_attention_20261007/control_seed{S}` 的唯一差别是 `hour_complete.npy`：`outputs/gru_beta_neutral_20261008/hour_complete_with_combined_2019_repairs.npy`（形状 (8476, 1178)、bool，与原掩码相同；本地核验只把 2019 年的 1621 个股票-小时从 False 翻成 True，没有 True→False）。窗口写死为原协议：训练 2019-01-01..2024-01-01，验证 2024，测试 2025-01-01..2026-09-24（2025 与 2026 合并在一个 `test_scores.npz` 中，可按 `test_sections.csv` 的 `date` 拆开）。输出目录 `outputs/gru_repair2019_20261010/control_seed{S}/`。

`meta.json` 仍是原文件的符号链接：它的 `files_sha256["hour_complete.npy"]` 列的是原掩码。`PanelDataset` 不校验各文件哈希，数据集指纹只对 `meta.json` 做哈希，所以 `context_manifest.json` 的指纹断言照常通过（本地已用桩替代 torch 实际加载修复面板核验）。修复掩码的 sha256（`adffee0970348ce91e9ac059a3a74f6124a411d751c2d0a300c920bc5ada5425`）写进 `repair_panel_manifest.json`、`wf.json` 和 run signature。

## 前置条件

1. 把本目录 `gru_walkforward_20261010/` 整个上传到集群项目根 `/home/users/s1155284034/gru_hourly_20261001/`，与 `gru_market_attention_20261007/`（必须含 `context.npz`、`context_manifest.json`、`train.py`、`model.py`）并列。
2. 把修复掩码上传到集群 `outputs/gru_beta_neutral_20261008/hour_complete_with_combined_2019_repairs.npy`（约 10 MB；脚本会校验 sha256，路径不同可作为第二个参数传入）。
3. 冻结数据集在集群上的位置沿用原包默认 `/data/users/s1155284034/gru_hourly_20261001/dataset`（与本地 `gru_research/artifacts/dataset` 的 `meta.json` 指纹一致：`6c2426b3…`）。

## 提交命令（在集群项目根执行）

```sh
cd /home/users/s1155284034/gru_hourly_20261001

# 可选：不占 GPU 地检查某个窗口的解析结果
WF_TRAIN_START=2019-01-01 WF_TRAIN_END=2020-01-01 WF_VAL_START=2020-01-01 WF_VAL_END=2021-01-01 \
WF_TEST_START=2021-01-01 WF_TEST_END=2022-01-01 \
/opt/network/anaconda3/envs/pytorch/bin/python -m gru_walkforward_20261010.experiment_wf describe

# 1) 滚动前推：30 个任务
sbatch gru_walkforward_20261010/walkforward.sbatch
#    只跑 T=2021：sbatch --array=0-4 gru_walkforward_20261010/walkforward.sbatch
#    限制并发 5： sbatch --array=0-29%5 gru_walkforward_20261010/walkforward.sbatch

# 2) 修复面板：先建面板，再提交 5 个种子
bash gru_walkforward_20261010/make_repair_panel.sh
sbatch gru_walkforward_20261010/repair2019.sbatch
```

日志：`outputs/gru-wf-<jobid>_<taskid>.log`、`outputs/gru-repair2019-<jobid>_<taskid>.log`。每个任务依次执行 `describe`、`train`、`export`。训练每轮保存 `last.pt`，同一下标重新提交会从 `last.pt` 续训（signature 含窗口和掩码 sha，不会串）；已有 `result.json` 的任务 `train` 直接跳过只做导出。

## 预计运行时间

原包五个 control 种子在 RTX 3090 上每轮 129–139 秒，分别跑了 9/11/12/15/17 轮（早停），训练 20–37 分钟，加上导出按每个种子约 40–50 分钟预留。训练截面越少每轮越快：T=2021 每轮约为原来的 1/5，T=2026 约 1.2 倍，但早停轮数不可预知；`--time=06:00:00` 与原脚本一致，足够。35 个任务合计上限约 25–30 GPU 小时，实际墙钟取决于 `c16g1` QoS 允许同时占用的 3090 数量。

## 每个 run 目录的产物

`config.json`（signature）、`wf.json`（窗口、面板目录、掩码 sha、导出后补充各切片截面数与测试指标）、`epochs.jsonl`、`best.pt`、`last.pt`、`best_validation14.json`、`validation.json`、`validation_sections.csv`、`result.json`（`best_validation14_rank_ic` 等）、`test_scores.npz`、`test_metrics.json`、`test_sections.csv`、`export.json`。

`test_scores.npz`：`scores` 形状 `[n_sections, 1178, 4]`（y4 为第 3 列，不可见股票为 NaN）、`indices`（`times.npy` 的行号）、`times`（int64 UTC ns）。

## 结果如何使用

**滚动前推**：六个窗口的 `test_scores.npz` 首尾相接即 2021-01 至 2026-09 共约六年的真正样本外分数，每年的模型只见过该年之前的数据。14:00 截面取 `test_sections.csv` 中 `slot == 2` 的行（或 `times` 转 Asia/Shanghai 后小时为 14）。示例：

```python
import numpy as np, pandas as pd
rows = []
for T in range(2021, 2027):
    per_seed = []
    for S in (11, 22, 33, 44, 55):
        q = np.load(f'outputs/gru_walkforward_20261010/T{T}/control_seed{S}/test_scores.npz')
        per_seed.append(q['scores'][:, :, 3]); times = q['times']
    y4 = np.nanmean(np.stack(per_seed), axis=0)            # 五种子平均 y4 分数
    t = pd.to_datetime(times, utc=True).tz_convert('Asia/Shanghai')
    rows.append(pd.DataFrame(y4[t.hour == 14], index=t[t.hour == 14]))
scores_14 = pd.concat(rows)                                 # 行 = 14:00 截面，列 = 1178 只股票（symbols.json 顺序）
```

把它接到既有 14:00 信号回放（`replay14_fee2_allin` 的口径）即可得到六年的逐年样本外 RankIC 与组合表现；`test_metrics.json` 已给出每窗口整体/分槽/分季度的 IC 与 RankIC，`wf.json` 的 `test_slot14` 是 14:00 的汇总。

**修复面板**：配对比较 2024 年验证 RankIC。修复 run 的 `outputs/gru_repair2019_20261010/control_seed{S}/result.json['best_validation14_rank_ic']`（以及 `validation.json['slot']['2']['rank_ic']`、`['overall']`）对比同种子的原 control run。原 run 在集群上位于 `outputs/gru_market_attention_20261007/control_seed{S}/result.json`；本地副本在 `outputs/gru_market_attention_report_20261007/evidence/control_seed{S}/result.json`（注意 `outputs/gru_beta_neutral_20261008/source_snapshot/outputs/gru_market_attention_20261007/control_seed*/` 本地只有两个 `*_scores.npz`，没有 `result.json`）。原五个种子的 `best_validation14_rank_ic`：11→0.0683、22→0.0675、33→0.0703、44→0.0647、55→0.0652。同种子同初始化、同验证截面，差异只来自 2019 年训练截面内可见股票的变化；五对差值做均值±标准差与符号检验即可，样本只有 5 对，结论应保守。`test_metrics.json`（2025–2026）可作次要对照。

## 假设与未在本地核验的事项

- 本地无 torch/GPU，`train`/`export` 两条路径没有运行过。本地做过的核验：`py_compile`；`describe`；用桩模块替代 torch 后真实加载本地面板和修复面板，确认封装后的 `split` 在原窗口下与冻结 `split` 逐元素相同（train/validation 以及 test_2025+recent_2026 拼接 = test），拟合期守卫对测试行抛错、对训练行放行，`CONFIG`/`OUT`/signature 替换生效；`make_repair_panel.sh` 在本地数据集上实际构建（27 个符号链接 + 修复掩码）、幂等、错误掩码被拒绝；两个 sbatch 的 bash 语法与 30 个下标的窗口/种子映射。
- 对 `experiment.py` 内部的依赖：`Data.split` 的键只用到 `train`/`validation`（`train()`）；`batch()` 里只有 `self.fit` 一处使用（封装把它置 False 并用自己的断言替代）；`signature()`、`corr()` 在调用时读取模块全局 `CONFIG`，`train()`/`export()` 在调用时读取模块全局 `OUT`、`Data`，因此重新赋值即生效（与 `residual_train.py` 的用法相同）；`export()` 的切片名写死，所以封装自带一个六行的 `export`，内部仍调用原 `evaluate`/`signature`/`checkpoint_load`。
- `context.npz` 的行业字典是用 2024-01-01 之前的周度快照建的（只是编码集合，不是拟合参数），市场状态特征是固定单位无全史归一化；对 T≤2024 的窗口，字典可能包含测试年之后才出现的行业码，这是非参数的极小前视，本轮未重建 context。
- 六个测试年的数据在此前实验中都已被看过，`export.json` 的 `historical_tests_already_exposed` 保持为 true；滚动前推是协议层面的样本外检查，不是实盘认证。
- 集群默认 python `/opt/network/anaconda3/envs/pytorch/bin/python` 需 ≥3.11（原包已使用 `hashlib.file_digest`）。
