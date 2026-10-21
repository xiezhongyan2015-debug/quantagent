"""CSI500 futures annualized basis (导师口径的"期货年化贴水") from public Sina daily klines of IC monthly contracts and
the Tencent sh000905 daily index. Expiry = third Friday of the contract month (CFFEX rule; holiday postponements ignored).
annualized basis = (F/S - 1) * 365 / calendar days to expiry.  'next-month' = nearest contract with >= 7 days left;
'next-quarter' = nearest quarter-month contract with 30..120 days left.  Output: outputs/mentor_review_20261017/basis_*.csv"""
import json, re, glob, os
import numpy as np, pandas as pd
RAW = 'outputs/mentor_review_20261017/public_market_raw'; OUT = 'outputs/mentor_review_20261017'
rows = []
for f in sorted(glob.glob(f'{RAW}/idx_*.json')):
    for r in json.load(open(f))['data']['sh000905']['day']: rows.append((r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
idx = pd.DataFrame(rows, columns=['date', 'open', 'close', 'high', 'low', 'volume']).drop_duplicates('date').sort_values('date'); idx['date'] = pd.to_datetime(idx.date)
idx.to_csv(f'{OUT}/csi500_price_index_daily_2009_2026.csv', index=False)
def third_friday(y, m):
    d = pd.Timestamp(year=y, month=m, day=1); fr = d + pd.Timedelta(days=(4 - d.weekday()) % 7); return fr + pd.Timedelta(days=14)
fut = []
for f in sorted(glob.glob(f'{RAW}/IC2*.js')):
    sym = os.path.basename(f)[:-3]; y, m = 2000 + int(sym[2:4]), int(sym[4:6]); txt = open(f, encoding='utf-8', errors='replace').read()
    mm = re.search(r'\((\[.*\])\)', txt, re.S)
    if not mm: continue
    for r in json.loads(mm.group(1)): fut.append((sym, pd.Timestamp(r['d']), float(r['c']), float(r['v']), third_friday(y, m), m in (3, 6, 9, 12)))
fut = pd.DataFrame(fut, columns=['contract', 'date', 'close', 'vol', 'expiry', 'quarterly']); fut = fut.merge(idx[['date', 'close']].rename(columns={'close': 'spot'}), on='date')
fut['days'] = (fut.expiry - fut.date).dt.days; fut = fut[fut.days > 0]; fut['ann_basis'] = (fut.close / fut.spot - 1) * 365 / fut.days; fut['basis_pct'] = fut.close / fut.spot - 1
nm = fut[fut.days >= 7].sort_values('days').groupby('date').first(); nq = fut[fut.quarterly & fut.days.between(30, 120)].sort_values('days').groupby('date').first()
nm['year'] = nm.index.year; nq['year'] = nq.index.year
tab = pd.DataFrame({'next_month_median_ann_basis': nm.groupby('year').ann_basis.median(), 'next_month_p25': nm.groupby('year').ann_basis.quantile(.25), 'next_month_p75': nm.groupby('year').ann_basis.quantile(.75),
                    'next_quarter_median_ann_basis': nq.groupby('year').ann_basis.median(), 'next_quarter_p25': nq.groupby('year').ann_basis.quantile(.25), 'next_quarter_p75': nq.groupby('year').ann_basis.quantile(.75), 'days': nq.groupby('year').size()})
tab.loc['2024-2026 all'] = [nm.ann_basis.median(), nm.ann_basis.quantile(.25), nm.ann_basis.quantile(.75), nq.ann_basis.median(), nq.ann_basis.quantile(.25), nq.ann_basis.quantile(.75), len(nq)]
last = nq[nq.index >= nq.index.max() - pd.Timedelta(days=90)]; tab.loc['last 90 days'] = [np.nan, np.nan, np.nan, last.ann_basis.median(), last.ann_basis.quantile(.25), last.ann_basis.quantile(.75), len(last)]
tab.to_csv(f'{OUT}/basis_by_year.csv'); nq.to_csv(f'{OUT}/basis_next_quarter_daily.csv'); nm.to_csv(f'{OUT}/basis_next_month_daily.csv')
print(tab.round(4).to_string())
# index context: drawdowns and 55-day windows
c = idx.set_index('date').close; dd = c / c.cummax() - 1
print('\n2024 Jan-Feb drawdown: peak', c['2023-12':'2024-02'].idxmax().date(), 'trough', c['2024-01':'2024-02'].idxmin().date(), f'{c["2024-01":"2024-02"].min() / c["2023-12":"2024-02"].max() - 1:.1%}')
r55 = c.pct_change(55).shift(-55).dropna()
for a, b in [('2010', '2026'), ('2018', '2026'), ('2024-06', '2026')]:
    s = r55[a:b]; print(f'index 55-day forward return {a}-{b}: n={len(s)} mean={s.mean():.2%} median={s.median():.2%} p10={s.quantile(.1):.2%} p90={s.quantile(.9):.2%} P(<0)={(s < 0).mean():.0%}')
print('competition so far 2026-09-18 ->', c.index.max().date(), f'{c[c.index.max()] / c[pd.Timestamp("2026-09-18")] - 1:.2%}')
