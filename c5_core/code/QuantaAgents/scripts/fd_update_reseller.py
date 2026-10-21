"""Incremental fundamental update through the Tushare-compatible reseller endpoint (tl.kaixin8.top), 2026-10-13.
The reseller rejects the ann_date-per-day queries used by fd_update.py, so this uses what it accepts (probed 2026-10-13):
  trade_date=<day>              daily, daily_basic, adj_factor
  end_date=<day>                pledge_stat
  start_date/end_date window    stk_holdernumber, stk_holdertrade, express, repurchase, forecast (often busy -> best effort)
  ts_code (+start_date)         income, cashflow, balancesheet, fina_indicator, fina_audit  for stocks whose disclosure_date.actual_date is in the window
Rows are merged with fd_update.merge_raw (same raw parquet files, duplicates dropped, atomic write).
Credentials: JSON {"token", "url"} at --secret (default C:/ChoiceBridge/live/secrets/tushare.json); never printed.
Usage: python scripts/fd_update_reseller.py --start 20260919 --end 20261013   |   --days 5  (last 5 weekdays up to today)"""
import argparse, json, os, sys, time
import pandas as pd, requests
import pyarrow as pa, pyarrow.parquet as pq, pyarrow.compute as pc
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fd_update as fu

class Reseller:
    def __init__(self, secret, min_gap=0.3):
        cfg = json.load(open(secret, encoding='utf-8')); self.url, self.tok, self.gap, self.last, self.calls = cfg['url'], cfg['token'], min_gap, 0.0, 0
    def call(self, api, params):
        err = None
        for attempt in range(6):
            time.sleep(max(0.0, self.last + self.gap - time.time())); self.last = time.time(); self.calls += 1
            try: j = requests.post(self.url, json=dict(api_name=api, token=self.tok, params=params, fields=''), timeout=240).json()
            except Exception as e: err = type(e).__name__; time.sleep(10); continue
            code = j.get('code')
            if code == 0:
                d = j.get('data') or {}; return pd.DataFrame(d.get('items') or [], columns=d.get('fields') or [])
            if code in (429, 503, 504): err = f'{code} busy'; time.sleep(31); continue
            raise RuntimeError(f'{api} {params}: code {code} {str(j.get("msg"))[:60]}')
        raise RuntimeError(f'{api} {params}: retries exhausted ({err})')

def weekdays(start, end): return pd.bdate_range(pd.Timestamp(start), pd.Timestamp(end)).strftime('%Y%m%d').tolist()

def window(cl, api, start, end, cap=3000):
    """Date-window query; split the window when a reply looks truncated."""
    df = cl.call(api, dict(start_date=start, end_date=end))
    if len(df) >= cap and start < end:
        days = weekdays(start, end); mid = days[len(days) // 2 - 1] if len(days) > 1 else start
        nxt = (pd.Timestamp(mid) + pd.Timedelta(days=1)).strftime('%Y%m%d')
        return pd.concat([window(cl, api, start, mid, cap), window(cl, api, nxt, end, cap)], ignore_index=True)
    if 'ann_date' in df.columns: df = df[(df['ann_date'] >= start) & (df['ann_date'] <= end)]
    return df

DATE_KEYED = {'daily', 'daily_basic', 'adj_factor'}
def merge_arrow(api, new):
    """Append rows not already present. Only the slice of the old table that can collide (same trade dates, or same codes)
    is materialised in pandas, so the 500 MB daily tables never load whole into pandas. Atomic replace."""
    path = os.path.join(fu.RAW, f'{api}.parquet'); old = pq.read_table(path); cols = old.column_names
    new = fu.add_code(new.copy())
    for c in cols:
        if c not in new.columns: new[c] = None
    new = new[cols].copy()
    for f in old.schema:
        if pa.types.is_floating(f.type) or pa.types.is_integer(f.type): new[f.name] = pd.to_numeric(new[f.name], errors='coerce').astype('float64' if pa.types.is_floating(f.type) else 'Int64')
        else: new[f.name] = new[f.name].map(lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    new = new.drop_duplicates()
    col = 'trade_date' if api in DATE_KEYED and 'trade_date' in cols else 'code'
    sub = old.filter(pc.is_in(old[col], value_set=pa.array(sorted(new[col].dropna().astype(str).unique()), type=pa.string()))).to_pandas()
    key = ['code', 'trade_date'] if col == 'trade_date' else cols   # daily tables: one row per stock and day, first version kept
    h = lambda d: pd.util.hash_pandas_object(d[key].astype(str), index=False)
    if col == 'trade_date': new = new.drop_duplicates(subset=key)
    seen = set(h(sub)) if len(sub) else set(); fresh = new[~h(new).isin(seen).values] if len(new) else new
    if len(fresh):
        tmp = path + '.tmp'; pq.write_table(pa.concat_tables([old, pa.Table.from_pandas(fresh, schema=old.schema, preserve_index=False)]), tmp, compression='zstd'); os.replace(tmp, path)
    fu.log(f'[{api}] +{len(fresh)} rows (fetched {len(new)}, already present {len(new) - len(fresh)})'); return len(fresh)

def merge(api, df, summary):
    if len(df): summary[api] = merge_arrow(api, df)
    else: summary[api] = 0; fu.log(f'[{api}] nothing new')

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--start'); ap.add_argument('--end'); ap.add_argument('--days', type=int)
    ap.add_argument('--secret', default='C:/ChoiceBridge/live/secrets/tushare.json'); ap.add_argument('--skip', nargs='*', default=[])
    ap.add_argument('--max-minutes', type=float, default=None, help='stop starting new tables after this many minutes (daily chain budget)')
    ap.add_argument('--since-last', action='store_true', help='start 2 weekdays before the last successful end (daily chain)')
    a = ap.parse_args(); end = a.end or time.strftime('%Y%m%d')
    start = a.start or weekdays((pd.Timestamp(end) - pd.Timedelta(days=3 * (a.days or 5))).strftime('%Y%m%d'), end)[-(a.days or 5)]
    state_p = os.path.join(fu.RAW, '_reseller_update_state.json')
    if a.since_last and os.path.exists(state_p):
        last = json.load(open(state_p)).get('last_ok_end')
        if last: start = weekdays((pd.Timestamp(last) - pd.Timedelta(days=6)).strftime('%Y%m%d'), last)[-3]
    cl = Reseller(a.secret); days = weekdays(start, end); summary, failed = {}, {}
    fu.log(f'reseller update {start}..{end} ({len(days)} weekdays)')
    t0 = time.time()
    def step(api, fn):
        if api in a.skip: return
        if a.max_minutes and time.time() - t0 > a.max_minutes * 60: failed[api] = 'time budget exhausted'; fu.log(f'[{api}] skipped: time budget'); return
        try: merge(api, fn(), summary)
        except Exception as e: failed[api] = str(e)[:160]; fu.log(f'[{api}] FAILED {failed[api]}')
    def missing_days(api):   # 2026-10-14: the reseller is slow for trade_date queries; skip days the raw table already has
        try: have = set(pq.read_table(os.path.join(fu.RAW, f'{api}.parquet'), columns=['trade_date']).column('trade_date').unique().to_pylist())
        except Exception: have = set()
        present = [d for d in days if d in have]   # re-read the newest present day too: a day fetched early may have been partial
        return sorted({d for d in days if d not in have} | set(present[-1:]))
    for api in ('daily', 'daily_basic', 'adj_factor'):
        step(api, lambda api=api: pd.concat([cl.call(api, dict(trade_date=d)) for d in missing_days(api)] or [pd.DataFrame()], ignore_index=True))
    step('pledge_stat', lambda: pd.concat([cl.call('pledge_stat', dict(end_date=d)) for d in days], ignore_index=True))
    for api in ('stk_holdernumber', 'stk_holdertrade', 'express', 'repurchase', 'forecast'):
        step(api, lambda api=api: window(cl, api, start, end))
    # statements: stocks whose actual disclosure date falls in the window (fresh disclosure calendar for the last two periods)
    try:
        dd = pd.concat([cl.call('disclosure_date', dict(end_date=p)) for p in sorted({f'{int(end[:4]) - (1 if end[4:] < "0501" else 0)}0630', f'{end[:4]}0930' if end[4:] >= '0930' else f'{end[:4]}0630', f'{int(end[:4]) - 1}1231'})], ignore_index=True)
        merge('disclosure_date', dd, summary)
        codes = sorted(dd.loc[dd['actual_date'].fillna('').between(start, end), 'ts_code'].dropna().unique())
    except Exception as e: codes = []; failed['disclosure_date'] = str(e)[:160]
    fu.log(f'statements: {len(codes)} stocks disclosed in the window')
    since = f'{int(end[:4]) - 1}0101'
    for api in ('income', 'cashflow', 'balancesheet', 'fina_indicator', 'fina_audit'):
        step(api, lambda api=api: pd.concat([cl.call(api, dict(ts_code=c, start_date=since)) for c in codes], ignore_index=True) if codes else pd.DataFrame())
    critical = {'daily_basic', 'stk_holdernumber', 'stk_holdertrade', 'pledge_stat'} - set(a.skip)
    prev = json.load(open(state_p)) if os.path.exists(state_p) else {}
    out = dict(updated=time.strftime('%Y-%m-%dT%H:%M:%S'), start=start, end=end, rows_added=summary, failed=failed, calls=cl.calls, statement_stocks=len(codes),
               last_ok_end=end if not critical & set(failed) else prev.get('last_ok_end'))
    json.dump(out, open(state_p, 'w'), indent=1)
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(3 if critical & set(failed) else 0)

if __name__ == '__main__': main()
