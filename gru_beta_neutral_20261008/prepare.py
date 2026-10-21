"""Frozen prior-information exposures, benchmark validation and backfill inventory."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from .attribution import ROOT, DATA, OUT, sha

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    times = np.load(DATA/'times.npy', mmap_mode='r')
    daytimes = times[3::4]
    dates = pd.to_datetime(daytimes, utc=True).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d').to_numpy()
    src = OUT/'benchmark_sources'
    benchmark = {}
    for code in ['000905', 'H00905']:
        value = json.loads((src/(code+'.json')).read_text())
        assert str(value['code']) == '200' and value['success']
        frame = pd.DataFrame(value['data'])
        assert frame.indexCode.eq(code).all()
        frame['date'] = pd.to_datetime(frame.tradeDate, format='%Y%m%d').dt.strftime('%Y-%m-%d')
        assert not frame.date.duplicated().any()
        assert np.isfinite(frame.close).all() and (frame.close > 0).all()
        frame = frame[frame.date.isin(dates) | (frame.date == '2024-12-31')]
        frame[['date', 'indexCode', 'indexNameCnAll', 'close']].to_csv(src/(code+'_daily.csv'), index=False)
        benchmark[code] = frame.set_index('date').close
    prior = pd.read_csv(OUT/'source_snapshot/official_csi500_price_2025_2026.csv').set_index('date').close
    matched = benchmark['000905'].reindex(prior.index)
    assert matched.notna().all() and np.max(np.abs(matched-prior)) < .011
    close = np.asarray(np.load(DATA/'hour_ohlcv.npy', mmap_mode='r')[3::4, :, 3], float)
    stockret = np.full_like(close, np.nan)
    valid = np.isfinite(close[1:]) & np.isfinite(close[:-1]) & (close[1:] > 0) & (close[:-1] > 0)
    np.divide(close[1:], close[:-1], out=stockret[1:], where=valid)
    stockret[1:] -= 1
    indexclose = benchmark['000905'].reindex(dates)
    assert indexclose.notna().all()
    marketret = indexclose.pct_change(fill_method=None).to_numpy()
    # Every exposure on date d ends at d-1 close, not the future d close.
    beta = np.full_like(close, np.nan)
    observations = np.zeros_like(close, dtype=np.int16)
    for d in range(60, len(dates)):
        y, m = stockret[d-60:d], marketret[d-60:d, None]
        ok = np.isfinite(y) & np.isfinite(m)
        n = ok.sum(axis=0)
        ym = np.where(ok, y, 0).sum(axis=0)/np.maximum(n, 1)
        mm = np.where(ok, m, 0).sum(axis=0)/np.maximum(n, 1)
        covariance = np.where(ok, (y-ym)*(m-mm), 0).sum(axis=0)
        variance = np.where(ok, (m-mm)**2, 0).sum(axis=0)
        np.divide(covariance, variance, out=beta[d], where=(n>=40)&(variance>1e-12))
        observations[d] = n
    raw_path = DATA/'slow_raw_by_day.npy'
    if raw_path.exists():
        raw = np.load(raw_path, mmap_mode='r')
        volatility, turnover, log_cap = (np.asarray(raw[:, :, k]) for k in [5, 11, 15])
        style_source = {'kind': 'frozen_raw_panel', 'path': str(raw_path)}
    else:
        # The original remote deployment omitted this raw sidecar. Use the
        # exact three frozen columns, never normalized X as a substitute.
        sidecar = OUT/'slow_risk_inputs.npz'
        evidence = json.loads((OUT/'slow_risk_inputs_receipt.json').read_text())
        assert sha(sidecar) == evidence['sha256']
        assert sha(DATA/'times.npy') == evidence['times_sha256']
        assert sha(DATA/'symbols.json') == evidence['symbols_sha256']
        raw = np.load(sidecar)
        volatility, turnover, log_cap = (raw[k] for k in ['volatility', 'turnover', 'log_cap'])
        assert all(x.shape == close.shape for x in [volatility, turnover, log_cap])
        style_source = evidence
    q = np.load(ROOT/'gru_market_attention_20261007/context.npz')
    assert np.array_equal(q['times'], times)
    np.savez_compressed(OUT/'risk_context.npz', times=times, dates=dates,
                        beta60=np.repeat(beta, 4, axis=0).astype(np.float32),
                        beta_observations=np.repeat(observations, 4, axis=0),
                        volatility=np.repeat(volatility, 4, axis=0),
                        turnover=np.repeat(turnover, 4, axis=0),
                        log_cap=np.repeat(log_cap, 4, axis=0),
                        industry=q['industry'])
    members = np.load(DATA/'members.npy', mmap_mode='r')
    complete = np.load(DATA/'hour_complete.npy', mmap_mode='r')
    bad = np.vstack([np.zeros((1, members.shape[1]), np.int32), np.cumsum(~complete, axis=0, dtype=np.int32)])
    window = np.zeros_like(members)
    window[79:] = (bad[80:]-bad[:-80]) == 0
    daily = pd.DataFrame({'date': dates, 'members': members[3::4].sum(axis=1),
                          'complete80': (members & window)[3::4].sum(axis=1),
                          'hour_complete': (members & complete)[3::4].sum(axis=1)})
    daily['year'] = daily.date.str[:4].astype(int)
    daily['coverage80'] = daily.complete80/daily.members
    daily.to_csv(OUT/'coverage_daily.csv', index=False)
    annual = daily.groupby('year')[['members', 'complete80', 'hour_complete']].sum()
    annual['coverage80'] = annual.complete80/annual.members
    annual.to_csv(OUT/'coverage_by_year.csv')
    symbols = json.loads((DATA/'symbols.json').read_text())
    dm = members[3::4]
    requests = []
    for u, symbol in enumerate(symbols):
        starts = np.flatnonzero(dm[:, u] & ~np.r_[False, dm[:-1, u]])
        for start in starts:
            exits = np.flatnonzero(~dm[start:, u])
            end = start + int(exits[0]) if len(exits) else len(dates)-1
            lo, hi = max(start-120, 0), min(end+20, len(dates)-1)
            requests.append(dict(symbol=symbol, entry_date=dates[start], exit_or_tape_end=dates[end],
                                 requested_start=dates[lo], requested_end=dates[hi],
                                 available_daily_closes=int(np.isfinite(close[lo:hi+1, u]).sum()),
                                 requested_sessions=hi-lo+1,
                                 missing_daily_closes=int(np.isnan(close[lo:hi+1, u]).sum())))
    pd.DataFrame(requests).to_csv(OUT/'backfill_requests.csv', index=False)
    receipt = {'status': 'complete', 'code_sha256': sha(__file__),
               'risk_context_sha256': sha(OUT/'risk_context.npz'), 'style_source': style_source,
               'beta': '60 prior exchange sessions; minimum40 joint valid observations; no forward-fill of prices; unavailable remains NaN',
               'industry': 'Frozen historical provider S-codes; weekly snapshots strictly before signal date; proxy taxonomy',
               'reference': 'Prior-visible total-cap weights across current constituents; not official float-adjusted CSI weights',
               'price_benchmark_overlap_max_abs': float(np.max(np.abs(matched-prior))),
               'benchmark_sources': {k: json.loads((src/(k+'_receipt.json')).read_text()) for k in benchmark},
               'downloaded_h00905_identity_verified': True,
               'backfill_inventory_only': True, 'new_stock_data_downloaded': False}
    (OUT/'preparation.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(annual.to_string())
    print('risk context ready; backfill intervals', len(requests))

if __name__ == '__main__':
    main()
