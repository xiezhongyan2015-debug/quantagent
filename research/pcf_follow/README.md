# PCF 跟随研究与目标持仓接口

本目录将 563030 易方达中证500增强策略ETF的历史PCF跟随研究移入仓库，并提供与现有 `live target` 相同核心字段的独立影子账户接口。核查基线：主分支 053efee47e49604fced3ea79558be1757204f79f。

后续新增收盘归档、Choice适配器及Windows定时任务，见 [自动化部署说明](../../docs/PCF_CHOICE_AUTOMATION.zh-CN.md)。该执行器09:35开始，历史开盘回测不代表实际执行收益。

## 接入位置

现有 `scripts/competition_daily.sh` 自动更新数据、打分、生成目标持仓；`docs/COMPETITION_OPS.md` 将实际执行交给外部程序。本仓库这一流程未包含可直接接通券商的下单实现。新模块也只生成计划，不发送订单；原 LightGBM 日任务不变。

安装：`pip install -r research/pcf_follow/requirements.txt`，并安装项目或设置 `PYTHONPATH=src`。

```bash
python -m quanta_agents.factor_lab_a.pcf fetch --date 2026-09-17 --out shadow_pcf/pcf.json
python -m quanta_agents.factor_lab_a.pcf target --pcf shadow_pcf/pcf.json --quotes quotes.csv --holdings holdings.csv --calendar calendar.json --asof 2026-09-17 --execution-date 2026-09-18 --nav 1000000 --lag 1 --out shadow_pcf
```

输出 `target_20260917.csv/json`。CSV兼容字段：code、bucket、score_pct、state、held_mv、target_mv、exec_shares、exec_mv、delta_shares、delta_mv、close、is_st、action。`exec_shares` 是最终目标股数，`delta_shares` 是相对输入持仓的增量。不要将目标股数作为增量下单。新程序附加 blocked、pcf_weight 和执行日期元数据。

输入契约：

- quotes.csv：code,date,close,reference_date,reference_close,member_csi500,member_union,is_st,tradable。close 为 asof 收盘的不复权价；reference_close 为 PCF 的 PRETRADINGDAY 不复权收盘价。指数成员必须为 asof 当时有效成员，四个标志为明确0/1。
- holdings.csv：code,shares，仅包含独立PCF子账户，空账户也要提供表头。nav 为该账户现金加当日收盘股票市值。
- calendar.json：递增且无重复的交易所交易日字符串数组，必须覆盖参考日、PCF日、asof及执行日。不得用自然工作日代替交易日。
- 股票代码支持600001、600001.SH、SH600001等沪深A股格式。缺失报价、非法标志或日期不一致直接报错。

使用PCF数量乘其前一交易日价格/NAVPERCU，超过98%才等比压低，现金不重新分配。PCF日期之后第1/3/5个交易日执行；asof必须为执行日前一交易日。目标向下取整，科创板买入至少200股，其余至少100股；保守整手处理，ST或不可交易股票冻结。按买卖各千分之一预留费用，卖出优先。**回测采用碎股近似，而此接口按手数生成计划，二者收益不应直接等同。**

JSON包含原仓库precheck的持仓比例、单股及部分成交场景检查。检查失败仍输出供审阅的影子计划，不代表批准执行。外部执行器必须检查precheck、实际可卖数量/T+1、价格和涨跌停、账户约束、现金以及订单幂等性，并按当时价格重新检查；该适配器尚未与实际券商执行器联调。

## 历史研究复现

默认数据根目录为仓库根目录，可通过 `PCF_PROJECT_ROOT` 指向现有研究数据根目录。原始行情和历史PCF不随代码PR上传，也没有声称缺少数据时可以一键复现。

需要：

- data_expansion/normalized/benchmark_index_daily.parquet：date(datetime), symbol（含000905.SH），用于历史交易日。
- data_expansion/normalized/daily_stock_panel.parquet：date(datetime), symbol（600001.SH格式）, open/close（同口径复权）, raw_close（不复权）, amount, is_st_bs。
- 历史PCF与补充行情目录：data_expansion/public_fund_holdings/pcf_history_563030/。下载脚本可生成raw、components.parquet、basic.json及prices；行情不足时必须补齐，不能以当前成分股过滤历史股票。

```bash
python research/pcf_follow/pcf_research.py
python research/pcf_follow/pcf_prices.py
python research/pcf_follow/fetch_csi500_total_return.py
python research/pcf_follow/pcf_backtest.py
python research/pcf_follow/pcf_report.py
python -m unittest discover -s research/pcf_follow -p test_pcf_backtest.py
python -m unittest discover -s tests -p test_pcf_target.py
```

研究区间固定2023-03至2026-09-17；延长必须同步更新各脚本日期并补全行情。结果写入本目录results/，原始数据及生成结果不提交。

`recent_comparison.py` 从回测daily_returns.parquet和ETF行情gzip生成近半年比较。行情gzip格式为 `{symbol,url,data:{data:{timestamp:[毫秒时间戳],open:[...],close:[...]}}}`；公共行情来源 `https://free-api.tickflow.org/v1/klines`，参数 symbol、period=1d、count=400、adjust=forward。应保存完整响应及抓取时间。命令：

```bash
python research/pcf_follow/recent_comparison.py --returns research/pcf_follow/results/daily_returns.parquet --prices /path/to/etf_price_snapshots --out research/pcf_follow/recent_six_months.json
```

全部收益对 H00905 中证500全收益指数计算几何超额：策略净值/基准净值-1；最大回撤是超额净值回撤。复权股票/ETF计入分红影响，交易买卖各0.001；不强制期末清仓。复权口径未扣除个人股息税，行情质量和复权处理仍影响结果。

历史PCF按日期重新下载，无法证明文件当年发布时的版本和精确时间。日期滞后与价格因果性检查通过，也不能消除此历史版本风险。PCF是申赎篮子，不保证等于实际完整持仓；现金替代和篮外持仓会造成偏差。563030是观察近期表现后选出的样本，存在选样偏差。
