import os, sys, shutil, tempfile, pandas as pd, pyarrow.parquet as pq
R = '/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/fundamental/raw'
tmp = tempfile.mkdtemp()
for f in ('stk_holdernumber', 'adj_factor'): shutil.copy(f'{R}/{f}.parquet', tmp)
sys.path.insert(0, '/Users/leslie/Downloads/中证500量化60min/c5_core/code/QuantaAgents/scripts'); sys.path.insert(0, '..')
import fd_update as fu; fu.RAW = tmp
import fd_update_reseller as r
old = pd.read_parquet(f'{tmp}/stk_holdernumber.parquet'); n0 = len(old)
dup = old.tail(3).drop(columns=['code']); newrow = pd.DataFrame([dict(ts_code='600000.SH', ann_date='20261010', end_date='20260930', holder_num=123456)])
assert r.merge_arrow('stk_holdernumber', pd.concat([dup, newrow, newrow])) == 1
x = pd.read_parquet(f'{tmp}/stk_holdernumber.parquet'); assert len(x) == n0 + 1 and x.iloc[-1].code == 'SH600000', x.tail(2)
assert r.merge_arrow('stk_holdernumber', newrow) == 0
a0 = pq.read_metadata(f'{tmp}/adj_factor.parquet').num_rows; last = pd.read_parquet(f'{tmp}/adj_factor.parquet', filters=[('trade_date', '==', '20260918')])
print('adj rows on 20260918', len(last))
nd = last.drop(columns=['code']).assign(trade_date='20260921')
assert r.merge_arrow('adj_factor', pd.concat([last.drop(columns=['code']), nd])) == len(nd)
assert pq.read_metadata(f'{tmp}/adj_factor.parquet').num_rows == a0 + len(nd)
print('merge tests pass'); shutil.rmtree(tmp)
