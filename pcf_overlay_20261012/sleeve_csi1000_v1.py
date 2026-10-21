"""CSI1000 LONG SLEEVE v1 (2026-10-15, registered before simulation).

Question: the rule only requires >=80% (user: 70% acceptable) of the account in CSI500 names; the live construction keeps
85-93% in CSI500 and nothing is deliberately invested outside.  The teammate's c5 holds a 13% bucket of the top-110 c5 names
inside CSI1000 (not in CSI500) and attributes ~+2.5pp/yr to it in 2019-2024 research.  Does a long sleeve of CSI1000 names
chosen by the c5 score add to OUR constructions over 2024-06..2026-09, after costs, and how much of it is selection
(vs just owning CSI1000 beta)?

Sleeve universe: panel member_csi1000 & ~member_csi500 & eligible & not ST & listed >= 120 sessions, scored by c5 at the
signal date (bucket 'other_csi1000').  Rule (teammate bucket_book replica): enter when rank < N among scored universe
names and the next open is tradable; keep while rank < km*N (or score missing); leaving the universe forces a sale;
entries take an equal slot = invested/n_after funded by exits and a pro-rata trim ('drift' weighting); fee 2bp/side;
execution at the next session's adjusted open (panel open), marks at adjusted close.  Universe ~1000 names/day.
Arms (sleeve alone, 1.0 notional):
  EW         equal weight of the whole eligible sleeve universe, rebalanced on the first session of each month (beta baseline)
  S60/S110/S150   N = 60 / 110 / 150, km = 4, c5 union score_pct (teammate's setting is N=110, km=4)
  S110_km2   N = 110, km = 2 (tighter hysteresis, our 2bp cost regime)
  S110_tn    N = 110, km = 4, score = bucket_pct (turnover-neutral percentile)
  S110_eq    N = 110, km = 4, equal weight rebalanced daily (cost sensitivity)
Combinations (constant-mix daily, s in {10%, 15%, 20%}): core_live = v4 V5_P1blend (the live construction, PCF skeleton),
  core_index = v2 WV0 (official-weight index + c5 exclusion).  combo_t = (1-s) r_core + s r_sleeve.
Statistics: sleeve vs EW (selection alpha), sleeve vs H00905, combo vs core (block bootstrap 2000x20d, seed 20261011,
Bonferroni family 8), yearly, 50-day windows; CSI500 share of the combo = (1-s) x core share.
No parameter is chosen on results; the primary arm is S110 (teammate's setting) at s = 15%.
"""
from pathlib import Path
import json, time
import numpy as np
import pandas as pd
from .data import ROOT, OUT, write, sha
from . import replay as R
from .optimizer_overlay_v4 import C5_PATH
from . import wide_pool_optimizer_v1 as V1

DEST = OUT/'sleeve_csi1000_v1'; PANEL = ROOT/'c5_core/data/A_Layer_Live/panel'; V4 = OUT/'optimizer_overlay_v4'; V2 = OUT/'wide_pool_optimizer_v2'
FEE = .0002
ARMS = {'S60': dict(N=60, km=4, score='score_pct', weighting='drift'), 'S110': dict(N=110, km=4, score='score_pct', weighting='drift'), 'S150': dict(N=150, km=4, score='score_pct', weighting='drift'),
        'S110_km2': dict(N=110, km=2, score='score_pct', weighting='drift'), 'S110_tn': dict(N=110, km=4, score='bucket_pct', weighting='drift'), 'S110_eq': dict(N=110, km=4, score='score_pct', weighting='equal')}
SHARES = [.10, .15, .20]; CORES = {'core_live': V4/'V5_P1blend_daily.csv', 'core_index': V2/'WV0_daily.csv'}
PROTOCOL = dict(version='sleeve_csi1000_v1', registered='2026-10-15', universe='panel member_csi1000 & ~member_csi500 & eligible & ~ST & listed>=120, c5-scored (bucket other_csi1000)',
                rule='teammate bucket_book replica: enter rank<N & tradable next open; keep while rank<km*N or unscored; forced sale on leaving; drift weighting; fee 2bp', arms=ARMS, baseline='EW monthly',
                shares=SHARES, cores=list(CORES), mixing='constant-mix daily returns', primary='S110 at 15%', statistics='block bootstrap 2000x20d seed 20261011, Bonferroni 8', no_grid_search=True, parameters_chosen_on_results=False)


def preregister():
    DEST.mkdir(exist_ok=True); p = DEST/'protocol.json'
    if p.exists(): assert json.loads(p.read_text()) == json.loads(json.dumps(PROTOCOL)), 'Frozen sleeve protocol changed'
    else: write(p, PROTOCOL)


def load(name):
    x = pd.read_parquet(PANEL/f'{name}.parquet'); x.index = pd.to_datetime(x.index).strftime('%Y-%m-%d'); x.columns = [c[2:] + '.' + c[:2] for c in x.columns]; return x


def sleeve(arm, dates, prior_of, score_by_day, universe_by_day, tradable_by_day, opn, cls, codes):
    """units-based ledger of the sleeve; returns daily frame (nav, turnover, count, fee)."""
    p = ARMS.get(arm); n = len(codes); units = np.zeros(n); marks = np.zeros(n); cash = 1.0; held = np.zeros(n, bool); rows = []
    month_prev = None
    for t, day in enumerate(dates):
        prior = prior_of[day]; c_prev = cls.loc[prior].to_numpy(float); ok = np.isfinite(c_prev) & (c_prev > 0); marks[ok] = c_prev[ok]
        nav = cash + units @ marks; mem = universe_by_day[prior]; buyable = tradable_by_day[day] & mem
        w_cur = np.where(marks > 0, units * marks / nav, 0.)
        if arm == 'EW':
            month = day[:7]
            if month != month_prev or not held.any():
                target = np.where(mem & buyable, 1., 0.); target = target / target.sum() if target.sum() else target; month_prev = month
            else:
                target = np.where(mem, w_cur, 0.)     # hold (names that left the universe are sold)
            held = target > 0
        else:
            sc = score_by_day[prior]; scored = np.isfinite(sc) & mem; pos = np.full(n, np.inf)
            idx = np.flatnonzero(scored); order = idx[np.argsort(-sc[idx], kind='stable')]; pos[order] = np.arange(len(order))
            stay = held & mem & ((pos < p['km'] * p['N']) | ~scored); new = (~held) & (pos < p['N']) & buyable
            held_new = stay | new; n_after = int(held_new.sum())
            if p['weighting'] == 'equal' or not held.any():
                target = held_new / max(n_after, 1)
            else:
                target = np.where(stay, w_cur, 0.); k = int(new.sum())
                if n_after and k:
                    slot = 1. / n_after; ex = target.sum(); need = k * slot
                    if ex > 1. - need: target *= (1. - need) / ex
                    target[new] = slot
                elif n_after: target = target / target.sum()
            held = held_new
        desired = np.divide(target * nav, marks, out=np.zeros(n), where=marks > 0)
        px = opn.loc[day].to_numpy(float); can = tradable_by_day[day] & np.isfinite(px) & (px > 0); px = np.where(can, px, 0.)
        delta = desired - units; delta[~can] = 0.; delta[np.abs(delta) * np.where(marks > 0, marks, 1.) < 1e-6] = 0.
        sell = np.maximum(-delta, 0); proceeds = sell * px; cash += float((proceeds * (1 - FEE)).sum()); units -= sell
        buy = np.maximum(delta, 0); cost = float((buy * px).sum() * (1 + FEE))
        if cost > cash: buy *= max(cash, 0) / max(cost, 1e-12)
        paid = buy * px; cash -= float(paid.sum() * (1 + FEE)); units += buy
        c_now = cls.loc[day].to_numpy(float); ok = np.isfinite(c_now) & (c_now > 0); marks[ok] = c_now[ok]
        units[(units < 1e-12)] = 0.; held &= units > 0
        nav = cash + units @ marks
        rows.append(dict(date=day, nav=nav, turnover=float((paid.sum() + proceeds.sum()) / nav), count=int((units > 0).sum()), fee=float((paid.sum() + proceeds.sum()) * FEE), cash=float(cash / nav)))
    return pd.DataFrame(rows)


def main():
    preregister(); t0 = time.time()
    dates = pd.read_csv(V4/'P0_daily.csv').date.tolist()
    opn = load('open'); cls = load('close'); elig = load('eligible'); st = load('is_st'); ld = load('listed_days'); m1 = load('member_csi1000'); m5 = load('member_csi500')
    codes = list(cls.columns); n = len(codes); lookup = {c: i for i, c in enumerate(codes)}
    pdays = list(cls.index); ppos = {d: i for i, d in enumerate(pdays)}; prior_of = {d: pdays[ppos[d] - 1] for d in dates}
    uni = ((m1 == 1) & (m5 != 1) & (elig == 1) & (st != 1) & (ld >= 120)).fillna(False)
    trad = ((elig == 1) & (st != 1) & opn.notna() & (opn > 0)).fillna(False)
    trad_prior = trad.shift(1).fillna(False)           # prior-session information only
    universe_by_day = {d: uni.loc[d].to_numpy(bool) for d in set(prior_of.values())}
    tradable_by_day = {d: trad_prior.loc[d].to_numpy(bool) for d in dates}
    c5 = pd.read_parquet(C5_PATH); c5 = c5[(c5.segment != 'live') & (c5.bucket == 'other_csi1000')]
    score = {}
    for col in ['score_pct', 'bucket_pct']:
        by = {}
        for d, g in c5.groupby('date'):
            v = np.full(n, np.nan); gg = g[g.code.isin(lookup)]; v[[lookup[c] for c in gg.code]] = gg[col].to_numpy(float); by[d] = v
        score[col] = by
    last = None
    for d in sorted(set(prior_of.values())):
        for col in score:
            if d in score[col]: continue
            score[col][d] = score[col][last] if last else np.full(n, np.nan)
        last = d if d in score['score_pct'] else last
    cov = pd.Series({d: int(np.isfinite(score['score_pct'][prior_of[d]])[universe_by_day[prior_of[d]]].sum()) for d in dates}); print('scored universe per day', cov.describe()[['mean', 'min', 'max']].to_dict(), flush=True)
    historic = pd.read_parquet(ROOT/'outputs/cluster_sync_20261011/teammate_latest_uploads/unpacked/pcf_alpha_materials_20261011/benchmarks/H00905_daily.parquet')[['date', 'close']]
    recent = pd.read_csv(ROOT/'outputs/gru_beta_neutral_20261008/benchmark_sources/H00905_daily.csv')[['date', 'close']]
    for f in (historic, recent): f['date'] = pd.to_datetime(f.date).dt.strftime('%Y-%m-%d')
    bench = pd.concat([historic, recent]).drop_duplicates('date', keep='last').set_index('date').close.astype(float).loc[dates].to_numpy(); pb = pd.read_csv(ROOT/'outputs/gru_beta_neutral_20261008/benchmark_sources/000905_daily.csv').set_index('date').close.loc[dates].to_numpy()
    results = {}; scores = []
    for arm in ['EW'] + list(ARMS):
        t1 = time.time(); sc = score[ARMS[arm]['score']] if arm in ARMS else score['score_pct']
        d = sleeve(arm, dates, prior_of, sc, universe_by_day, tradable_by_day, opn, cls, codes); d.to_csv(DEST/f'{arm}_daily.csv', index=False); results[arm] = d
        rec = dict(arm=arm, net_return=float(d.nav.iloc[-1] / d.nav.iloc[0] - 1), annualized=float((d.nav.iloc[-1] / d.nav.iloc[0]) ** (252 / (len(d) - 1)) - 1), avg_turnover=float(d.turnover.iloc[1:].mean()), avg_count=float(d['count'].mean()), fees=float(d.fee.sum()), avg_cash=float(d.cash.mean()), seconds=round(time.time() - t1))
        st_, _ = V1.paired(d.nav.to_numpy(), bench / bench[0], np.asarray(dates), 8); rec.update({'vsH00905_' + k: v for k, v in st_.items() if k in ('annualized_relative_cagr', 'boot95_low', 'boot95_high', 'tracking_error', 'information_ratio')})
        if arm != 'EW':
            st_, _ = V1.paired(d.nav.to_numpy(), results['EW'].nav.to_numpy(), np.asarray(dates), 8); rec.update({'vsEW_' + k: v for k, v in st_.items() if k in ('annualized_relative_cagr', 'boot95_low', 'boot95_high', 'bonferroni_low', 'bonferroni_high', 'tracking_error', 'information_ratio', 'w50_p10', 'w50_worst', 'w50_win')})
        scores.append(rec); print('SLEEVE', arm, round(rec['annualized'], 4), 'vs EW', round(rec.get('vsEW_annualized_relative_cagr', np.nan), 4), flush=True)
    pd.DataFrame(scores).to_csv(DEST/'sleeve_scorecard.csv', index=False)
    # ---- combinations
    comp = []; yearly = []
    for cname, path in CORES.items():
        core = pd.read_csv(path); assert core.date.tolist() == dates, cname; rc = core.nav.to_numpy(); rc = rc[1:] / rc[:-1] - 1
        share_core = float(core.member_nav_weight.iloc[3:].mean()) if 'member_nav_weight' in core else np.nan
        for arm in ['EW'] + list(ARMS):
            rs = results[arm].nav.to_numpy(); rs = rs[1:] / rs[:-1] - 1
            for s in SHARES:
                nav = np.r_[1., np.cumprod(1 + (1 - s) * rc + s * rs)]
                st_, hv = V1.paired(nav, core.nav.to_numpy() / core.nav.iloc[0], np.asarray(dates), 8)
                stb, _ = V1.paired(nav, bench / bench[0], np.asarray(dates), 8)
                comp.append(dict(core=cname, sleeve=arm, share=s, csi500_share=(1 - s) * share_core, vs_core_cagr=st_['annualized_relative_cagr'], vs_core_low=st_['boot95_low'], vs_core_high=st_['boot95_high'], vs_core_bonf_low=st_['bonferroni_low'], vs_core_bonf_high=st_['bonferroni_high'],
                                 vs_core_te=st_['tracking_error'], vs_core_ir=st_['information_ratio'], w50_p10=st_['w50_p10'], w50_worst=st_['w50_worst'], w50_win=st_['w50_win'], share_2026=st_['share_of_log_gain_2026'],
                                 vs_index_cagr=stb['annualized_relative_cagr'], vs_index_te=stb['tracking_error'], vs_index_ir=stb['information_ratio'], **{k: round(v, 2) for k, v in hv.items()}))
                lg = np.log(nav[1:] / nav[:-1]) - np.log(core.nav.to_numpy()[1:] / core.nav.to_numpy()[:-1]); ser = pd.Series(lg, index=pd.to_datetime(dates[1:]))
                for y, g in ser.groupby(ser.index.year): yearly.append(dict(core=cname, sleeve=arm, share=s, year=int(y), increment_ann_pp=float(g.sum() * 252 / len(g) * 100)))
    C = pd.DataFrame(comp); C.to_csv(DEST/'combo_comparisons.csv', index=False); Y = pd.DataFrame(yearly); Y.to_csv(DEST/'combo_yearly.csv', index=False)
    write(DEST/'run_receipt.json', dict(code_sha256=sha(__file__), c5_sha256=sha(C5_PATH), seconds=round(time.time() - t0)))
    pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40)
    print(pd.DataFrame(scores).round(4).to_string(index=False))
    print(C[(C.share == .15)][['core', 'sleeve', 'share', 'csi500_share', 'vs_core_cagr', 'vs_core_low', 'vs_core_high', 'vs_core_bonf_low', 'vs_core_te', 'vs_core_ir', 'w50_p10', 'w50_worst', 'w50_win', 'share_2026', 'vs_index_cagr']].round(4).to_string(index=False))
    print(Y[Y.share == .15].pivot_table(index=['core', 'sleeve'], columns='year', values='increment_ann_pp').round(2).to_string())


if __name__ == '__main__':
    main()
