"""Rebuild the 13-field paired_v1 account base locally (the frozen base.npz never left the cluster).

Run: .pcf-research-venv/bin/python -I pcf_overlay_20261012/size_rank_replay/rebuild_base.py --snapshot <snapshot dir> --out <dir>

Sources:
- dates / signal_dates / symbols / reference: copied bit-for-bit from paired_v1 P0_plans.npz (P0 target == reference).
- signal_time_ns: T15 Asia/Shanghai of each signal date.  valuation_dates: local pcf_metadata.csv.
- membership_at_execution_close: OLD_RANK six-field signal membership at the execution date (the driver asserts this).
- prices: their adjusted price is raw x a per-dividend-segment constant.  We take local raw open/close, a local
  cumulative ex-date factor L (baostock: close[t-1]/preclose[t]; tencent rows: qfq/raw factor jumps > 0.3%), and pin
  the remaining ratio q = their trade price / (raw*L) on every executed trade of the four paired_v1 arms, carrying q
  between trade days (a q step is put on the day with the largest local jump inside the gap, else on the later trade day).
  A per-symbol constant scale cancels exactly in account(); only q steps (ex-date disagreements) change returns.
- fill: local prior-session open>0, close>0, amount>0 (tencent rows: volume>0), not ST (as candidate_replay.prepare_inputs), overridden by
  ledger evidence: any executed trade => fillable; a nonzero order with no trade in that arm => not fillable.
- benchmark: not read by account()/paired_summary(); placeholder ones.
Every field is digested against paired_v1 input_receipt.non_score_array_sha256; mismatches are reported, not hidden.
"""
from pathlib import Path
import argparse, hashlib, json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / 'outputs/pcf_overlay_20261012'
ARMS = ('P0', 'OLD_RANK', 'RANK_NULL', 'RANK_STATS')


def array_digest(value):  # identical to candidate_replay.array_digest
    a = np.ascontiguousarray(value); h = hashlib.sha256()
    h.update(a.dtype.str.encode()); h.update(json.dumps(a.shape).encode()); h.update(a.tobytes())
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--snapshot', type=Path, required=True); ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args(); snap = a.snapshot.resolve(); out = a.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    paired = snap / 'DATA/gru_rank_minute_20261015/accounts/paired_v1'
    frozen = json.load(open(paired / 'input_receipt.json'))['non_score_array_sha256']
    with np.load(paired / 'P0_plans.npz', allow_pickle=False) as z:
        dates, signal_dates, symbols, reference = z['dates'], z['signal_dates'], z['symbols'], z['target']
    T, U = reference.shape; names = symbols.astype(str)
    base = {'dates': dates, 'signal_dates': signal_dates, 'symbols': symbols, 'reference': reference}
    local_t15 = signal_dates.astype('datetime64[ns]').astype(np.int64) + 15 * 3600 * 10**9
    base['signal_time_ns'] = (local_t15 - 8 * 3600 * 10**9).astype(np.int64)
    meta = pd.read_csv(LOCAL / 'pcf_metadata.csv', dtype=str).set_index('date')
    base['valuation_dates'] = np.asarray([meta.loc[str(d), 'valuation_date'] for d in signal_dates], dtype='datetime64[D]')
    with np.load(snap / 'HOME/gru_teammate_features_20261014_scratch_v2/inference/OLD_RANK_ensemble_signals.npz', allow_pickle=False) as z:
        sd, ss, sm = z['dates'], z['symbols'], z['membership']
    ei = pd.Index(sd).get_indexer(dates.astype('U10')); cols = pd.Index(ss).get_indexer(names); cov = cols >= 0
    mem = np.zeros((T, U), bool); mem[:, cov] = sm[ei[:, None], cols[cov][None, :]]; base['membership_at_execution_close'] = mem

    # calendar: row 0 = first signal date, rows 1..T = execution dates (signal_dates[t] == calendar[t])
    cal = np.r_[signal_dates[:1], dates].astype('U10')
    if not np.array_equal(signal_dates.astype('U10'), cal[:-1]): raise SystemExit('signal dates are not the prior execution sessions')
    px = pd.read_parquet(LOCAL / 'daily_prices.parquet', columns=['date', 'symbol', 'open', 'close', 'preclose', 'amount', 'volume', 'isST', 'factor', 'source'])
    px = px[px.date.isin(set(cal)) | ((px.date >= '2024-12-20') & (px.date <= cal[0]))]
    prior = sorted(d for d in px.date.unique() if d < cal[0])[-1]; full = np.r_[[prior], cal]  # one extra session for the first jump
    wide = lambda f: px.pivot(index='date', columns='symbol', values=f).reindex(index=full, columns=names)
    op, cl, pre, am, vo, st, fa = (wide(f).apply(pd.to_numeric, errors='coerce').to_numpy(float) for f in ('open', 'close', 'preclose', 'amount', 'volume', 'isST', 'factor'))
    am = np.where(np.isfinite(am), am, vo)  # tencent rows carry volume but no amount/ST field
    tencent = wide('source').to_numpy(object) == 'tencent_public_daily'
    with np.errstate(divide='ignore', invalid='ignore'):
        jb = cl[:-1] / pre[1:]
        jt = fa[1:] / fa[:-1]
    jump = np.where(tencent[1:], np.where(np.abs(jt - 1) > 3e-3, jt, 1.), np.where(np.abs(jb - 1) > 1e-6, jb, 1.))
    jump = np.where(np.isfinite(jump) & (jump > 0), jump, 1.)               # rows 1..: jump on session full[i]
    L = np.vstack([np.ones((1, U)), np.cumprod(jump, axis=0)])[1:]          # aligned to cal (drop the extra prior row)
    raw_open, raw_close = op[1:], cl[1:]

    # ledger evidence
    trades = pd.concat([pd.read_csv(paired / f'{arm}_trades.csv', usecols=['date', 'symbol', 'adjusted_price']).assign(arm=arm) for arm in ARMS])
    tr = trades.drop_duplicates(['date', 'symbol'])
    ci = pd.Index(cal).get_indexer(tr.date); ui = pd.Index(names).get_indexer(tr.symbol)
    q_obs = np.full((len(cal), U), np.nan); q_obs[ci, ui] = tr.adjusted_price.to_numpy() / (raw_open[ci, ui] * L[ci, ui])
    q = np.ones((len(cal), U)); steps = 0; step_sizes = []; unexplained = 0
    for j in range(U):
        rows = np.flatnonzero(np.isfinite(q_obs[:, j]))
        if not len(rows): continue
        q[:, j] = q_obs[rows[0], j]
        for a_, b_ in zip(rows[:-1], rows[1:]):
            qa, qb = q_obs[a_, j], q_obs[b_, j]
            if abs(qb / qa - 1) <= 1e-7: q[a_:b_ + 1, j] = qa; continue
            gap = np.arange(a_ + 1, b_ + 1); ev = np.abs(np.log(jump[gap, j])); k = gap[np.argmax(ev)] if ev.max() > 0 else b_
            unexplained += int(ev.max() == 0); steps += 1; step_sizes.append(abs(np.log(qb / qa)))
            q[a_:k, j] = qa; q[k:b_ + 1, j] = qb
        q[rows[-1]:, j] = q_obs[rows[-1], j]
    adj_open, adj_close = raw_open * L * q, raw_close * L * q
    base['signal_marks'] = adj_close[:-1]; base['closes'] = adj_close[1:]; base['execution_prices'] = adj_open[1:]
    po, pc, pa, ps = op[1:-1], cl[1:-1], am[1:-1], st[1:-1]                # signal-date (prior-session) fields
    fill = np.isfinite(po) & (po > 0) & np.isfinite(pc) & (pc > 0) & np.isfinite(pa) & (pa > 0) & (ps != 1)
    local_fill = fill.copy()
    ti = pd.Index(dates.astype('U10')).get_indexer(trades.date); tu = pd.Index(names).get_indexer(trades.symbol)
    fill[ti, tu] = True
    blocked = 0
    for arm in ARMS:
        with np.load(paired / f'{arm}_plans.npz', allow_pickle=False) as z: orders = z['order_units'] != 0
        done = np.zeros((T, U), bool); s = trades[trades.arm == arm]
        done[pd.Index(dates.astype('U10')).get_indexer(s.date), pd.Index(names).get_indexer(s.symbol)] = True
        no = orders & ~done; blocked += int(no.sum()); fill[no] = False
    base['fill'] = fill; base['benchmark'] = np.ones(T); base['scores'] = np.full((T, U), np.nan)
    np.savez_compressed(out / 'base_rebuilt.npz', **base)
    ex = base['execution_prices'][ti, tu]; rel = np.abs(ex / trades.adjusted_price.to_numpy() - 1)
    audit = {'source': 'local rebuild; frozen base.npz (sha 3c75b1bd...) absent from the 2026-10-17 sync',
             'fields_bitwise_equal_to_paired_v1_digest': {k: array_digest(base[k]) == frozen[k] for k in sorted(frozen)},
             'prices': {'trade_cells': int(len(tr)), 'symbols_with_trades': int(np.isfinite(q_obs).any(0).sum()),
                        'q_steps_between_trade_days': steps, 'q_steps_without_local_jump_evidence': unexplained,
                        'q_step_abs_log_median': float(np.median(step_sizes)) if step_sizes else 0., 'q_step_abs_log_max': float(max(step_sizes)) if step_sizes else 0.,
                        'max_rel_error_vs_all_trade_prices': float(np.nanmax(rel)), 'trade_price_cells_missing_locally': int(np.isnan(rel).sum())},
             'fill': {'local_rule_cells_false': int((~local_fill).sum()), 'ledger_trades_where_local_rule_false': int((~local_fill[ti, tu]).sum()),
                      'ledger_unfilled_orders_set_false': blocked, 'ledger_unfilled_where_local_rule_true': int((local_fill & ~fill).sum())},
             'benchmark_note': 'placeholder ones; benchmark is not read by account() or paired_summary()'}
    (out / 'base_rebuild_audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2))
    print(json.dumps(audit, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
