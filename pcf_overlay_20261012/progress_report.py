"""Updated research progress; keep full-seed evidence separate from readiness."""
import json
from datetime import datetime,timezone
import pandas as pd
from .data import OUT,write,ROOT

def main():
    dest=OUT/'training_followup';status=json.loads((dest/'status.json').read_text())
    ensemble=pd.read_csv(dest/'daily_5seed_ensemble_yearly.csv')
    paired=pd.read_csv(dest/'daily_vs_hourly_yearly.csv')
    swa=json.loads((dest/'T2021_SWA_paired_bootstrap.json').read_text())
    intstatus=json.loads((OUT/'integer_target_research/historical_target_feasibility_summary.json').read_text())
    samples=json.loads((OUT/'integer_target_research/latest_example_feasibility.json').read_text())
    current=samples[0];old=json.loads((OUT/'interface_example/sample_receipt.json').read_text())['checks']
    a1=pd.read_csv(dest/'20seed_yearly_scorecard.csv')
    lg=ensemble[(ensemble.family=='LightGBM')&(ensemble.horizon_days==5)]
    lp=paired[(paired.family=='LightGBM')&(paired.horizon_days==5)]
    lg_mean=float(lg.rank_ic.mean());residual=float(lp.residual_rank_ic.mean());recent=float(lp.loc[lp.year>=2023,'residual_rank_ic'].mean())
    rows=['年份\t日频LGB五种子5日RankIC（500∪1000）\t对小时信号残差RankIC（共同股票）']
    for r in lg.itertuples():rows.append(f'{r.year}\t{r.rank_ic:.5f}\t{lp.set_index("year").loc[r.year,"residual_rank_ic"]:.5f}')
    note=f'''研究进度更新（2026-10-11，本地报告生成于 {datetime.now(timezone.utc).astimezone().isoformat()}）

进度：A1 {status['counts']['A1']}/120（日历六年旧30+新90），LightGBM {status['counts']['LightGBM']}/30，日频GRU {status['counts']['GRU']}/30。
本次CUHK实时检查与新增模型快照时间14:23，A1新模型18个完成、数组任务18继续运行；B2排在A1后，A4排在B2后，A3排在A4后。LightGBM30个完整导出已同步，尚无B2/A4完整输出。队列依赖等待不是训练失败。
新同步T2022 seed88共20个文件，归档和逐文件SHA256与服务器清单全部匹配。没有重提作业、修改训练代码或新增收费算力。

1. A1 首个完整20种子年份已汇总
完整年份：{status['complete_20seed_years']}。
T2021 15:00，20种子集成RankIC {a1.set_index('year').loc[2021,'rank_ic']:.5f}；原5种子同一收集口径约0.03924，20种子并未在此年份提高IC。固定种子组分数波动随组大小减少，但不等于预测效果一定提升。全六年结果尚未齐，不挑一个年份作全量选型结论。
T2021新15种子best与SWA同日15点配对：SWA减best平均 {swa['mean_rank_ic_delta_swa_minus_best']:.5f}；20日块自助法95%区间 {swa['block20_bootstrap95'][0]:.5f} 至 {swa['block20_bootstrap95'][1]:.5f}；15个种子中改善者{swa['positive_seed_count']}个。
当前证据不支持“全部切换SWA”；先保留best，等六年完成后按原门槛决定。原5种子多数年份并无SWA，不把15个新种子的配对结果冒充20个SWA模型。

2. LightGBM 六年五种子已完成
使用每种子截面z分数的五种子平均，并分别评估1日和5日头。下面是五种子集成结果，不是单模型IC平均：
{chr(10).join(rows)}
六年等权年度5日RankIC均值 {lg_mean:.5f}。全日频宇宙含500与1000，不能直接与小时模型500宇宙的原始IC横比。
对比时严格取同日同股票，双方使用相同日频未来标签；5日残差RankIC六年年度均值 {residual:.5f}，2023–2026均值 {recent:.5f}。这低于计划0.03信息增量门槛，仅是LGB基线对照，不代替尚未完成的日频GRU决策。GRU–LGB相关性仍待GRU完整输出。
T2021/2022共享上游2019–2022冻结因子选择暴露；高IC不能描述为完全未见过的独立测试。

3. 整手和仓位约束新增研究
独立登记两种离线方法：最新样例的整数优化，及所有859日的确定性整手目标构造。原四账户收益基线不变。
修正了样例的一处价格时点：PCF数量权重用PCF前一交易日估值收盘价，股数取整用信号日收盘价（执行日前收盘）。修正前样例另存于interface_price_clock_before_correction，原回放此前已分开使用两种时点，因此收益及历史门槛未变化。
200万、2026-09-22信号/09-23执行，按原程序逐股向下取整的股票仓位为 {old['rounded_stock_weight']*100:.2f}%，500占NAV {old['member_nav_weight']*100:.2f}%；修正后整数优化目标股票仓位 {current['stock_NAV']*100:.2f}%，500占NAV {current['member_NAV']*100:.2f}%，单股最大 {current['max_stock_NAV']*100:.2f}%。整数优化满足冻结现金与成员约束，求解达到登记相对误差范围；CSV和shares模式JSON均在研究目录，未接入交易。
完整历史：{intstatus['tested_dates']}日，成功构造达标目标 {intstatus['feasible_target_dates']}日；最低股票仓位 {intstatus['minimum_constructed_stock_NAV']*100:.2f}%，最低信号时500占NAV {intstatus['minimum_constructed_member_NAV']*100:.5f}%。
按原向下取整方法，{intstatus['naive_floor_under90_dates']}日股票仓位不足90%，{intstatus['naive_floor_member80_fail_dates']}日成员占NAV不高于80%。取整问题是系统性的，不能直接输出分数权重后任由程序逐股舍去。
构造后相对原分数目标的NAV权重L1误差中位数 {intstatus['median_L1_target_error']*100:.2f}个百分点，最大 {intstatus['max_L1_target_error']*100:.2f}个百分点；这是目标偏离，不是已实现收益或实际换手。200万比950万整账户更易受最小股数影响；950万样例有可行解但未证明最优。
重要界限：以上用信号时价格/成员名单，只认证可构造目标。执行日成员变化、实际持仓可卖数量、停牌/ST/涨跌停、失败成交和公司行动现金/股数结算均未认证；P1约束回放尚未完成。不能拿这859日目标达标替代实际账户每天达标，也不能把原P1增益1.93%搬到新整手方法。

4. 验证与下一步
10项测试通过，包括现金、因果价格、PCF估值/执行参考日期、科创最小目标、可行/不可行整手边界；新增完整目标表逐股整手、单股限额、现金含初始买费和成员占比核验。
下一步：继续等待A1/B2/A4/A3预定完整结果；建立P0/P1真实整数现金账本与公司行动分拆、执行日约束检查；日频checkpoint到齐后验证真实98维日更链。账户/规则/真实子账与日更数据仍是外部依赖，保持离线研究，不启动交易。
继续每小时本地结果跟进，原集群监控继续同步和预算保护。无实质变化保持安静。

证据位置：training_followup/20seed_yearly_scorecard.csv、seed_noise_fixed_groups.png、T2021_SWA_paired_bootstrap.json、daily_5seed_ensemble_yearly.csv、daily_vs_hourly_yearly.csv。
integer_target_research/P0_all_dates_target_feasibility.csv、P0_all_dates_integer_targets.parquet、historical_target_feasibility_summary.json、latest_example_feasibility.json。
progress_sync_20261011/sync_receipt.json；price_clock_correction.json；研究启动回执_2026-10-11.txt。
'''
    (OUT/'进度更新_2026-10-11.txt').write_text(note,encoding='utf-8')
    write(OUT/'current_progress.json',dict(updated_at=datetime.now(timezone.utc).isoformat(),
        local_complete_models=status['counts'],complete_A1_20seed_years=status['complete_20seed_years'],
        LightGBM_5day_six_year_ensemble_rank_ic=lg_mean,LightGBM_5day_hourly_residual_rank_ic=residual,
        T2021_SWA_vs_best_delta=swa['mean_rank_ic_delta_swa_minus_best'],
        feasible_P0_integer_target_dates=intstatus['feasible_target_dates'],
        target_construction_verified=True,integer_cash_account_replay_complete=False,daily_live_chain_complete=False,
        live_activation=False,report=str(OUT/'进度更新_2026-10-11.txt')))
    print('PROGRESS_REPORT_COMPLETE',flush=True)

if __name__=='__main__':main()
