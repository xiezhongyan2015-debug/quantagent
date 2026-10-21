"""Which signal slot / execution window to trade: 10:30->10:31, 11:30->13:01, 14:00->14:01, 15:00->next 09:31.
Registered 2026-10-15.  Skeleton = official CSI500 weights (v2), c5 hard zeros (state as of the previous close), k=.5 tilt
with the blend of the walk-forward control z at the chosen slot and the c5 z (previous close).  Execution price = the
frozen dataset's execution-window mean raw price after the slot (execution_raw_prices x adjustment factor), tradable =
execution_complete & execution_tradable & ~all_flat; marks at the signal hour's adjusted close (daily panel basis); NAV at the day's close (daily panel).  One rebalance per day.  Fee 2bp, 10M.  Window 2024-06-04..2026-09-17 (v2 calendar).  Arms: slot10,
slot11, slot14, slot15 (+ pure skeleton I0 for reference).  Compared pairwise with block bootstrap.
Caveat: names outside the 1178 union have no execution tape and are held at marks (reported); the official-weight
universe is inside the union on every date of this window (checked).
"""
from pathlib import Path
import json, time
import numpy as np
import pandas as pd
from scipy.special import ndtri
from .data import ROOT, OUT, END, write, sha
from . import replay as R
from .optimizer_overlay_v4 import C5_PATH
from . import wide_pool_optimizer_v1 as V1

DEST = OUT/'slot_execution_v1'; V2 = OUT/'wide_pool_optimizer_v2'; OFFICIAL = ROOT/'outputs/csi500_index_weight_tushare_official_monthly.csv'
CAPITAL = 10_000_000.; FEE = .0002; INVESTED = .98; K = .5; TZ = 'Asia/Shanghai'; SLOT_HOUR = {10: 0, 11: 1, 14: 2, 15: 3}


def main():
    DEST.mkdir(exist_ok=True); t0 = time.time()
    DATA = ROOT/'gru_research/artifacts/dataset'; symbols = json.load(open(DATA/'symbols.json')); U = len(symbols); lookup = {s: i for i, s in enumerate(symbols)}
    times = np.load(DATA/'times.npy'); clock = pd.to_datetime(times, utc=True).tz_convert(TZ); day_of = clock.strftime('%Y-%m-%d').to_numpy(); hour = clock.hour.to_numpy()
    ohlcv = np.load(DATA/'hour_ohlcv.npy', mmap_mode='r'); execp = np.load(DATA/'execution_raw_prices.npy', mmap_mode='r')
    efill = np.load(DATA/'execution_complete.npy', mmap_mode='r') & np.load(DATA/'execution_tradable.npy', mmap_mode='r') & ~np.load(DATA/'execution_all_flat.npy', mmap_mode='r')
    members = np.load(DATA/'members.npy', mmap_mode='r')
    # daily panel (baostock 后复权 incl. dividends) = the v2 ledger basis; the hourly tape only supplies raw execution prices
    prices = pd.read_parquet(OUT/'daily_prices.parquet'); psyms = json.load(open(OUT/'price_symbols.json')); pl = {s: i for i, s in enumerate(psyms)}; sel_cols = np.array([pl[s] for s in symbols])
    pdays = sorted(set(prices.date)); ppos = {d: i for i, d in enumerate(pdays)}
    widep = lambda f: prices.pivot(index='date', columns='symbol', values=f).reindex(index=pdays, columns=psyms).apply(pd.to_numeric, errors='coerce').to_numpy(float)[:, sel_cols]
    dclose = widep('close_adjusted'); dfactor = widep('factor')
    days = sorted(set(day_of)); rows_of = {}
    for i, d in enumerate(day_of): rows_of.setdefault(d, {})[int(hour[i])] = i
    historic = pd.read_parquet(ROOT/'outputs/cluster_sync_20261011/teammate_latest_uploads/unpacked/pcf_alpha_materials_20261011/benchmarks/H00905_daily.parquet')[['date', 'close']]
    recent = pd.read_csv(ROOT/'outputs/gru_beta_neutral_20261008/benchmark_sources/H00905_daily.csv')[['date', 'close']]
    for f in (historic, recent): f['date'] = pd.to_datetime(f.date).dt.strftime('%Y-%m-%d')
    bench = pd.concat([historic, recent]).drop_duplicates('date', keep='last').set_index('date').close.astype(float)
    pb = pd.read_csv(ROOT/'outputs/gru_beta_neutral_20261008/benchmark_sources/000905_daily.csv').set_index('date').close
    # official weights drifted by adjusted 15:00 close
    ow = pd.read_csv(OFFICIAL, dtype={'trade_date': str}); ow['date'] = pd.to_datetime(ow.trade_date, format='mixed').dt.strftime('%Y-%m-%d'); snaps = {}
    for d, g in ow.groupby('date'):
        v = np.zeros(U); miss = 0
        for c, w in zip(g.con_code, g.weight):
            if c in lookup: v[lookup[c]] = float(w)
            else: miss += 1
        snaps[d] = (v, miss)
    snap_dates = sorted(snaps); adj_close15 = lambda d: dclose[ppos[d]] if d in ppos else None
    def ref_weights(d):
        sd = [x for x in snap_dates if x <= d][-1]; base, miss = snaps[sd]; p0 = adj_close15(sd); p1 = adj_close15(d)
        if p0 is None or p1 is None: return None, miss
        okd = np.isfinite(p0) & np.isfinite(p1) & (p0 > 0); w = np.where(okd, base * p1 / np.where(okd, p0, 1.), base); w = np.where(np.isfinite(w) & (w > 0), w, 0.); return w / w.sum() * INVESTED, miss
    # signals per slot (walk-forward control), c5 z and zeros
    sig = {s: R.signals(s, days, symbols)[0] for s in (10, 11, 14, 15)}
    c5 = pd.read_parquet(C5_PATH); c5 = c5[c5.segment != 'live']; s_c5 = {}; bad_c5 = {}
    for day, g in c5.groupby('date'):
        z = np.full(U, np.nan); gg = g[g.code.isin(lookup)]; z[[lookup[c] for c in gg.code]] = ndtri(np.clip(gg.score_pct.to_numpy(), 1e-4, 1 - 1e-4))
        bad = gg[(gg.state == 'vetoed') | ((gg.state == 'excluded') & np.isfinite(gg.raw_score.to_numpy(dtype=float)))]; v = np.zeros(U, bool); v[[lookup[c] for c in bad.code]] = True; s_c5[day] = z; bad_c5[day] = v
    def blend(a, b): return np.where(np.isfinite(a) & np.isfinite(b), (a + b) / 2, np.where(np.isfinite(a), a, b))
    cal = pd.read_csv(V2/'I0_daily.csv').date.tolist()      # execution days of the v2 calendar
    arms = {'I0': None, 'slot10': 10, 'slot11': 11, 'slot14': 14, 'slot15': 15}; results = {}; audit = []
    for arm, slot in arms.items():
        units = np.zeros(U); marks = np.zeros(U); cash = CAPITAL; rows = []; last_c5 = None
        for day in cal:
            di = days.index(day); prev_day = days[di - 1]
            if prev_day in s_c5: last_c5 = prev_day
            cday = last_c5
            if slot in (None, 15):
                sig_day = prev_day; exec_row = rows_of[prev_day].get(15); ref_day = prev_day                 # 15:00 signal yesterday, execute at today's 09:31 window
            else:
                sig_day = day; exec_row = rows_of[day].get(slot); ref_day = prev_day                           # intraday signal today, execute in the following window today
            ref, miss = ref_weights(ref_day)
            if ref is None or exec_row is None or cday is None: rows.append(dict(date=day, nav=(cash + units @ marks) / CAPITAL, turnover=0., skipped=1, cash_weight=float(cash / (cash + units @ marks)), fee_cny=0., stale_weight=0., member_nav_weight=0., fill_blocked_weight=0., max_stock_nav_weight=0., holdings=int((units > 0).sum()))); continue
            # marks at the signal hour's adjusted close
            fac_day = dfactor[ppos[day]]                                            # execution day's adjustment factor (daily basis)
            if slot in (None, 15): mk = dclose[ppos[sig_day]]
            else: mk = np.asarray(ohlcv[exec_row, :, 8], float) * fac_day                # raw close of the signal hour on the daily basis
            ok = np.isfinite(mk) & (mk > 0); marks[ok] = mk[ok]; nav = cash + units @ marks
            w = ref.copy(); w[bad_c5[cday]] = 0.; w = w / w.sum() * INVESTED if w.sum() > 0 else ref
            if slot is not None:
                zz = blend(sig[slot].get(sig_day, np.full(U, np.nan)), s_c5[cday]); w = R.target_weights(w, zz, K)
            desired = np.divide(w * nav, marks, out=np.zeros(U), where=marks > 0)
            px = np.asarray(execp[exec_row], float) * fac_day; can = np.asarray(efill[exec_row]) & np.isfinite(px) & (px > 0)
            delta = desired - units; delta[~can] = 0.; delta[np.abs(delta) * np.where(marks > 0, marks, 1.) < 1000] = 0.
            sell = np.maximum(-delta, 0); proceeds = sell * np.where(can, px, 0.); cash += float((proceeds * (1 - FEE)).sum()); units -= sell
            buy = np.maximum(delta, 0); cost = float((buy * np.where(can, px, 0.)).sum() * (1 + FEE))
            if cost > cash: buy *= max(cash, 0) / max(cost, 1e-12)
            paid = buy * np.where(can, px, 0.); cash -= float(paid.sum() * (1 + FEE)); units += buy
            c15 = dclose[ppos[day]]; ok = np.isfinite(c15) & (c15 > 0); marks[ok] = c15[ok]; nav = cash + units @ marks
            eq = units * marks; memb = np.asarray(members[rows_of[ref_day][15]]) if 15 in rows_of[ref_day] else np.ones(U, bool)
            rows.append(dict(date=day, nav=nav / CAPITAL, turnover=float((paid.sum() + proceeds.sum()) / nav), skipped=0, cash_weight=float(cash / nav), fee_cny=float((paid.sum() + proceeds.sum()) * FEE), stale_weight=0.,
                             member_nav_weight=float(eq[memb].sum() / nav), fill_blocked_weight=float((np.abs(delta) * marks)[~can].sum() / nav), max_stock_nav_weight=float(eq.max() / nav), holdings=int((units > 0).sum())))
            if arm == 'I0': audit.append(dict(date=day, snapshot_missing=miss))
        d = pd.DataFrame(rows); d['H00905_nav'] = bench.loc[d.date].to_numpy() / bench.loc[d.date.iloc[0]]; d.to_csv(DEST/f'{arm}_daily.csv', index=False); results[arm] = d
        rec = R.summary(d, bench.loc[d.date].to_numpy(), pb.loc[d.date].to_numpy()); print('ARM', arm, round(rec['annualized_relative_excess'], 4), 'TE', round(rec['tracking_error'], 4), 'turnover', round(float(d.turnover.mean()), 4), 'skipped', int(d.skipped.sum()), flush=True)
    dates = results['I0'].date.tolist(); comp = []
    for x, y in [('slot15', 'I0'), ('slot14', 'I0'), ('slot11', 'I0'), ('slot10', 'I0'), ('slot14', 'slot15'), ('slot11', 'slot15'), ('slot10', 'slot15'), ('slot14', 'slot11')]:
        st, hv = V1.paired(results[x].nav.to_numpy(), results[y].nav.to_numpy(), np.asarray(dates), 4)
        lg = np.log(results[x].nav.to_numpy()[1:] / results[x].nav.to_numpy()[:-1]) - np.log(results[y].nav.to_numpy()[1:] / results[y].nav.to_numpy()[:-1]); ser = pd.Series(lg, index=pd.to_datetime(dates[1:]))
        comp.append(dict(arm=x, versus=y, **{k: v for k, v in st.items() if k in ('days', 'annualized_relative_cagr', 'boot95_low', 'boot95_high', 'tracking_error', 'information_ratio', 'w50_p10', 'w50_worst', 'w50_win')}, **{f'y{yy}': round(float(g.sum() * 252 / len(g) * 100), 2) for yy, g in ser.groupby(ser.index.year)}))
    C = pd.DataFrame(comp); C.to_csv(DEST/'paired_comparisons.csv', index=False); pd.DataFrame(audit).to_csv(DEST/'audit.csv', index=False)
    write(DEST/'run_receipt.json', dict(code_sha256=sha(__file__), seconds=round(time.time() - t0)))
    pd.set_option('display.width', 250); print(C.round(4).to_string(index=False))


if __name__ == '__main__':
    main()
