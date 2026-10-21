"""Position timing on the CSI500 price index, 2010-2026 (public Tencent daily): does a simple volume / trend signal predict the
sign of the next 55 trading days (one competition horizon)?  Holding fraction f in stocks and the rest in cash gives
excess over the index of about (f-1)*R55 (stock alpha aside), so cutting 95% -> 80% only helps when R55 < 0.
Signals at t use data through t.  Output: outputs/mentor_review_20261017/position_timing_*.csv"""
import numpy as np, pandas as pd
OUT = 'outputs/mentor_review_20261017'
idx = pd.read_csv(f'{OUT}/csi500_price_index_daily_2009_2026.csv', parse_dates=['date']).set_index('date').sort_index()
c, v = idx.close, idx.volume
df = pd.DataFrame({'r55_fwd': c.shift(-55) / c - 1, 'vol20_120': v.rolling(20).mean() / v.rolling(120).mean(), 'vol20_250': v.rolling(20).mean() / v.rolling(250).mean(),
                   'ma60': c / c.rolling(60).mean() - 1, 'ma120': c / c.rolling(120).mean() - 1, 'ret20': c.pct_change(20), 'ret60': c.pct_change(60),
                   'dd250': c / c.rolling(250).max() - 1, 'rv20': np.log(c).diff().rolling(20).std() * np.sqrt(252)})
df.to_csv(f'{OUT}/position_timing_panel.csv')
rows = []
def evaluate(name, cond, start='2010-01-01'):
    d = df[start:].dropna(subset=['r55_fwd']); x = d[cond(d)]; y = d[~cond(d)]
    rows.append(dict(signal=name, start=start, n_all=len(d), n_signal=len(x), share_signal=len(x) / len(d), P_neg_all=(d.r55_fwd < 0).mean(), P_neg_signal=(x.r55_fwd < 0).mean() if len(x) else np.nan,
                     P_neg_rest=(y.r55_fwd < 0).mean() if len(y) else np.nan, mean_r55_all=d.r55_fwd.mean(), mean_r55_signal=x.r55_fwd.mean() if len(x) else np.nan, mean_r55_rest=y.r55_fwd.mean() if len(y) else np.nan,
                     cut15_excess_if_followed=(-0.15 * x.r55_fwd).mean() if len(x) else np.nan, cut15_hit=((-0.15 * x.r55_fwd) > 0).mean() if len(x) else np.nan))
for start in ['2010-01-01', '2018-01-01', '2024-06-01']:
    evaluate('volume shrink: vol20/vol120 < 0.8', lambda d: d.vol20_120 < 0.8, start)
    evaluate('volume shrink: vol20/vol250 < 0.7', lambda d: d.vol20_250 < 0.7, start)
    evaluate('below MA60', lambda d: d.ma60 < 0, start)
    evaluate('below MA120', lambda d: d.ma120 < 0, start)
    evaluate('below MA60 and volume shrink (vol20/vol120<0.8)', lambda d: (d.ma60 < 0) & (d.vol20_120 < 0.8), start)
    evaluate('ret20 < -5%', lambda d: d.ret20 < -0.05, start)
    evaluate('drawdown from 250d high < -15%', lambda d: d.dd250 < -0.15, start)
    evaluate('high vol: rv20 > 30%', lambda d: d.rv20 > 0.30, start)
tab = pd.DataFrame(rows); tab.to_csv(f'{OUT}/position_timing_rules.csv', index=False)
pd.set_option('display.width', 250); print(tab.round(3).to_string())
# rank-based: quintiles of the volume ratio -> forward 55d
d = df['2010':].dropna(subset=['r55_fwd', 'vol20_120']); q = pd.qcut(d.vol20_120, 5, labels=False)
print('\nvol20/vol120 quintile -> mean r55, P(r55<0):'); print(pd.DataFrame({'mean_r55': d.r55_fwd.groupby(q).mean(), 'P_neg': (d.r55_fwd < 0).groupby(q).mean(), 'n': d.r55_fwd.groupby(q).size()}).round(3).to_string())
# Spearman of each signal with r55 (overlapping, so t-stats are not valid; effective n ~ len/55)
print('\nSpearman with forward 55d return (2010-):'); print(d[['vol20_120', 'vol20_250', 'ma60', 'ma120', 'ret20', 'ret60', 'dd250', 'rv20']].corrwith(d.r55_fwd, method='spearman').round(3).to_string())
print('\ncurrent signal values', df.iloc[-1].drop('r55_fwd').round(3).to_dict(), 'as of', df.index[-1].date())
