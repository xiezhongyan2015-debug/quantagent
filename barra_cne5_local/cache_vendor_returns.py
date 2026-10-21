"""Cache pre-2022 daily returns and total market cap for all A shares from the vendor CSVs (GBK) in the c5 package.
Only rows 2020-01-01 .. 2022-05-31 are needed (warm-up for beta / momentum before the c5 panel starts 2022-06-01)."""
from pathlib import Path
import pandas as pd, numpy as np, time, sys
ROOT = Path(__file__).resolve().parents[1]; SRC = ROOT/'c5_core/data/vendor/stock-trading-data-2025-12-23N'; OUT = ROOT/'outputs/barra_cne5_local'
t0 = time.time(); rows = []
files = sorted(SRC.glob('*.csv'))
for i, f in enumerate(files):
    try:
        d = pd.read_csv(f, encoding='gbk', skiprows=1, usecols=[0, 2, 6, 7, 11], names=['code', 'date', 'close', 'pre_close', 'total_cap'], header=0)
    except Exception as e:
        print('skip', f.name, type(e).__name__, flush=True); continue
    d = d[(d.date >= '2020-01-01') & (d.date <= '2022-05-31')]
    if len(d): rows.append(d)
    if i % 1000 == 0: print(i, len(files), round(time.time() - t0), 's', flush=True)
a = pd.concat(rows, ignore_index=True)
a['symbol'] = a.code.str[2:] + '.' + a.code.str[:2].str.upper()
a['ret'] = a.close / a.pre_close - 1
a.loc[~np.isfinite(a.ret) | (a.pre_close <= 0), 'ret'] = np.nan
a[['symbol', 'date', 'close', 'pre_close', 'ret', 'total_cap']].to_parquet(OUT/'vendor_returns_2020_2022H1.parquet', index=False)
print('done', a.shape, a.date.min(), a.date.max(), a.symbol.nunique(), round(time.time() - t0), 's')
