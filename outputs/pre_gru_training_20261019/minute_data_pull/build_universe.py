"""Build the baostock 5-minute pull universe (all A shares traded since 2020-01-01, incl. delisted).

Sources (all local, except stock_basic.csv which was fetched from baostock once with query_stock_basic):
  - c5_core/data/A_Layer_Live/daily_source/*.parquet  (local all-A daily, SH/SZ, incl. delisted; scan -> universe_daily_source_scan.csv)
  - data/stock_basic.csv  (baostock query_stock_basic, 2026-10-19: code, ipoDate, outDate, type, status)
  - CSI500 / CSI1000 iFinD daily memberships (only used for pull priority: CSI500 members first, then CSI1000, then rest)
Output: data/universe.csv with columns code(bs format), start, end, priority, in_local_daily, in_bs_basic, out_date
"""
import glob, gzip, json, os
import pandas as pd

ROOT = '/Users/leslie/Downloads/中证500量化60min'
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
START, END = '2020-01-01', '2026-10-16'


def members(folder, since='2020-01-01'):
    s = set()
    for f in sorted(glob.glob(os.path.join(ROOT, folder, 'memberships', '*.json.gz'))):
        if os.path.basename(f)[:10] < since:
            continue
        try:
            t = json.load(gzip.open(f))['data'][0]['table']
            s.update(t.get('THSCODE') or [])
        except Exception:
            pass
    # 000006.SZ -> sz.000006
    return {f'{c[-2:].lower()}.{c[:6]}' for c in s if len(c) == 9}


scan = pd.read_csv(os.path.join(DATA, 'universe_daily_source_scan.csv'), dtype={'code': str})
scan = scan[scan.n_after2020 > 0].copy()
scan['bs'] = scan.code.str[:2] + '.' + scan.code.str[2:]
local = set(scan.bs)

sb = pd.read_csv(os.path.join(DATA, 'stock_basic.csv'), dtype=str).fillna('')
sb = sb[(sb.type == '1') & sb.code.str.match(r'^(sh|sz)\.')]
sb_keep = sb[((sb.status == '1') | (sb.outDate >= START)) & (sb.ipoDate <= END)]
codes = sorted(local | set(sb_keep.code))
info = sb.set_index('code')

csi500 = members('中证500_历年成分股_日频_2010-2026_iFinD_后复权')
csi1000 = members('中证1000_历年成分股_日频_2010-2026_iFinD_后复权')
rows = []
for c in codes:
    ipo = info.ipoDate.get(c, '') if c in info.index else ''
    out = info.outDate.get(c, '') if c in info.index else ''
    start = max(START, ipo) if ipo else START
    end = min(END, out) if out else END
    pr = 0 if c in csi500 else (1 if c in csi1000 else 2)
    rows.append(dict(code=c, start=start, end=end, priority=pr, in_local_daily=int(c in local),
                     in_bs_basic=int(c in info.index), out_date=out))
u = pd.DataFrame(rows)
u = u[u.start <= u.end].sort_values(['priority', 'code']).reset_index(drop=True)
u.to_csv(os.path.join(DATA, 'universe.csv'), index=False)
print('universe', len(u), 'by priority', u.priority.value_counts().sort_index().to_dict(),
      'only_local', int((u.in_bs_basic == 0).sum()), 'only_bs', int((u.in_local_daily == 0).sum()),
      'delisted_2020+', int((u.out_date >= START).sum()))
