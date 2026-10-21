"""CPU inference and strict offline external-file handoff, never broker calls."""
from pathlib import Path
import argparse
import json
import numpy as np
import pandas as pd
import torch
from .data import ROOT, OUT, sha, write
from .replay import target_weights, zscore
from gru_dualplatform_20261011.daily import DailyGRU
from gru_beta_neutral_20261008.replay import neutralize

PANEL=ROOT/'outputs/gru_dualplatform_20261011/daily_panel'

def infer_cpu(sequence,industry,checkpoints,batch=128):
    """Each checkpoint receives the same normalized 60×98 feature history."""
    if sequence.ndim!=3 or sequence.shape[1:]!=(60,98):raise ValueError('Expected stocks×60×98')
    if industry.shape!=sequence.shape[:2]:raise ValueError('Industry axes mismatch')
    if np.isinf(sequence).any():raise ValueError('Infinite input feature')
    if np.any(industry!=0):raise ValueError('Frozen daily model only admits unknown industry ID 0')
    torch.set_num_threads(4);ensembles=[];identities=[]
    for path in checkpoints:
        ck=torch.load(path,map_location='cpu',weights_only=True)
        if 'model' not in ck or 'signature' not in ck:raise ValueError('Missing daily checkpoint identity')
        model=DailyGRU().cpu().eval();model.load_state_dict(ck['model'])
        scores=[]
        with torch.inference_mode():
            for i in range(0,len(sequence),batch):
                scores.append(model(torch.from_numpy(sequence[i:i+batch].copy()).float(),
                                    torch.from_numpy(industry[i:i+batch].copy()).long()).numpy())
        s=np.concatenate(scores);assert np.isfinite(s).all();ensembles.append(zscore(s[:,3]))
        identities.append(dict(path=str(path),sha256=sha(path),signature=ck['signature']))
    return zscore(np.mean(ensembles,axis=0)),identities

def external_targets(reference,scores,execution_date,signal_date,capital=2_000_000.,invested=.98):
    """Diagnostic share counts and console-compatible weight JSON in parallel."""
    if pd.Timestamp(signal_date)>=pd.Timestamp(execution_date):raise ValueError('Execution must be after signal date')
    required={'code','quantity','pcf_valuation_close','previous_close','nav_per_cu','member','substitution'}
    if not required.issubset(reference.columns):raise ValueError('Incomplete PCF reference data')
    if reference.code.duplicated().any():raise ValueError('Duplicate PCF code')
    df=reference.copy();df.code=df.code.astype(str).str.zfill(6)
    if not df.code.str.fullmatch(r'[036]\d{5}').all():raise ValueError('Invalid A-share stock code')
    if not np.isfinite(df[['quantity','pcf_valuation_close','previous_close','nav_per_cu']].to_numpy(float)).all():raise ValueError('Nonfinite PCF inputs')
    if (df.quantity<0).any() or (df.nav_per_cu<=0).any():raise ValueError('Invalid quantity or NAV per creation unit')
    if not df.member.isin([True,False,0,1]).all():raise ValueError('Membership must be an explicit boolean')
    if not ((pd.to_numeric(df.previous_close)>0)&np.isfinite(pd.to_numeric(df.previous_close))).all():raise ValueError('PCF stock lacks previous close')
    if (df.pcf_valuation_close<=0).any():raise ValueError('Invalid PCF valuation price')
    # PCF valuation date is T-1; execution-reference previous close is T.
    # They are distinct prices, even when adjacent returns happen to be small.
    base=df.quantity.to_numpy(float)*df.pcf_valuation_close.to_numpy(float)/df.nav_per_cu.to_numpy(float)
    allowed=(df.quantity.to_numpy(float)>0)&~df.substitution.astype(str).isin(['2','4']).to_numpy()
    base=np.where(allowed,base,0.)
    if base.sum()>invested:base*=invested/base.sum()
    # Missing model stock is neutral, including stocks outside training universe.
    alpha=np.array([scores.get(code,np.nan) for code in df.code],float)
    weight=target_weights(base,alpha,.5)
    df['alpha_z']=alpha;df['missing_alpha']=~np.isfinite(alpha);df['target_weight']=weight
    df['target_shares']=(capital*weight/df.previous_close.to_numpy(float)//100).astype(int)*100
    star=df.code.str.startswith('688')&(df.target_shares<200);df.loc[star,'target_shares']=0
    df['rounded_weight']=df.target_shares*df.previous_close/capital
    membership=(df.rounded_weight*df.member.astype(bool)).sum()
    checks=dict(signal_date=signal_date,execution_date=execution_date,capital=capital,
                target_weight=float(weight.sum()),rounded_stock_weight=float(df.rounded_weight.sum()),
                member_nav_weight=float(membership),member80_pass=bool(membership>.8),
                minimum90_pass=bool(df.rounded_weight.sum()>=.9),cash_weight=float(1-df.rounded_weight.sum()),
                missing_alpha_names=int(df.missing_alpha.sum()),max_single_name=float(df.rounded_weight.max()),
                round_lot_value_error=float((weight-df.rounded_weight).sum()*capital),
                account_snapshot_verified=False,competition_rules_verified=False,live_ready=False)
    obj=dict(strategy_id='pcf_overlay_daily_research_k05',execution_date=execution_date,
             signal_date=signal_date,mode='weights',targets=[dict(code=('SH' if r.code.startswith('6') else 'SZ')+r.code,
                 weight=float(r.target_weight)) for r in df.itertuples() if r.target_weight>0],
             research_only=True,precheck=checks)
    return df,obj,checks

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--checkpoint',action='append',required=True)
    ap.add_argument('--signal-date',required=True);ap.add_argument('--execution-date',required=True)
    ap.add_argument('--pcf-reference',required=True);ap.add_argument('--risk-exposures',required=True)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--panel',type=Path,default=PANEL)
    a=ap.parse_args();panel=a.panel
    meta=json.loads((panel/'meta.json').read_text());symbols=json.loads((panel/'symbols.json').read_text())
    times=np.load(panel/'times.npy');dates=pd.to_datetime(times,utc=True).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d')
    ix=np.flatnonzero(dates==a.signal_date)
    if len(ix)!=1:raise ValueError('Requested date unavailable: no stale-date substitution')
    t=int(ix[0]);complete=np.load(panel/'complete.npy',mmap_mode='r');member=np.load(panel/'members.npy',mmap_mode='r')
    if t<59:raise ValueError('Fewer than 60 complete sessions')
    eligible=member[t]&complete[t-59:t+1].all(axis=0);ids=np.flatnonzero(eligible)
    if len(ids)<100:raise ValueError('Too few inference-eligible stocks')
    x=np.load(panel/'X.npy',mmap_mode='r');ind=np.load(panel/'industry.npy',mmap_mode='r')
    sequence=np.asarray(x[t-59:t+1,ids]).transpose(1,0,2).copy();industry=np.asarray(ind[t-59:t+1,ids]).T.copy()
    scores,identities=infer_cpu(sequence,industry,[Path(p) for p in a.checkpoint])
    # Model selection must be earlier than the requested signal; no test-year fits.
    for identity in identities:
        signature=identity['signature']
        if signature.get('pilot'):raise ValueError('Pilot is not a production research signal')
        if signature['panel']!=meta['data_fingerprint']:raise ValueError('Feature schema/panel lineage changed without acceptance')
        if signature['year']!=pd.Timestamp(a.signal_date).year:raise ValueError('Checkpoint/window year mismatch')
    risk=pd.read_csv(a.risk_exposures,dtype={'code':str})
    if not (risk.signal_date==a.signal_date).all():raise ValueError('Stale risk exposures')
    cols=['volatility','turnover','log_cap','beta60'];risk.code=risk.code.str.zfill(6)
    exposure=risk.set_index('code')[cols].reindex([symbols[i][:6] for i in ids]).to_numpy(float)
    scores=zscore(neutralize(scores,exposure))
    if np.isfinite(scores).sum()<100:raise ValueError('Insufficient finite neutralized signals')
    scoremap={symbols[i][:6]:s for i,s in zip(ids,scores)}
    reference=pd.read_csv(a.pcf_reference,dtype={'code':str,'substitution':str})
    if 'signal_date' not in reference or not (reference.signal_date==a.signal_date).all():raise ValueError('Stale PCF reference')
    df,obj,checks=external_targets(reference,scoremap,a.execution_date,a.signal_date)
    a.output.mkdir(parents=True,exist_ok=True)
    df.to_csv(a.output/f'target_{a.execution_date}.csv',index=False)
    write(a.output/f'target_{a.execution_date}.json',obj)
    write(a.output/'inference_receipt.json',dict(status='offline_research_signal_only',checkpoints=identities,
          signal_date=a.signal_date,eligible_stocks=len(ids),panel_fingerprint=meta['data_fingerprint'],precheck=checks))

if __name__=='__main__':main()
