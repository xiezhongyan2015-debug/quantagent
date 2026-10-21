"""Independent fund NAV audit, using the API called by the official fund page."""
import json
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from .data import ROOT, OUT, write, sha
from .replay import relative_windows

SOURCE='https://www.efunds.com.cn/Mobile/fund/563030.shtml'

def main():
    path=OUT/'fund_nav_official/official_history_response.json'
    obj=json.loads(path.read_text());assert obj['status']==1
    records=obj['data']['data'];assert len(records)==obj['data']['total']
    nav=pd.DataFrame(records)[['navDate','netValue','totalNetValue','growthRate']]
    nav.columns=['date','unit_nav','accumulated_nav','published_growth_percent']
    nav=nav.sort_values('date').drop_duplicates('date').set_index('date')
    nav=nav.apply(pd.to_numeric,errors='raise')
    assert (nav.unit_nav>0).all()
    # Cumulative dividends are not a reinvested wealth index. Reinvest each
    # change in accumulated-minus-unit NAV instead of dividing accumulated NAV.
    distribution=(nav.accumulated_nav-nav.unit_nav).diff().fillna(0.)
    assert (distribution>=-1e-7).all()
    ret=(nav.unit_nav+distribution)/nav.unit_nav.shift()-1
    nav['reinvested_wealth']=(1+ret.fillna(0.)).cumprod()
    nav.to_csv(OUT/'fund_nav_official/independent_daily_nav.csv')
    meta=pd.read_csv(OUT/'pcf_metadata.csv',dtype={'date':str,'valuation_date':str})
    paired=meta.merge(nav.reset_index(),left_on='valuation_date',right_on='date',suffixes=('_pcf','_nav'),validate='many_to_one')
    paired['unit_nav_difference']=paired.nav-paired.unit_nav
    paired.to_csv(OUT/'fund_nav_official/pcf_nav_crosscheck.csv',index=False)
    p0=pd.read_csv(OUT/'P0_daily.csv').set_index('date');p1=pd.read_csv(OUT/'P1_daily.csv').set_index('date')
    assert set(p0.index)<=set(nav.index), 'Independent fund NAV must cover every replay date'
    assert p0.index.equals(p1.index)
    wealth=nav.loc[p0.index,'reinvested_wealth'].to_numpy();wealth/=wealth[0]
    b=p0.H00905_nav.to_numpy();b/=b[0];pb=p0.price_index_nav.to_numpy();pb/=pb[0]
    p=p0.nav.to_numpy();p/=p[0];q=p1.nav.to_numpy();q/=q[0];n=len(p)-1
    frame=pd.DataFrame({'date':p0.index,'fund_NAV_reinvested':wealth,'P0':p,'P1':q,'H00905':b,'price_index':pb})
    frame.to_csv(OUT/'fund_nav_official/matched_wealth.csv',index=False)
    window=relative_windows(wealth,b)
    summary=dict(source=SOURCE,response_sha256=sha(path),retrieved_at=datetime.now(timezone.utc).isoformat(),
        independent_nav_rows=len(nav),pcf_nav_pairs=len(paired),
        max_absolute_pcf_nav_difference=float(paired.unit_nav_difference.abs().max()),
        pcf_nav_disagreements_over_00005=int((paired.unit_nav_difference.abs()>.00005+1e-10).sum()),
        historical_publication_times_verified=False,
        matched_days=len(p0),start=p0.index[0],end=p0.index[-1],
        fund_return=float(wealth[-1]-1),H00905_return=float(b[-1]-1),price_index_return=float(pb[-1]-1),
        fund_annualized_relative_H00905=float((wealth[-1]/b[-1])**(252/n)-1),
        fund_arithmetic_H00905_excess=float(wealth[-1]-b[-1]),
        fund_w50_p10=float(np.quantile(window,.1)),fund_w50_mean=float(window.mean()),
        P0_annualized_relative_to_fund=float((p[-1]/wealth[-1])**(252/n)-1),
        P1_annualized_relative_to_fund=float((q[-1]/wealth[-1])**(252/n)-1),
        fund_minus_P0_cumulative_return_points=float(wealth[-1]-p[-1]),
        explanation='Fund minus P0 combines disclosed basket lag, transaction costs, portfolio replication differences and settlement approximations; it is not a causal estimate of delay alone.')
    write(OUT/'fund_nav_official/summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
