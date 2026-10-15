OLD_RANK：四头、五 seed 的截面 rank GRU 回测交付包
=================================================

这是已经完成训练的 OLD_RANK：122 维输入、80 小时窗口、64 个人工因子
截面 uniform-rank、四头输出，实际 seed 为 66 / 77 / 88 / 99 / 111。
本包对应原 rank GRU 基准；正在训练的 134 维分钟统计实验另行交付。

一、最快开始：直接使用历史 y4 信号

推荐文件：signals/old_rank_y4.csv.gz，可由 pandas.read_csv 直接读取。
共 318086 行有效评分，列为 signal_date,symbol,score,signal_time_ns,role。
signal_time_ns 是 UTC 纳秒整数；日期与 15:00 时点使用 Asia/Shanghai。
用 Python/int64 读取纳秒时间，避免表格软件把它转成浮点数。

完整矩阵：signals/OLD_RANK_ensemble_signals.npz
SHA256: e402d5a3c48ad45bd3d4c5e8c4fde068f5a9d37933a12295519f035e5ee63b1f
6 个字段：scores[662,1178],dates,symbols,signal_time_ns,membership,roles。
缺失评分保留 NaN。1178 是跨历史股票轴，不代表每天有1178只可评分股票。

评分已经完成：逐 seed 的 y4 截面 population-z -> 五 seed 等权平均 ->
波动率/换手率/log市值/beta60 四风格 OLS 中性化 -> EMA(alpha=.2) ->
当前成员截面 population-z。请直接使用 score，不要再重复中性化或 EMA。
它是排序评分，不是校准后的预期收益率，也不是原始 y4 输出。

时间区间：
  2024-01-02 至 2024 年末：242 个信号日，role=warmup2024。
    2024 被用于验证和选模；这里只供 EMA 预热，不可声称独立样本外业绩。
  2025：243 个信号日，role=primary2025。
  2026 至 2026-09-23：177 个信号日，role=carry2026。
    沿用同一 T2025 模型，不是每年重新训练的 walk-forward。
  合计662个信号日。既往研究已看过2025/2026历史，不能声称全新未触碰OOS。

二、接队友的 PCF 回测引擎

建议采用收盘T15评分、下一交易日开盘成交的口径，手续费在队友引擎中计算。
历史同口径比较使用 K=.5、单边0.0002；本包 helper 只生成目标，不计算收益。

对每个 PCF 股票：m = 1 + K * clip(score,-2,2)。
缺评分、股票不在模型轴或不是模型成员时，m=1，保留原PCF股票。
target_weight = 原PCF股票预算 * (pcf_weight*m) / sum(pcf_weight*m)。
这样保持当日原股票预算和现金比例。不要把缺评分股票当作排雷删除。

安装依赖（已有环境可跳过）：
  python -m pip install -r requirements.txt

校验现成信号：
  python verify.py signals --file signals/OLD_RANK_ensemble_signals.npz --sha256 e402d5a3c48ad45bd3d4c5e8c4fde068f5a9d37933a12295519f035e5ee63b1f

也可重新导出 CSV（输出路径必须尚不存在）：
  python backtest_inputs.py export --signals signals/OLD_RANK_ensemble_signals.npz --out my_scores.csv

准备 pcf.csv：date,symbol,weight（date 是评分日，weight 是0至1的小数权重）。
准备 signal_execution.csv：signal_date,execution_date，显式写下一交易日。
只把评分时已经可得的PCF权重接入；日期标签本身不能证明PCF公布时点。
运行：
  python backtest_inputs.py tilt --signals signals/OLD_RANK_ensemble_signals.npz --pcf pcf.csv --mapping signal_execution.csv --weight-date signal_date --k 0.5 --out targets.csv

targets.csv 保留全部 PCF 行，并附原权重、评分、乘子、缺分状态和目标权重。
工具要求非负乘子及正归一化总和；全体乘子为零时会报错，需要回测引擎
明确现金回退规则。它不内置停牌、涨跌停、成交量约束、滑点或组合优化器。
K 可改，但请把参数选择与最终评估区间分开，不要按同一测试期收益挑K。

三、模型权重与重新推理

checkpoints/rank_seed*.pt 是只含模型 state_dict 的安全加载文件，原模型张量
逐值保留；不含 optimizer、RNG、账户信息或训练数据。
weights_manifest.json 记录每份文件 SHA、原 best 检查点 SHA 和张量摘要。

校验五份权重（清单 SHA 固定）：
  python verify.py weights --file weights_manifest.json --sha256 9021e8e79c6064f947b2525e552a127cd851c9e7781ee734714c1c909d14b782

模型需要原生、按时点可得且已归一化的122列输入，不是直接喂OHLCV。
feature_names.json 和 input_spec.json 给出列顺序、39类行业字典、时间槽、
完整80小时资格及因子rank公式；原始行情到122列的全套数据生产链不在本包。
64个因子必须先按当日完整历史成员截面rank，再筛选可前推股票。
make_windows() / rank_factor_day() / predict_windows() 位于 predict.py。

已有适配后的窗口 NPZ，包含 x[N,80,122] float32、industry[N,80] int64、
slots[80] 或 [N,80] int64 时，可运行：
  python predict.py --manifest weights_manifest.json --manifest-sha256 9021e8e79c6064f947b2525e552a127cd851c9e7781ee734714c1c909d14b782 --windows windows.npz --factor-input-state uniform_rank_of_frozen_clipped_z --device cpu --out raw_predictions.npz

输出是原始四头 [5,N,4]，最后一维index3为原始y4，不能直接当作前述score。
processed_head4() 可在完整时间序列上复现后处理，但还需要逐时点可得的
四风格暴露；从2024预热开始，跨2026不重置EMA，保存的是最终z之前的EMA状态。

训练：2019—2023；验证和选模：2024 14:00 y4 RankIC；原四头标签保持不变。
这不是早期 rank-label3 模型，也不是新5/20日VWAP标签模型。
模型保留原注册attention/FiLM参数以严格加载原权重；control前推不执行它们。

SHA256SUMS.json 校验包内文件。PACKAGE_VALIDATION.json 区分真实权重/信号验证
和合成输入测试；本交付没有因上传而重新训练、重新选模或产生新的收益结论。
