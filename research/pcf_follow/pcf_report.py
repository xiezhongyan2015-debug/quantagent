"""Render the completed PCF experiment, without selecting variants by performance."""
import json
import hashlib
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pcf_backtest import RESULT,OUT

def main():
    summary=pd.read_json(RESULT/'summary.json');ret=pd.read_parquet(RESULT/'daily_returns.parquet')
    coverage=pd.read_parquet(RESULT/'coverage.parquet');audit=json.loads((RESULT/'verification.json').read_text())
    names={'pcf_lag1_cost10bp':'PCF延迟1日，单边10bp','pcf_lag3_cost10bp':'PCF延迟3日，单边10bp','pcf_lag5_cost10bp':'PCF延迟5日，单边10bp',
           'etf563030_buyhold':'直接持有563030','etf510500_buyhold':'直接持有510500','csi500_total_return_index':'中证500全收益指数',
           }
    def table(period,keys):
        lines=['| 方案 | 累计超额收益 | 年化超额收益 | 超额净值最大回撤 |','|---|---:|---:|---:|']
        for k in keys:
            r=summary[(summary.model==k)&(summary.period==period)].iloc[0]
            lines.append(f'| {names[k]} | {r.excess_total:.2%} | {r.excess_annual:.2%} | {r.excess_max_drawdown:.2%} |')
        return '\n'.join(lines)
    keys=['pcf_lag1_cost10bp','pcf_lag3_cost10bp','pcf_lag5_cost10bp','etf563030_buyhold','etf510500_buyhold','csi500_total_return_index']
    annual=['| 时段 | PCF延迟1日/10bp | 563030买入持有 | 510500买入持有 |','|---|---:|---:|---:|']
    for period in ['2023','2024','2025','2026','2026H2']:
        vals=[summary[(summary.model==k)&(summary.period==period)].iloc[0].excess_total for k in [keys[0],keys[3],keys[4]]]
        label={'2023':'2023年3月20日起','2026':'2026年至9月17日','2026H2':'2026年7月1日至9月17日'}.get(period,period)
        annual.append('| '+label+' | '+' | '.join(f'{v:.2%}' for v in vals)+' |')
    mainrow=summary[(summary.model==keys[0])&(summary.period=='full')].iloc[0]
    lines=[
        '# 按历史PCF跟随易方达中证500增强ETF：首轮回测',
        '研究对象为用户截图中的563030，不能将结果外推至其他管理人的增强ETF。本版按用户2026-09-20要求统一单边千分之一手续费及超额收益，替代此前0.2%费率和价格指数基准的展示结果。原始冻结方案留档，覆盖规则记录于reporting_conventions.json。结果是历史公开存档的延迟跟随研究，不是已认证的逐时点历史披露数据库回测。',
        '完成时间：2026-09-20。官方PCF覆盖2023-03-13至2026-09-17，共856个交易日、159277条证券记录、798个证券代码。补充154只现有股票池之外的股票行情，所有正数量PCF条目均有当时或此前价格；未按今日成分股筛选。',
        '## 主要结果',
        '统一比较期为2023-03-20至2026-09-17。首次入场日统一为最早PCF后第五个交易日。收益统一以2023-03-17收盘为初始时点：策略该时点持现金，2023-03-20开盘建仓；全收益基准从2023-03-17收盘开始持有。官方H00905只提供收盘点位，因此没有虚构开盘价或用价格指数开盘涨跌替代。以下年化按252个交易日计算。所有回报均为相对中证500全收益指数H00905的几何超额：累计超额=(1+组合累计收益)/(1+指数累计收益)-1；年化超额=(1+累计超额)^(252/交易日数)-1。最大回撤也基于该相对净值。不是组合收益减指数收益的百分点差。组合与ETF使用含分红复权收益，基准使用含分红再投资的全收益指数；不再把价格指数遗漏分红产生的差额当作超额。全收益指数为税前分红再投资口径，本回测没有模拟个人投资者分红税和实际到账后的再投资延迟。',
        table('full',keys),
        '## 分年与近期超额收益', '\n'.join(annual),
        '2023与2026均为部分年度；2026H2是2026年内部子区间，不可与全年收益相加。每段收益来自连续运行账户，没有年初重置仓位。',
        '## 统一费用口径',
        f'主方案年化买卖双边合计成交额约为净资产的{mainrow.annual_two_way_turnover:.2f}倍。单边10bp代表每次买入和卖出均按成交额收取0.1%的统一手续费，不额外叠加佣金、税费或滑点费用模型；这是一致的研究假设，不是历史实际费率声明。费用逐笔扣现金、复利反映。ETF对照初始买入也收10bp，期末均按市值计价，不额外假设卖出。',
        '## 回测规则与时间约束',
        '1. 仅使用各历史交易日自己的PCF。T日PCF用T之前最近可得的不复权收盘价乘篮子数量，再除PCF所载前一交易日每申赎单位净资产，得到权重。数量为0即目标仓位为0，不根据后来持仓补填。现金替代股票只要数量为正，也按股票估值并尝试买入；这属于股票穿透跟随，不是在实际办理ETF申赎。',
        '2. 股票权重总和超过98%时统一缩放到98%，否则保留原比例；余额为不计息现金。因此这是带2%最低现金预算的PCF近似复制。清单数量取整、现金替代及实际持仓差异会造成复制误差。',
        '3. T日清单分别在T+1、T+3、T+5交易日开盘成交；没有使用T日收盘价生成目标后又按T日开盘成交。延迟方案沿用T日计算的权重，不用未来价格重算信号。每次下单先按成交时市值计算账户资产，先卖后买，现金不足按比例缩减买入。',
        '4. 每日按最新可用指令调整。没有新的清单则持有原股份，权重随价格漂移。未成交部分不能假设已经买到或卖出。采用停牌及开盘涨跌停近似限制；日成交额为0只用作全天停牌的执行可用性标记，不作为选股特征。',
        '5. 收益使用复权行情处理公司行动的价格影响；仓位权重使用不复权历史价格，绝不以当前前复权绝对价格乘历史股数。当前复权快照只服务收益核算，不用于模型训练或历史估值信号。缺失报价持仓按最后可用价估值，不能把缺价资产凭空清零。',
        '6. 按用户新口径，中证500全收益指数为统一基准；510500复权市场价格买入持有作为可投资对照，同样报告相对指数的超额。563030对照也是复权市场价格收益，不是基金净值收益；包含折溢价变化影响。两只ETF均初始投入98%，随后持有，不每日再平衡。',
        '## 防未来信息检查及无法消除的限制',
        '所有执行审计行均满足：用于估值的价格日期 < PCF日期 < 交易日期。时间截断测试通过：去掉截断日之后全部PCF和行情，之前的目标仓位、净值完全不变。3项独立测试通过：未来价格修改不改变过去权重、缺失股票不把资金分配给其他股票、信号不能赚取发布前的跳空收益。',
        '**没有历史首次发布时间和修订版本证明。** 现在从官网取回的历史档案，不足以证明当年某分钟就能拿到完全相同的版本。延迟1/3/5日降低发布时间不确定性，但无法修复后来修订数据。因此不能声称本研究已经完全排除数据版本层面的未来信息。原始响应保留抓取时间，historical_publication_timestamp_verified=false。',
        '563030来自事后观察到的较好表现，存在基金选择偏差；不能据此证明事前挑基金策略有效。仅测试这一只基金，没有调参选择收益最高的延迟或费率。股票日线执行使用可分割份额，未模拟100股整手、逐笔流动性、排队成交与冲击成本，不能视作小资金实盘可完全复制的收益。日频涨跌停近似也不能替代真实委托回放。',
        '## 研究判断',
        '按披露篮子跟随在本样本中保留了一部分增强收益，但扣除成本后弱于直接持有563030。延迟到3或5日没有让收益完全消失，说明其信息不全是隔夜即失效的短线信号；这不证明存在可交易的独立预测能力，因为持续持仓和风格暴露也能解释相似走势。超额收益为正仅表示跑赢基准，不代表组合绝对盈利。',
        '更值得进一步验证的是多个管理人PCF的持续超配、增减配是否能改善已有模型，而不是默认逐日照抄优于买基金。本次不扩展到未经测试的其他基金，也未启动自动采集任务。',
        '## 文件与来源',
        '- 全收益指数：中证指数官网 https://www.csindex.com.cn/csindex-home/perf/index-perf ，indexCode=H00905、startDate=20230301、endDate=20260917；原始响应H00905_csindex_raw.json，校验864个交易日无缺口、代码一致、收盘价正值。身份依据：https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000905factsheet.pdf 。',
        '- 官方基金页：https://www.efunds.com.cn/fund/563030.shtml',
        '- 官方公开接口：https://api.efunds.com.cn/xcowch/front/etffund/baseinfo 与 stocklist，参数fundCode、tDate、listType=1。每个日期校验返回日期、证券日期、记录数、代码唯一性。',
        '- 行情：已有本地TickFlow标准化日线，加公开接口 https://free-api.tickflow.org/v1/klines 补充历史篮子股票及两只ETF。',
        '- 原始PCF及冻结方案：data_expansion/public_fund_holdings/pcf_history_563030/。',
        '- 结果：本报告同目录results/，含summary.json、daily_returns.parquet、pcf_date_targets.parquet、execution_audit.parquet、coverage.parquet、verification.json。',
        '- 复现：依次运行pcf_research.py、pcf_prices.py、fetch_csi500_total_return.py、pcf_backtest.py、pcf_report.py；test_pcf_backtest.py提供时间约束测试。',
    ]
    (RESULT.parent/'PCF披露后跟随回测.md').write_text('\n\n'.join(lines)+'\n',encoding='utf-8')
    plt.rcParams['font.sans-serif']=['Microsoft YaHei','SimHei','DejaVu Sans'];plt.rcParams['axes.unicode_minus']=False
    fig,axs=plt.subplots(2,1,figsize=(12,8),sharex=True,gridspec_kw={'height_ratios':[2,1]})
    for k in ['pcf_lag1_cost10bp','pcf_lag5_cost10bp','etf563030_buyhold','etf510500_buyhold']:
        nav=(1+ret[k]).cumprod()/(1+ret['csi500_total_return_index']).cumprod();axs[0].plot(nav.index,nav,label=names[k],lw=1.5)
        axs[1].plot(nav.index,(nav/nav.cummax().clip(lower=1)-1)*100,lw=1.1)
    axs[0].set_title('563030历史PCF延迟跟随：扣费超额净值（2023-03-20至2026-09-17）')
    axs[0].set_ylabel('超额净值（相对基准）');axs[1].set_ylabel('超额净值回撤（%）');axs[0].legend(loc='upper left',fontsize=9)
    for ax in axs:ax.grid(alpha=.2)
    fig.text(.5,.015,'基准：中证500全收益H00905；单边手续费0.1%；历史PCF首次披露时间未认证。',ha='center',fontsize=9)
    fig.tight_layout(rect=[0,.04,1,1]);fig.savefig(RESULT/'nav_comparison.png',dpi=150);plt.close(fig)
    # Hashes bind the result to the actual archived files used for research.
    records=[{'file':str(p.relative_to(OUT)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted((OUT/'raw').glob('*.gz'))]
    (RESULT/'raw_manifest.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    print(table('full',keys))
    print('REPORT',RESULT.parent/'PCF披露后跟随回测.md')

if __name__=='__main__':main()
