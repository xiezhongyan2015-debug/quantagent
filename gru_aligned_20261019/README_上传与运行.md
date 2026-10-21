# gru_aligned_20261019：对齐目标 GRU 与热启动微调（上传算力平台用）

## 这批训练做什么

实盘的倾斜是 w ∝ b(1+kz)，其中 z 是"中性化 → EMA → 标准化"之后的 GRU 分数，所以组合吃到的是 15:00 中性化后信号与收益的底仓加权协方差。现在的训练优化的是另一件事：等权 RankIC、四个时点四个头等权，并且按 14:00 选模。这批实验把组合口径直接写进损失，再拿同样的种子和窗口与原配方做配对比较。

| 臂 | 做法 |
|---|---|
| C0 | 原配方（代码路径与实盘 ctrl5 相同），另存两个按 15:00 指标选出的 checkpoint（G0） |
| G1a | 损失 = 0.5×原损失 + 0.5×对齐项；对齐项为 15:00 生产中性化后 z 与 y4 秩正态的等权协方差 |
| G1b | 同上，按中证500 PIT 官方权重加权（γ=1） |
| G1c | 原损失权重 0.2，γ=0.5 |
| G2b | G1b 的 15:00 目标换成 10 日、0.8 折扣的多日 y4 |
| G4 | 原损失加 SAM（ρ=0.05） |
| G6（另一个作业） | 实盘 T2024–T2026 模型在 10 个历史切点做热启动微调回放（A/B1/B3/C1/C3） |

每个训练臂都保留原来的早停规则，各存三个 checkpoint：`best`（原 14:00 选模）、`best15n`（15:00 中性化 RankIC，**主口径**）和 `best15b`（15:00 b 加权协方差）。协议在训练前冻结，见 `PROTOCOL_v3.json`，其中写明了臂数、种子、窗口、上线门槛和判伪条件。

## 本地已完成的检查

- `test_aligned.py`（真实面板，CPU）：
  - 投影与生产 `neutralize()` 的最大误差 3.8e-8；
  - 完全对齐时 A=−1；
  - 加任意风格组合后 A 不变；
  - 梯度落在风格空间的分量 1e-13；
  - 10 日目标的清洗边界正确。
- 冒烟：在 CPU 上把 C0、G1a、G1b、G2b、G4 各训练 2 个 epoch（每个 epoch 限 2 个 batch）后导出，全部跑通；G6 在 1 个切点上 5 个变体全部跑通。冒烟里的数字没有意义。
- 辅助数组 `aux/aligned_aux_20261019.npz` 由 `build_aux.py` 从本地冻结面板生成，时间轴和股票轴都和集群上的 `/data/users/s1155284034/gru_hourly_20261001/dataset` 一致（训练时会断言）。成员覆盖率：beta60 93%、波动 97%、指数权重 97%、10 日目标 97%。

## 上传与运行（在集群上）

1. 把 `gru_aligned_20261019_upload.tar.gz` 上传到 `/home/users/s1155284034/gru_hourly_20261001/`，然后：
   ```bash
   cd /home/users/s1155284034/gru_hourly_20261001
   tar -xzf gru_aligned_20261019_upload.tar.gz
   cd gru_aligned_20261019 && sha256sum -c SHA256SUMS && cd ..
   ```
   它只会新建 `gru_aligned_20261019/` 和 `outputs/gru_aligned_20261019/`，不会覆盖现有文件。依赖集群上已有的 `gru_market_attention_20261007/`（含 context.npz）、`gru_walkforward_20261010/` 和数据集目录。

2. 预检（约 20–40 分钟，1 块 GPU）：
   ```bash
   sbatch gru_aligned_20261019/run_checks.sbatch
   ```
   日志在 `outputs/gru_aligned_20261019/logs/check-<jobid>.log`，最后一行应为 `ALL CHECKS PASSED`。

3. 正式提交（预检通过后）：
   ```bash
   sbatch gru_aligned_20261019/run_aligned.sbatch
   sbatch gru_aligned_20261019/run_finetune.sbatch
   ```
   - `run_aligned.sbatch`：90 个任务（6 臂 × 3 窗口 × 5 种子），默认 `%15` 表示同时最多占 15 块卡。卡多可以改大，比如提交时用 `sbatch --array=0-89%30 ...` 覆盖。
   - 估计耗时：每个任务 1.5–2 小时，G4（SAM）约 3 小时，合计约 160–190 GPU 小时。15 卡并行约 12 小时。
   - `run_finetune.sbatch`：5 个任务（每个种子 1 个），每个约 1–2 小时。
   - 中断后重新提交同一脚本会从 `last.pt` 续跑，已完成的任务会自动跳过。
   - 进度：`squeue -u $USER`；`tail -f outputs/gru_aligned_20261019/logs/aligned-<jobid>_<id>.log`。

4. 自检：C0 在 T2026 窗口的 5 个种子用的就是实盘 ctrl5 的配方和种子。若 GPU 确定性成立，`best_epoch` 应依次为 5/8/4/6/3（种子 11/22/33/44/55），`best_validation14_rank_ic` 应约为 0.0725/0.0725/0.0731/0.0717/0.0734（对照 `gru_aligned_20261019/base_models/T2026/control_seed*/result.json`）。不一致也不影响配对比较，因为同一批作业里的 C0 和 G 臂是同条件训练的，但请告诉我差了多少。

5. 回收结果：
   ```bash
   bash gru_aligned_20261019/collect_results.sh
   ```
   它会打包 `result.json`、`export.json`、`epochs.jsonl`、测试分数和验证分数（`*.npz`）、微调回放结果和日志，不含 checkpoint，预计 2–4 GB。下载后放到本地 `outputs/gru_aligned_20261019/downloads/`，我来做实盘构造回放和配对检验。

## 结果回来之后怎么判

按 `PROTOCOL_v3.json` 的 `go_live_gate` 判断，四条同时满足才算通过：
1. 比较对象是实盘构造（c5 混合、PCF、同行业回填、beta 倾斜、死区）；
2. 2025、2026 两年各自至少 4/5 个种子对为正；
3. 2025-01 至 2026-09 年化 ≥ +0.8 个点，90% 下界 ≥ −0.5；
4. 500 占比、beta、行业偏离、最差 20 日都不恶化，换手增幅 ≤10%。

通过之后还要影子运行 5 个交易日、经你批准才能上线，每周最多改一处。G1a/b/c 里谁胜出，只按 T2024 窗口（测试年 2024）的回放来选，2025–2026 只用于确认。

## 本批不包括

- G3 换手项：需要成对的连续交易日 batch，下一批再做。
- G7 分钟专家：要等零 GPU 诊断 Z3、Z4 的闸门结果。
- G8 全 A 预训练：要等全 A 分钟数据。
