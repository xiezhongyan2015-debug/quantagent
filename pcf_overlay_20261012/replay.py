"""Four matched diagnostic cash accounts. No strategy or broker activation."""
from pathlib import Path
import importlib.util
import json
import numpy as np
import pandas as pd
from .data import ROOT, OUT, END, symbol, write, sha
from gru_beta_neutral_20261008.replay import neutralize

DATA=ROOT/'gru_research/artifacts/dataset'
CAPITAL=2_000_000.
FEE=.0002
PROTOCOL={
    'frozen_parameters':{'capital':CAPITAL,'fee_per_side_all_in':FEE,'k':.5,'clip_z':[-2,2],
                         'ema_alpha':.2,'max_stock_NAV_weight':None,'max_invested':.98},
    'dates':['2023-03-13',END],
    'signal':'five frozen walk-forward seeds, y4 head; per-seed cross-sectional z, mean, existing past-risk neutralization, EMA .2, z again',
    'weights':'PCF quantity times PCF valuation-date unadjusted close / NAV per creation unit; cap total stock allocation at 98%; keep PCF cash sleeve',
    'cash_substitution':'retain positive-quantity optional substitutions (1/3); mandatory cash (2/4) never bought; missing signal keeps neutral alpha',
    'P0':'PCF dated T, target fixed at T close using PCF prior-close weights, executed next session open',
    'P1':'same PCF and clock as P0, T 15:00 signal, next-session open',
    'P2':'same T PCF as P0/P1; next session 14:00 signal, 14:01–14:05 mean raw open; no daily-open substitution for absent minute tape',
    'S':'prior-session CSI500 market-cap skeleton with same 15:00 signal and next-session open; continuous-account rerun',
    'initial_build':'excluded from main metrics by rebasing every account at its first close, full path also retained',
    'adoption_gate':{'annualized_P1_vs_P0_relative_wealth':.01,'P1_vs_P0_50day_H00905_excess_p10_min_delta':-.003},
    'no_grid_search':True,'bootstrap':{'block_days':20,'replicates':2000,'seed':20261011},
    'status':'historical_diagnostic_not_live_certification',
    'limits':['Historical PCF publication times and revisions not certified.',
              'Corporate actions use adjusted fractional units; not raw-share cash-dividend settlement or round-lot execution.',
              'Tencent additions lack historical ST flags. No daily price-limit queue or capacity model.',
              'P2 may lack minute execution tape outside the frozen index universe; rejected trades and cash are disclosed.',
              '200万元 is a research account assumption, not proven live subaccount configuration.',
              'Training-only frozen upstream factor selection and previously exposed historical tests remain disclosed.']}

def preregister():
    path=OUT/'preregistration.json'
    if path.exists():assert json.loads(path.read_text())==PROTOCOL,'Frozen protocol changed'
    else:write(path,PROTOCOL)

def zscore(s):
    s=np.asarray(s,float);ok=np.isfinite(s);out=np.full_like(s,np.nan)
    if ok.sum() and s[ok].std()>1e-12:out[ok]=(s[ok]-s[ok].mean())/s[ok].std()
    return out

def signals(slot, dates, symbols):
    times=np.load(DATA/'times.npy');clock=pd.to_datetime(times,utc=True).tz_convert('Asia/Shanghai')
    members=np.load(DATA/'members.npy',mmap_mode='r')
    model_symbols=json.loads((DATA/'symbols.json').read_text());dest={s:i for i,s in enumerate(symbols)}
    mapping=np.array([dest[s] for s in model_symbols]);q=np.load(ROOT/'outputs/gru_beta_neutral_20261008/risk_context.npz')
    score_by_date={};source_hashes=[];last=None
    for year in range(2023,2027):
        seed_scores=[];ref=None
        for seed in [11,22,33,44,55]:
            path=ROOT/f'outputs/gru_walkforward_20261010/T{year}/control_seed{seed}/test_scores.npz'
            a=np.load(path);ix=a['indices'];assert np.array_equal(a['times'],times[ix])
            keep=clock[ix].hour==slot;ix=ix[keep]
            if ref is None:ref=ix
            else:assert np.array_equal(ref,ix)
            s=a['scores'][keep,:,3].astype(float)
            seed_scores.append(np.asarray([zscore(row) for row in s]))
            source_hashes.append(dict(path=str(path.relative_to(ROOT)),sha256=sha(path)))
        raw=np.mean(seed_scores,axis=0)
        for row,t in zip(raw,ref):
            day=str(clock[t].date())
            expo=np.stack([q['volatility'][t],q['turnover'][t],q['log_cap'][t],q['beta60'][t]],axis=1)
            row=np.where(members[t],row,np.nan)
            row=neutralize(row,expo)
            if last is not None:row=np.where(np.isfinite(row)&np.isfinite(last),.2*row+.8*last,row)
            last=row.copy();row=zscore(np.where(members[t],row,np.nan))
            full=np.full(len(symbols),np.nan);full[mapping]=row;score_by_date[day]=full
    return score_by_date,source_hashes

def target_weights(reference,score,k):
    # Scores are left-joined. Missing alpha never removes a PCF stock.
    factor=np.where(np.isfinite(score),1+k*np.clip(score,-2,2),1.)
    w=reference*factor;total=reference.sum()
    return w/w.sum()*total if w.sum()>0 else w

def account(weights,signal_marks,exec_prices,fill,dates,close,member):
    """Commit quantities first; only then inspect fills. Carry marks only for NAV."""
    U=weights.shape[1];units=np.zeros(U);marks=np.zeros(U);cash=CAPITAL
    rows=[];trades=[]
    for t,day in enumerate(dates):
        sig=signal_marks[t];valid=np.isfinite(sig)&(sig>0);marks[valid]=sig[valid]
        nav=cash+units@marks
        desired=units.copy() if not np.isfinite(weights[t]).all() else np.divide(weights[t]*nav,marks,out=np.zeros(U),where=marks>0)
        desired[~valid]=units[~valid]  # A carried valuation cannot create a trade.
        delta=desired-units;delta[np.abs(delta)*marks<100]=0
        px=exec_prices[t];can=fill[t]&np.isfinite(px)&(px>0)
        # Invalid future prices cannot choose or replace a target security.
        px=np.where(can,px,marks);sell=np.where(can,np.maximum(-delta,0),0)
        proceeds=sell*px;fees=proceeds*FEE;cash+=float((proceeds-fees).sum());units-=sell
        buy=np.where(can,np.maximum(delta,0),0);cost=float((buy*px).sum()*(1+FEE))
        if cost>cash:buy*=max(cash,0)/cost
        paid=buy*px;cash-=float(paid.sum()*(1+FEE));units+=buy
        assert cash>=-1e-6 and (units>=-1e-10).all()
        for u in np.flatnonzero(buy+sell>1e-10):
            trades.append(dict(date=day,stock_index=int(u),requested_adjusted_units=float(delta[u]),
                               buy_adjusted_units=float(buy[u]),sell_adjusted_units=float(sell[u]),
                               adjusted_price=float(px[u]),fee=float((paid[u]+proceeds[u])*FEE)))
        observed=np.isfinite(close[t])&(close[t]>0);marks[observed]=close[t,observed]
        equity=units*marks;nav=cash+equity.sum()
        rows.append(dict(date=day,nav=nav/CAPITAL,cash_weight=cash/nav,
                         fee_cny=float((paid.sum()+proceeds.sum())*FEE),
                         turnover=float((paid.sum()+proceeds.sum())/nav),
                         fill_blocked_weight=float((np.abs(delta)*signal_marks[t]*~can)[np.isfinite(signal_marks[t])].sum()/nav),
                         stale_weight=float(equity[~observed].sum()/nav),
                         member_nav_weight=float(equity[member[t]].sum()/nav),
                         target_member_nav_weight=float(np.nansum(weights[t,member[t]])),
                         max_stock_nav_weight=float(equity.max()/nav),holdings=int((units>1e-10).sum())))
    return pd.DataFrame(rows),pd.DataFrame(trades)

def relative_windows(nav,bench,n=50):
    wealth=np.asarray(nav)/np.asarray(bench)
    return wealth[n:]/wealth[:-n]-1

def summary(d,bench,pricebench):
    nav=d.nav.to_numpy();r=nav[1:]/nav[:-1]-1;b=bench[1:]/bench[:-1]-1
    nav=nav/nav[0];bench=bench/bench[0];pricebench=pricebench/pricebench[0];active=r-b
    window=relative_windows(nav,bench)
    rel=nav/bench;dd=rel/np.maximum.accumulate(rel)-1
    return dict(days=len(r),net_return=float(nav[-1]-1),H00905_return=float(bench[-1]-1),
                price_index_return=float(pricebench[-1]-1),arithmetic_excess=float(nav[-1]-bench[-1]),
                relative_wealth_excess=float(rel[-1]-1),price_index_excess=float(nav[-1]-pricebench[-1]),
                annualized_relative_excess=float(rel[-1]**(252/len(r))-1),
                annualized_net_return=float(nav[-1]**(252/len(r))-1),
                tracking_error=float(active.std(ddof=1)*np.sqrt(252)),
                information_ratio=float(active.mean()/active.std(ddof=1)*np.sqrt(252)),
                excess_max_drawdown=float(dd.min()),average_cash=float(d.cash_weight.iloc[1:].mean()),
                average_turnover=float(d.turnover.iloc[1:].mean()),fees=float(d.fee_cny.iloc[1:].sum()),
                fees_including_build=float(d.fee_cny.sum()),max_stale_weight=float(d.stale_weight.max()),
                member80_violation_days_after_three=int((d.member_nav_weight.iloc[3:]<=.8).sum()),
                max_fill_blocked_weight=float(d.fill_blocked_weight.max()),
                w50_mean=float(window.mean()),w50_p10=float(np.quantile(window,.1)),
                w50_p50=float(np.median(window)),w50_p90=float(np.quantile(window,.9)),
                w50_worst=float(window.min()),w50_win_rate=float((window>0).mean()))

def main():
    preregister()
    pc=pd.read_parquet(OUT/'pcf_components.parquet');meta=pd.read_csv(OUT/'pcf_metadata.csv',dtype={'date':str,'valuation_date':str}).set_index('date')
    prices=pd.read_parquet(OUT/'daily_prices.parquet');symbols=json.loads((OUT/'price_symbols.json').read_text());U=len(symbols)
    historic=pd.read_parquet(ROOT/'outputs/cluster_sync_20261011/teammate_latest_uploads/unpacked/pcf_alpha_materials_20261011/benchmarks/H00905_daily.parquet')[['date','close']]
    recent=pd.read_csv(ROOT/'outputs/gru_beta_neutral_20261008/benchmark_sources/H00905_daily.csv')[['date','close']]
    historic['date']=pd.to_datetime(historic.date).dt.strftime('%Y-%m-%d')
    recent['date']=pd.to_datetime(recent.date).dt.strftime('%Y-%m-%d')
    benchmark=pd.concat([historic,recent]).drop_duplicates('date',keep='last').set_index('date').close.astype(float)
    pricebench=pd.read_csv(ROOT/'outputs/gru_beta_neutral_20261008/benchmark_sources/000905_daily.csv').set_index('date').close
    calendar=sorted(set(benchmark.index)&set(pricebench.index)&set(prices.date))
    all_days=[d for d in calendar if '2023-03-01'<=d<=END]
    required=meta.loc[(meta.index>='2023-03-13')&(meta.index<=END),'valuation_date']
    assert set(required)<=set(all_days), 'PCF valuation dates absent from merged benchmark/price calendar'
    def wide(field):return prices.pivot(index='date',columns='symbol',values=field).reindex(index=all_days,columns=symbols).to_numpy()
    rawclose=wide('close');op=wide('open_adjusted');close=wide('close_adjusted');factor=wide('factor')
    volume=wide('volume');valid=wide('valid')==1;isST=wide('isST')
    # Raw source status, never membership or future score/label completeness.
    can=valid&(volume>0)&(isST!=1)
    lookup={s:i for i,s in enumerate(symbols)};daypos={d:i for i,d in enumerate(all_days)}
    s15,h15=signals(15,all_days,symbols);s14,h14=signals(14,all_days,symbols)
    times=np.load(DATA/'times.npy');clock=pd.to_datetime(times,utc=True).tz_convert('Asia/Shanghai')
    model_symbols=json.loads((DATA/'symbols.json').read_text());mapping=np.array([lookup[s] for s in model_symbols])
    frozen_member=np.load(DATA/'members.npy',mmap_mode='r');risk=np.load(ROOT/'outputs/gru_beta_neutral_20261008/risk_context.npz')
    execution=np.load(DATA/'execution_raw_prices.npy',mmap_mode='r');efill=np.load(DATA/'execution_complete.npy',mmap_mode='r')&np.load(DATA/'execution_tradable.npy',mmap_mode='r')&~np.load(DATA/'execution_all_flat.npy',mmap_mode='r')
    hours=np.load(DATA/'hour_ohlcv.npy',mmap_mode='r');by15={str(clock[i].date()):i for i in np.flatnonzero(clock.hour==15)};by14={str(clock[i].date()):i for i in np.flatnonzero(clock.hour==14)}
    dates=[];refs=[];srefs=[];a15=[];a14=[];signal_marks=[];p2marks=[];fills=[];p2fills=[];members=[];execution_prices=[];p2prices=[];closes=[];coverage=[]
    pcgroups={d:g[g.stock_replicable] for d,g in pc.groupby('date')}
    for j,day in enumerate(all_days):
        if j==0:continue
        prior=all_days[j-1]
        if prior not in pcgroups or prior not in s15 or day not in s14:continue
        if prior<'2023-03-13':continue
        g=pcgroups[prior];m=meta.loc[prior];vday=m.valuation_date
        if vday not in daypos:raise ValueError('PCF valuation date missing from raw-price calendar')
        ids=np.array([lookup[symbol(c)] for c in g.code]);prev=rawclose[daypos[vday],ids]
        missing=~np.isfinite(prev)|(prev<=0)
        reference=np.zeros(U)
        values=g.quantity.to_numpy()*prev/float(m.nav_per_cu)
        if missing.any(): reference[:]=np.nan  # hold, never renormalize a damaged basket.
        else:
            reference[ids]=values
            if reference.sum()>.98:reference*=.98/reference.sum()
        ti=by15[prior];cur=by15[day];member=np.zeros(U,bool);member[mapping]=frozen_member[cur]
        base=np.where(frozen_member[ti],np.exp(risk['log_cap'][ti]-np.nanmax(risk['log_cap'][ti])),0.)
        base=np.nan_to_num(base);base=base/base.sum()*.98;sr=np.zeros(U);sr[mapping]=base
        c14=by14[day];p14=np.full(U,np.nan);p14[mapping]=execution[c14]*factor[j,mapping]
        f14=np.zeros(U,bool);f14[mapping]=efill[c14];f14&=np.isfinite(p14)&(p14>0)
        mark14=close[j-1].copy();observed=np.isfinite(hours[c14,:,8]);mark14[mapping[observed]]=hours[c14,observed,8]*factor[j,mapping[observed]]
        dates.append(day);refs.append(reference);srefs.append(sr);a15.append(s15[prior]);a14.append(s14[day])
        signal_marks.append(close[j-1]);p2marks.append(mark14);fills.append(can[j]);p2fills.append(f14)
        execution_prices.append(op[j]);p2prices.append(p14);closes.append(close[j]);members.append(member)
        coverage.append(dict(execution_date=day,pcf_date=prior,pcf_source=m.source,names=len(g),missing_prior_price=int(missing.sum()),
                             scored15_names=int(np.isfinite(s15[prior][ids]).sum()),scored14_names=int(np.isfinite(s14[day][ids]).sum()),
                             p2_execution_names=int(f14[ids].sum()),target_stock_weight=float(np.nansum(reference))))
    arrays=lambda x:np.asarray(x)
    refs,srefs,a15,a14=map(arrays,[refs,srefs,a15,a14]);closes=arrays(closes);members=arrays(members)
    weight={'P0':refs,'P1':np.array([target_weights(w,s,.5) for w,s in zip(refs,a15)]),
            'P2':np.array([target_weights(w,s,.5) for w,s in zip(refs,a14)]),
            'S':np.array([target_weights(w,s,.5) for w,s in zip(srefs,a15)])}
    results={};scores=[];yearly=[];b=benchmark.loc[dates].to_numpy();pb=pricebench.loc[dates].to_numpy()
    for arm in ['P0','P1','P2','S']:
        d,tr=account(weight[arm],arrays(p2marks if arm=='P2' else signal_marks),
                     arrays(p2prices if arm=='P2' else execution_prices),arrays(p2fills if arm=='P2' else fills),dates,closes,members)
        d['H00905_nav']=b/b[0];d['price_index_nav']=pb/pb[0];d['nav_after_build']=d.nav/d.nav.iloc[0]
        d.to_csv(OUT/f'{arm}_daily.csv',index=False);tr['symbol']=tr.stock_index.map(dict(enumerate(symbols)));tr.to_csv(OUT/f'{arm}_trades.csv',index=False)
        record=summary(d,b,pb);record['arm']=arm;scores.append(record);results[arm]=d
        for year in range(2023,2027):
            ids=np.flatnonzero(d.date.str.startswith(str(year)))
            start=max(int(ids[0])-1,0);stop=int(ids[-1])+1
            r=summary(d.iloc[start:stop],b[start:stop],pb[start:stop]);r.update(arm=arm,year=year);yearly.append(r)
        print('REPLAY_COMPLETE',arm,record['net_return'],flush=True)
    pd.DataFrame(scores).to_csv(OUT/'scorecard.csv',index=False);pd.DataFrame(yearly).to_csv(OUT/'yearly_scorecard.csv',index=False)
    pd.DataFrame(coverage).to_csv(OUT/'daily_input_coverage.csv',index=False)
    p0=results['P0'].nav.to_numpy();p1=results['P1'].nav.to_numpy();n=len(p0)-1
    increment=(p1[-1]/p1[0])/(p0[-1]/p0[0]);annual=increment**(252/n)-1
    w0=relative_windows(p0,b);w1=relative_windows(p1,b);p10=float(np.quantile(w1,.1)-np.quantile(w0,.1))
    r0=p0[1:]/p0[:-1]-1;r1=p1[1:]/p1[:-1]-1;diff=r1-r0
    rng=np.random.default_rng(20261011);ix=(rng.integers(0,n,size=(2000,int(np.ceil(n/20)),1))+np.arange(20))%n;ix=ix.reshape(2000,-1)[:,:n]
    boot=diff[ix].mean(axis=1)*252
    outcome=dict(status='diagnostic_complete_live_decision_deferred',annualized_P1_vs_P0=float(annual),
                 w50_p10_delta=p10,historical_gate_passes=bool(annual>=.01 and p10>=-.003),
                 block_bootstrap_mean_daily_difference_annualized95=np.quantile(boot,[.025,.975]).tolist(),
                 matched_days=len(dates),start=dates[0],end=dates[-1],missing_price_target_hold_days=sum(x['missing_prior_price']>0 for x in coverage),
                 live_activation=False,prerequisites=['current account snapshot','official competition rules','subaccount configuration','fresh daily signal data','actual lot/corporate-action verification'])
    outcome['P1_member80_violation_days_after_three']=int((results['P1'].member_nav_weight.iloc[3:]<=.8).sum())
    outcome['execution_readiness_passes']=False
    outcome['next_action']='Research candidate only; resolve member-NAV constraint, round lots and live data before shadow-to-live acceptance.'
    write(OUT/'decision.json',outcome);write(OUT/'signal_source_hashes.json',{'15':h15,'14':h14})
    print(json.dumps(outcome,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
