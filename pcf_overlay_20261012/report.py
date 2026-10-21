"""Produce a reviewable initial receipt and static research plots."""
import json
import numpy as np
import pandas as pd
from .data import ROOT,OUT,write,sha

def pct(x):return f'{x*100:.2f}%'

def main():
    s=pd.read_csv(OUT/'scorecard.csv').set_index('arm')
    decision=json.loads((OUT/'decision.json').read_text())
    fund=json.loads((OUT/'fund_nav_official/summary.json').read_text())
    training=json.loads((OUT/'training_followup/status.json').read_text())
    example=json.loads((OUT/'interface_example/sample_receipt.json').read_text())
    table=['账户\t区间净收益\t对H00905年化相对超额\t跟踪误差\t50日超额p10\t500占NAV≤80%天数']
    for arm in ['P0','P1','P2','S']:
        r=s.loc[arm];table.append('\t'.join([arm,pct(r.net_return),pct(r.annualized_relative_excess),pct(r.tracking_error),pct(r.w50_p10),str(int(r.member80_violation_days_after_three))]))
    text=f'''研究启动回执（2026-10-11）

已完成第一轮固定参数 PCF 四账户历史研究、独立基金净值核对、交易接口确认和本地 CPU 推理骨架；未来训练结果继续按计划落地。

判定：历史收益门槛通过，执行就绪门槛未通过。保持研究/影子候选，不能据此切换实盘。
P1相对P0年化相对财富增量 {pct(decision['annualized_P1_vs_P0'])}（门槛1%）；50日H00905相对超额p10改善 {pct(decision['w50_p10_delta'])}（容许恶化至-0.30%）。
每日收益差的20日块自助法95%区间为年化 {pct(decision['block_bootstrap_mean_daily_difference_annualized95'][0])} 至 {pct(decision['block_bootstrap_mean_daily_difference_annualized95'][1])}，不代表未来保证。

1. 固定历史实验
PCF覆盖856份镜像、10份官方XML，总计866个文件日期。价格宇宙1390只，实际有研究期价格1369只；另外21只在研究开始前已退市。补取212只公开日线并保存原始响应和哈希。
目标从2023-03-13的PCF与15点信号形成，第一次成交2023-03-14；至2026-09-23共859个账户日期、858个建仓后收益日。完整交易日历校验通过，所有PCF估值价格完整，缺价整篮暂停天数为0。
每账户200万元，买卖每边万二，k=0.5，z截断[-2,2]，EMA=0.2，仓位最多98%，保留PCF现金部分；强制现金替代不买入，缺模型分数仍保留该股中性alpha。参数预登记先于结果，未做网格挑参。
P0：T日PCF，T+1开盘；P1：同篮子加T日15点滚动5种子分数，T+1开盘；P2：同篮子加执行日14点分数，14:01–14:05价格；S：指数市值骨架加同15点分数，连续现金账户重跑。
小时模型y4约为1交易日，不称为日频5日标签。15/14点种子取每年独立滚动测试窗口，源文件哈希保留。

{chr(10).join(table)}
共同H00905区间收益 {pct(s.loc['P0','H00905_return'])}，价格指数 {pct(s.loc['P0','price_index_return'])}。建仓日手续费保留，但主统计重置在首个收盘，排除建仓本身的收益。
P2缺少PCF全部股票的分钟成交带，最大受阻目标权重 {pct(s.loc['P2','max_fill_blocked_weight'])}；实际现金平均 {pct(s.loc['P2','average_cash'])}。其数字只作不完整成交诊断，不可作为纯粹执行时点优势证据。

2. 独立基金净值
来源：https://www.efunds.com.cn/Mobile/fund/563030.shtml
按该官网页面实际调用的官方API获取独立净值863条。按PCF的前一交易日估值日期对齐，861对单位净值最大差为0，0个超0.00005差异；这证明对应NAV吻合，不能认证历史发布时间、修订情况或完整历史持仓。
同一账户区间基金净值再投资收益 {pct(fund['fund_return'])}，对H00905年化相对超额 {pct(fund['fund_annualized_relative_H00905'])}。
P0相对基金自身年化 {pct(fund['P0_annualized_relative_to_fund'])}，区间累计少 {pct(fund['fund_minus_P0_cumulative_return_points'])}。差异混合了滞后、费用、持仓复制差异与结算近似，不能全部归因于T+1延迟。

3. 上线前发现的执行缺口
P1建仓后三天起有{decision['P1_member80_violation_days_after_three']}天中证500股市值占账户NAV不高于80%，P0为17天。最低约72.39%（P1），所以收益门槛不能覆盖成员约束。
2026-09-22信号、09-23执行的纯复制格式样例，200万元按源码整手向下取整与科创200股最低目标检查后，股票仓位 {pct(example['checks']['rounded_stock_weight'])}，500占NAV {pct(example['checks']['member_nav_weight'])}，取整误差约{example['checks']['round_lot_value_error']:,.0f}元。它是无真实GRU checkpoint的历史格式演示，不是上线目标。高价股最小买入单位造成明显损失，不能把200万分账直接视作950万整账户。
后续须另登记实际整手、公司行动和成员占比约束版本，保留本轮固定基线，不通过改参数回填本轮结果。

4. 日更链与交易文件
已安装本地CPU torch，推理接受股票×60日×98维；严格日期、面板指纹和年份检查，过期数据不回退，使用同期风险暴露中性化，再与PCF左连接。
真实external_file接口读取JSON，包含strategy_id、execution_date、signal_date、mode=weights和targets[code,weight]；code采用SH600000/SZ000001，weight占整个账户总NAV。CSV只作核对，交易程序据自身账户NAV和报价取整，仍受单股上限、资金上限及仓位策略约束。
输出位于interface_example，未接入交易程序。当前日频GRU checkpoint尚未同步，冻结面板止于9-23；已完成推理骨架及合成checkpoint测试，尚未完成真实当日98维增量更新和真实checkpoint日更链。新面板需建立并验证训练指纹到增量特征的来源链，不能简单绕过指纹检查。
验证：10项因果、缺失分数、现金费用、失败成交、批次一致性、残差、估值/取整日期区分和整数可行性测试通过；四账户完整日历、正净值、非负现金、费用和重基准检查通过。

5. 训练结果落地
本地已同步A1 {training['counts']['A1']} /120（原30+新90），日频GRU {training['counts']['GRU']}/30，LightGBM {training['counts']['LightGBM']}/30。尚无完整20种子年份，未给全量采纳判定。
已写结果收集器：完整集合到齐后生成20种子逐年记分卡、已有SWA/best同日配对、固定种子分组的分数噪声收缩图；日频GRU/LightGBM和小时模型按同日期、同股票、同日频标签计算秩相关和残差RankIC；A4与同5种子control做14点配对。噪声图中n=20的零为相对自身的机械零，不是模型误差消失。
每小时本地跟进自动化：PCF 与 A/B 研究结果落地，id=pcf-a-b。本自动化读取同步后的本地结果；集群作业、同步和预算继续由原双平台任务负责。只有实质结果/失败/用户资料需求才通知。
仍待：A3双窗口40模型回放、完整SWA决策、队友R/H/C与c5回传、真实日更特征和账户/规则约束研究；未来任务未标完成。

6. 资料依赖
需要当日账户持仓与现金、组委会正式细则、五个真实子账配置，以及新鲜日线/分钟数据来源。没有这些资料不做实盘接入。
本轮价格使用复权的分数单位、非整手成交近似，未逐笔结算现金分红/送转；腾讯补充数据缺历史ST标签，未模拟涨跌停排队与成交容量。历史PCF发布时间未认证，上游因子筛选及历史测试已暴露。这些限制保留在登记卡，不据此声称可实现实盘收益。

可复查材料：
scorecard.csv / yearly_scorecard.csv：总体与逐年全指标；P0/P1/P2/S_daily.csv：逐日NAV、现金、费用和成员占比；对应trades.csv：成交与费用。
daily_input_coverage.csv：每次执行对应PCF/分数/价格覆盖；decision.json：两种门槛；preregistration.json：固定口径。
fund_nav_official/summary.json 与 pcf_nav_crosscheck.csv：独立来源与对齐；result_validation.json：结果核验。
interface_example：历史JSON和CSV样例及预检；training_followup：目前完整/待到清单与已有种子统计。

复现入口（项目根目录执行）：
.pcf-research-venv/bin/python -m pcf_overlay_20261012.replay
.pcf-research-venv/bin/python -m pcf_overlay_20261012.fund_nav
.pcf-research-venv/bin/python -m pcf_overlay_20261012.collect_results
.pcf-research-venv/bin/python -m unittest pcf_overlay_20261012.test_research
'''
    (OUT/'研究启动回执_2026-10-11.txt').write_text(text,encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    f=pd.read_csv(OUT/'fund_nav_official/matched_wealth.csv');dates=pd.to_datetime(f.date)
    st=pd.read_csv(OUT/'S_daily.csv');f['S']=st.nav_after_build
    fig,axes=plt.subplots(2,1,figsize=(10,7),sharex=True,gridspec_kw={'height_ratios':[2,1]})
    for name,label in [('P0','P0 PCF next-open'),('P1','P1 PCF + GRU k=0.5'),('S','S index + GRU'),('fund_NAV_reinvested','Fund official NAV'),('H00905','H00905 total return')]:
        axes[0].plot(dates,f[name],label=label,linewidth=1.3)
        if name!='H00905':axes[1].plot(dates,f[name]/f.H00905-1,label=label,linewidth=1.2)
    axes[0].set(ylabel='Wealth after initial build',title='Historical diagnostic | CNY 2m | 2 bps per side | fractional adjusted units')
    axes[0].legend(loc='upper left',fontsize=9);axes[1].set(ylabel='Relative wealth vs H00905');axes[1].axhline(0,color='gray',linewidth=.7)
    for ax in axes:ax.grid(alpha=.2)
    fig.text(.07,.015,'P2 omitted: incomplete minute execution coverage. Historical gains do not certify live execution.',fontsize=8)
    fig.tight_layout(rect=[0,.035,1,1]);fig.savefig(OUT/'historical_wealth_comparison.png',dpi=170);plt.close(fig)
    write(OUT/'research_output_manifest.json',[{'path':str(p.relative_to(ROOT)),'sha256':sha(p)} for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='research_output_manifest.json'])
    print('REPORT_COMPLETE',flush=True)

if __name__=='__main__':main()
