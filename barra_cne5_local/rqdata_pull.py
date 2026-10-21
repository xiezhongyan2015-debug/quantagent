"""Pull the RQData (米筐) risk model v1 (= Barra CNE5 style factors) for the CSI500 universe and archive it locally.

Free trial account needed (register at https://www.ricequant.com/welcome/trial/rqdata-cloud with your school email;
account creation must be done by a person, this script only pulls).  Trial quota is ~1 GB transfer per day, so the
pull is chunked by month and resumable: rerun the same command tomorrow and it continues where it stopped.

Usage (from project root, venv):
    .pcf-research-venv/bin/pip install rqdatac
    RQ_USER=<手机号或邮箱> RQ_PASS=<密码> .pcf-research-venv/bin/python barra_cne5_local/rqdata_pull.py --start 2019-01-01 --end 2026-10-15

Outputs under outputs/barra_rqdata/:
    exposure/YYYY-MM.parquet   daily factor exposures (10 styles + comovement + 申万一级 industries), universe = union of
                               CSI500 members over the project (gru_research/artifacts/dataset/symbols.json) ∪ CSI500 index
    factor_return.parquet      daily factor returns (implicit, whole market)
    cov/YYYY-MM-DD.parquet     factor covariance (daily horizon), one per month-end
    specific_risk/YYYY-MM.parquet
    quota.json                 last quota reading
Never prints credentials.
"""
import argparse, json, os, sys, time
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/barra_rqdata'
STYLES = ['size', 'beta', 'momentum', 'residual_volatility', 'non_linear_size', 'book_to_price', 'liquidity', 'earnings_yield', 'growth', 'leverage', 'comovement']


def to_rq(sym):  # 600000.SH -> 600000.XSHG
    code, ex = sym.split('.'); return code + ('.XSHG' if ex == 'SH' else '.XSHE')


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--start', default='2019-01-01'); ap.add_argument('--end', default=pd.Timestamp.today().strftime('%Y-%m-%d')); ap.add_argument('--model', default='v1')
    a = ap.parse_args()
    import rqdatac as rq
    user, pw = os.environ.get('RQ_USER'), os.environ.get('RQ_PASS')
    if not (user and pw): sys.exit('set RQ_USER and RQ_PASS environment variables (never put them in files)')
    rq.init(user, pw)
    (OUT/'exposure').mkdir(parents=True, exist_ok=True); (OUT/'cov').mkdir(exist_ok=True); (OUT/'specific_risk').mkdir(exist_ok=True)
    q = rq.user.get_quota(); json.dump(q, open(OUT/'quota.json', 'w')); print('quota', q, flush=True)
    syms = json.load(open(ROOT/'gru_research/artifacts/dataset/symbols.json'))
    ids = sorted({to_rq(s) for s in syms})
    try:
        for d in pd.date_range(a.start, a.end, freq='6M'):
            comp = rq.index_components('000905.XSHG', date=d.strftime('%Y-%m-%d')) or []
            ids = sorted(set(ids) | set(comp))
    except Exception as e: print('index_components skipped:', type(e).__name__, flush=True)
    print('universe', len(ids), flush=True)
    months = pd.period_range(a.start, a.end, freq='M')
    for m in months:
        f = OUT/'exposure'/f'{m}.parquet'
        if f.exists(): continue
        s, e = max(pd.Timestamp(a.start), m.start_time).strftime('%Y-%m-%d'), min(pd.Timestamp(a.end), m.end_time).strftime('%Y-%m-%d')
        t0 = time.time()
        x = rq.get_factor_exposure(ids, s, e, factors=None, industry_mapping='sws_2021', model=a.model)
        if x is None or len(x) == 0: print(m, 'no data', flush=True); continue
        x.reset_index().to_parquet(f, index=False)
        sr = rq.get_specific_risk(ids, s, e, horizon='daily', model=a.model)
        if sr is not None and len(sr): sr.reset_index().to_parquet(OUT/'specific_risk'/f'{m}.parquet', index=False)
        me = x.reset_index()['date'].max(); cf = OUT/'cov'/f'{pd.Timestamp(me).strftime("%Y-%m-%d")}.parquet'
        if not cf.exists():
            c = rq.get_factor_covariance(pd.Timestamp(me).strftime('%Y-%m-%d'), horizon='daily', model=a.model)
            if c is not None: c.to_parquet(cf)
        q = rq.user.get_quota(); json.dump(q, open(OUT/'quota.json', 'w'))
        print(m, 'rows', len(x), round(time.time() - t0, 1), 's; quota used', q.get('bytes_used'), '/', q.get('bytes_limit'), flush=True)
        if q.get('bytes_limit') and q.get('bytes_used', 0) > 0.95 * q['bytes_limit']: print('daily quota nearly exhausted; rerun tomorrow', flush=True); break
    fr = rq.get_factor_return(a.start, a.end, universe='whole_market', method='implicit', model=a.model)
    if fr is not None and len(fr): fr.reset_index().to_parquet(OUT/'factor_return.parquet', index=False); print('factor returns', fr.shape, flush=True)


if __name__ == '__main__':
    main()
