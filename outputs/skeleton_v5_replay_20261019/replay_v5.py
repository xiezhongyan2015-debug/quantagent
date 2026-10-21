"""骨架 v5 回放（2026-10-19）：K1 零股数行回填、T1 √b 倾斜、BA 取消 beta 倾斜段内止损。协议见 PROTOCOL.json（先冻结后运行）。

运行（项目根目录）：.pcf-research-venv/bin/python -I outputs/skeleton_v5_replay_20261019/replay_v5.py
只读项目文件，只写本目录。不连云机，不联网，不改实盘文件。

基础框架 = outputs/pre_gru_training_20261019/construction_replay/construction_replay.py 的臂 A（现行，无 85% 约束）。
本脚本复用其 risk_rows / industry_pit / Betas / Union / s3x_series / simulate；K1 与 T1 直接调用实盘模块 pcf_overlay_20261012/skeleton_k1.py；
beta 倾斜调用 beta_tilt_shadow.held / tilt。实盘模块的 STOP 已改为 None，本脚本不读 BT.STOP：带止损的臂显式用 -0.010。
"""
from pathlib import Path
import json, sys, time, glob
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[1]
CRDIR = ROOT / 'outputs/pre_gru_training_20261019/construction_replay'
for p in (ROOT, CRDIR, ROOT / 'timing_research_20261017', ROOT / 'pcf_overlay_20261012'): sys.path.insert(0, str(p))
from scipy.special import ndtri
from scipy.optimize import brentq
from pcf_overlay_20261012.data import OUT, END, symbol
from pcf_overlay_20261012.optimizer_overlay_v4 import C5_PATH
from pcf_overlay_20261012 import wide_pool_optimizer_v1 as V1
from pcf_overlay_20261012.ensemble_replay import build_signals, V4
import construction_replay as CRP
import beta_tilt_shadow as BT
import skeleton_k1 as SK1

STOP = -0.010                     # the original live constant (beta_tilt_shadow.py.bak_20261019); used only by arms with the stop
CAPF, MIN_W, K = CRP.CAPF, CRP.MIN_W, CRP.K
HOLDINGS = ROOT / '实盘日更/config/efund_holdings'
IFIND = ROOT / '中证500_历年成分股_日频_2010-2026_iFinD_后复权/bars'
SPEC = ROOT / 'outputs/barra_cne5_local/specific_risk.parquet'
CAL = ('2025-01-02', '2025-06-30')
DEST = HERE
SMALL_NAV = 9_000_000.


def sig3(x): return float(f'{x:.3g}')


def ifind_rawclose():
    fs = sorted(glob.glob(str(IFIND / '202[3456]/*/*.parquet')))
    d = pd.concat([pd.read_parquet(f, columns=['thscode', 'date', 'close', 'avgPrice', 'amount', 'volume']) for f in fs])
    d['date'] = d.date.astype(str).str[:10]; d['raw'] = d.close * (d.amount / d.volume) / d.avgPrice   # 后复权 close x (raw vwap / adjusted vwap)
    d = d[np.isfinite(d.raw) & (d.raw > 0)]
    return d.set_index(['date', 'thscode']).raw


def main():
    t0 = time.time(); log = lambda *a: print(*a, round(time.time() - t0), 's', flush=True)
    pc = pd.read_parquet(OUT / 'pcf_components.parquet'); meta = pd.read_csv(OUT / 'pcf_metadata.csv', dtype={'date': str, 'valuation_date': str}).set_index('date')
    prices = pd.read_parquet(OUT / 'daily_prices.parquet'); symbols = json.load(open(OUT / 'price_symbols.json')); U = len(symbols); lookup = {s: i for i, s in enumerate(symbols)}
    all_days = sorted(set(prices.date)); all_days = [d for d in all_days if '2023-03-01' <= d <= END]; daypos = {d: i for i, d in enumerate(all_days)}
    wide = lambda f: prices.pivot(index='date', columns='symbol', values=f).reindex(index=all_days, columns=symbols).apply(pd.to_numeric, errors='coerce').to_numpy(float)
    rawclose = wide('close'); op = wide('open_adjusted'); close = wide('close_adjusted'); volume = wide('volume'); valid = wide('valid') == 1; isST = wide('isST')
    can_same = valid & (volume > 0) & (isST != 1); can = np.vstack([can_same[:1], can_same[:-1]])
    ret = np.full_like(close, np.nan); ret[1:] = close[1:] / close[:-1] - 1
    ifr = ifind_rawclose(); log('prices')
    sig = build_signals(CRP.SETS, symbols); log('signals', len(sig))
    _, by15, frozen_member, mapping = CRP.risk_rows(symbols); ind_at = CRP.industry_pit(); betas = CRP.Betas(); S3 = CRP.s3x_series(); union = CRP.Union()
    c5 = pd.read_parquet(C5_PATH); c5 = c5[c5.segment != 'live']; s_c5 = {}; bad_c5 = {}
    for day, g in c5.groupby('date'):
        z = np.full(U, np.nan); gg = g[g.code.isin(lookup.keys())]; z[[lookup[c] for c in gg.code]] = ndtri(np.clip(gg.score_pct.to_numpy(), 1e-4, 1 - 1e-4))
        bad = gg[(gg.state == 'vetoed') | ((gg.state == 'excluded') & np.isfinite(gg.raw_score.to_numpy(dtype=float)))]; s_c5[day] = z; bad_c5[day] = set(bad.code)
    def blend(a, b): return np.where(np.isfinite(a) & np.isfinite(b), (a + b) / 2, np.where(np.isfinite(a), a, b))
    hb = pd.read_parquet(ROOT / 'outputs/cluster_sync_20261011/teammate_latest_uploads/unpacked/pcf_alpha_materials_20261011/benchmarks/H00905_daily.parquet')[['date', 'close']]
    hr = pd.read_csv(ROOT / 'outputs/gru_beta_neutral_20261008/benchmark_sources/H00905_daily.csv')[['date', 'close']]
    for f in (hb, hr): f['date'] = pd.to_datetime(f.date).dt.strftime('%Y-%m-%d')
    H = pd.concat([hb, hr]).drop_duplicates('date', keep='last').set_index('date').close.astype(float)
    SKr = pd.read_parquet(SPEC); SKr.index = SKr.index.strftime('%Y-%m-%d')
    pcall = {d: g for d, g in pc.groupby('date')}

    # ---------------------------------------------------------------- skeletons per PCF day (base = stock_replicable; K1 = live K1 rule)
    def price_at(vd, s):
        j = daypos.get(vd); a = rawclose[j, lookup[s]] if (j is not None and s in lookup) else np.nan
        if np.isfinite(a) and a > 0: return a, 'dp'
        b = ifr.get((vd, s), np.nan)
        return (b, 'ifind') if np.isfinite(b) and b > 0 else (np.nan, None)

    def skeletons(d):
        g = pcall[d]; m = meta.loc[d]; vd = m.valuation_date; navcu = float(m.nav_per_cu)
        gb = g[g.stock_replicable]; codes = [symbol(c) for c in gb.code]; ids = np.array([lookup[c] for c in codes])
        base = pd.Series(gb.quantity.to_numpy() * rawclose[daypos[vd], ids] / navcu, index=codes)
        pcf = pd.DataFrame(dict(code6=g.code.astype(str).str.zfill(6).to_numpy(), quantity=g.quantity.to_numpy(float), flag=g.substitution.astype(str).to_numpy()))
        hold, hm = SK1.latest_report(HOLDINGS, d)
        new, info = SK1.backfill(pcf, float(m.creation_unit), hold, hm)
        new = new[new.flag.isin(['1', '3']) | new.backfilled].copy(); new['code'] = new.code6.map(symbol)
        px = [price_at(vd, c) for c in new.code]; new['px'] = [p for p, _ in px]; new['src'] = [s for _, s in px]
        nopx = new.backfilled & ~np.isfinite(new.px)
        assert not (new.backfilled & new.src.eq('ifind') & ~new.code.isin(lookup.keys())).any(), 'backfilled name priced only by iFinD and outside the replay universe'
        kept = new[~nopx]
        assert np.isfinite(kept.px).all(), f'non-backfilled row without price on {d}'
        k1 = pd.Series(kept.quantity.to_numpy() * kept.px.to_numpy() / navcu, index=kept.code.to_numpy())
        assert np.allclose(k1[~kept.backfilled.to_numpy()].reindex(base.index).to_numpy(), base.to_numpy()), f'K1 non-backfilled rows differ from base on {d}'
        rec = dict(zero_rows=int((pcf.quantity == 0).sum()), report=info.get('backfill_report'), backfilled_rows=int(info.get('backfill_rows', 0)),
                   no_price_rows=int(nopx.sum()), no_price_names=','.join(sorted(new.code[nopx])), priced_by_ifind_only=int((kept.backfilled & kept.src.eq('ifind')).sum()))
        return base, k1, set(kept.code[kept.backfilled]), set(new.code[nopx]), rec

    # ---------------------------------------------------------------- events (identical to construction_replay)
    v4_dates = pd.read_csv(V4 / 'P0_daily.csv').date.tolist(); last_c5 = None; ev = []
    for day in v4_dates:
        j = daypos[day]; prior = all_days[j - 1]
        if prior in s_c5: last_c5 = prior
        else: s_c5[prior] = s_c5[last_c5]; bad_c5[prior] = bad_c5[last_c5]
        if prior not in sig: continue
        base, k1, bf, nopx, rec = skeletons(prior)
        ms = np.zeros(U, bool); ms[mapping] = frozen_member[by15[prior]]; me = np.zeros(U, bool); me[mapping] = frozen_member[by15[day]] if day in by15 else frozen_member[by15[prior]]
        ev.append(dict(day=day, prior=prior, j=j, raw=dict(base=base, k1=k1), bf=bf, nopx=nopx, rec=rec, ms=ms, me=me))
    dates = [e['day'] for e in ev]; T_ = len(ev); log('events', T_, dates[0], dates[-1])
    signal_marks = np.asarray([close[e['j'] - 1] for e in ev]); execp = np.asarray([op[e['j']] for e in ev]); fills = np.asarray([can[e['j']] for e in ev]); closes = np.asarray([close[e['j']] for e in ev])
    mem_sig = np.asarray([e['ms'] for e in ev]); mem_exec = np.asarray([e['me'] for e in ev]); rexec = np.asarray([ret[e['j']] for e in ev]); sym = np.asarray(symbols)
    def vec(s):
        out = np.zeros(U); out[[lookup[c] for c in s.index]] = s.to_numpy(); return out

    # ---------------------------------------------------------------- pre-tilt skeleton (union filter -> c5 veto -> same-industry refill), cached per skeleton
    pre = {}
    def pretilt(t, skel):
        if (t, skel) in pre: return pre[(t, skel)]
        e = ev[t]; T = e['prior']; ind = ind_at(T); allowed = union.at(T); raw = e['raw'][skel]; inu = raw.index.isin(list(allowed))
        w0 = raw[inu]; w0 = w0 / w0.sum() * CAPF
        bad = bad_c5[T]; removed = w0[w0.index.isin(bad)]; keep = w0[~w0.index.isin(bad)].copy(); pooled = 0.
        for code, wt in removed.items():
            gi = ind.get(code); peers = [c for c in keep.index if gi is not None and ind.get(c) == gi]
            if peers and keep[peers].sum() > 0: keep[peers] += wt * keep[peers] / keep[peers].sum()
            else: pooled += wt
        if pooled > 0: keep += pooled * keep / keep.sum()
        w = keep / keep.sum() * CAPF
        zfull = blend(sig[T], s_c5[T]); z = pd.Series(zfull[[lookup[c] for c in w.index]], index=w.index).fillna(0.).clip(-2, 2)
        inu_bf = [c for c in e['bf'] if c in set(raw.index[inu])]
        info = dict(outside_union_names=int((~inu).sum()), bf_in_union=len(inu_bf), bf_w_skeleton=float(w0.reindex(inu_bf).sum()) / CAPF, bf_w_postveto=float(w.reindex(inu_bf).fillna(0).sum()) / CAPF,
                    nopx_in_union=int(len([c for c in e['nopx'] if c in allowed])))
        pre[(t, skel)] = (w0, w, z, info); return pre[(t, skel)]

    def sigma_at(T, idx):
        row = SKr.loc[:T].iloc[-1]; s = row.reindex(idx).astype(float); return s.fillna(float(np.nanmedian(row.to_numpy(float)))).to_numpy()
    def mult(w, z): tot = w.sum(); x = w * (1 + K * z); return x / x.sum() * tot
    def te(a, s): return float(np.sqrt(np.sum(a.to_numpy() ** 2 * s ** 2)))

    # ---------------------------------------------------------------- lambda calibration (2025H1, arm-A skeleton)
    def calibrate(skel):
        cal = [t for t, e in enumerate(ev) if CAL[0] <= e['prior'] <= CAL[1]]; data = []
        for t in cal:
            _, w, z, _ = pretilt(t, skel); s = sigma_at(ev[t]['prior'], w.index); data.append((w, z, s))
        te_mult = np.array([te(mult(w, z) - w, s) for w, z, s in data]); target = float(np.median(te_mult))
        f = lambda lam: float(np.median([te(SK1.sqrtb_tilt(w, z, lam) - w, s) for w, z, s in data])) - target
        lam = brentq(f, 1e-5, 0.05, xtol=1e-10)
        return dict(skeleton=skel, days=len(cal), first=ev[cal[0]]['prior'], last=ev[cal[-1]]['prior'], te_mult_median=target, lambda_exact=lam, lambda_3sf=sig3(lam),
                    te_sqrtb_median_at_3sf=f(sig3(lam)) + target, te_mult_mean=float(te_mult.mean()))
    calA = calibrate('base'); calK = calibrate('k1'); LAM = calA['lambda_3sf']; log('lambda', calA, calK)
    json.dump(dict(rule='median TE_sqrtb(lambda) = median TE_mult(k=0.5), specific TE of tilt active weights, 2025-01-02..2025-06-30', used=calA, sensitivity_k1_skeleton=calK),
              open(DEST / 'lambda_calibration.json', 'w'), indent=1, ensure_ascii=False)

    # ---------------------------------------------------------------- arms
    def build_arm(skel, form, stop):
        W = np.zeros((T_, U)); recs = []; pnl = []; prev_delta = pd.Series(dtype=float)
        for t, e in enumerate(ev):
            T = e['prior']; w0, w, z, info = pretilt(t, skel); rec = dict(date=e['day'], **info)
            s = sigma_at(T, w.index); tot = w.sum()
            wt = mult(w, z) if form == 'mult' else SK1.sqrtb_tilt(w, z, LAM)
            a = wt - w; rec.update(tilt_spec_te=te(a, s), tilt_active_share=float(a.abs().sum() / 2), tilt_zeroed=int(((wt <= 0) & (w > 0)).sum()))
            w = wt[wt >= MIN_W]; w = w / w.sum() * CAPF
            b = betas.at(T, list(w.index)); rec['beta_untilted'] = float((w.reindex(b.index) * b).sum() / w.reindex(b.index).sum())
            delta = pd.Series(0., index=w.index)
            SS = S3.loc[:T]; pos, start, remaining = BT.held(SS.S3x.fillna(0)); epkey = str(start.date()) if start is not None else ''
            ep_cum = sum(p['net'] for p in pnl if p['episode'] == epkey) if (start is not None) else 0.
            rec['ep_cum'] = ep_cum
            if stop and pos != 0 and ep_cum < STOP: pos = 0; rec['stopped'] = True
            if not np.isfinite(SS.p_x.iloc[-1]): pos = 0
            cov = float(w.reindex(b.index).sum() / w.sum()) if len(b) else 0.
            if pos != 0 and cov < .80: pos = 0
            ws, tinfo = BT.tilt(w, b, pos); d = ws - w
            if pos != 0 and float(d.abs().sum()) > 0:
                tt = w.sum(); w2 = (w + d).clip(lower=0.); w2 = w2[w2 >= MIN_W]; w2 = w2 / w2.sum() * tt
                delta = w2.reindex(w2.index.union(w.index)).fillna(0) - w.reindex(w2.index.union(w.index)).fillna(0); w = w2
            rec.update(pos=pos, a=tinfo.get('a', 0.), episode=epkey if pos != 0 else '')
            rt = pd.Series(rexec[t][[lookup[c] for c in delta.index]], index=delta.index).fillna(0.)
            u = delta.index.union(prev_delta.index); turn = float((delta.reindex(u).fillna(0) - prev_delta.reindex(u).fillna(0)).abs().sum())
            net = float((delta * rt).sum()) - turn * BT.FEE * BT.COST_MULT; pnl.append(dict(date=e['day'], episode=epkey if pos != 0 else '', net=net, pos=pos)); prev_delta = delta
            rec['beta_final'] = float((w.reindex(b.index).fillna(0) * b).sum() / w.reindex(b.index).fillna(0).sum())
            rec['target_c500_share'] = float(w[w.index.isin(set(sym[e['ms']]))].sum()); rec['bf_w_final'] = float(w.reindex(list(e['bf'])).fillna(0).sum())
            rec['names'] = int(len(w)); W[t] = vec(w); recs.append(rec)
        return W, pd.DataFrame(recs), pd.DataFrame(pnl)

    ARMS = {'A': ('base', 'mult', True), 'K1': ('k1', 'mult', True), 'T1': ('base', 'sqrtb', True), 'BA': ('base', 'mult', False),
            'K1_T1': ('k1', 'sqrtb', True), 'K1_T1_BA': ('k1', 'sqrtb', False)}
    res = {}; recs = {}; Ws = {}
    for name, (skel, form, stop) in ARMS.items():
        W, rc, pn = build_arm(skel, form, stop)
        d = CRP.simulate(dates, W, signal_marks, execp, fills, closes, mem_sig, mem_exec)
        d.to_csv(DEST / f'arm_{name}_daily.csv', index=False); rc.to_csv(DEST / f'arm_{name}_construction.csv', index=False); pn.to_csv(DEST / f'arm_{name}_tilt_pnl.csv', index=False)
        res[name] = d; recs[name] = rc; Ws[name] = W; log('ARM', name, round(float(d.nav.iloc[-1]), 5))
        if name == 'A':
            ref = pd.read_csv(CRDIR / 'arm_A_daily.csv'); assert ref.date.tolist() == d.date.tolist()
            gate = float(np.max(np.abs(ref.nav.to_numpy() / d.nav.to_numpy() - 1))); log('reproduction gate: max rel NAV dev vs construction_replay arm A', gate); assert gate < 1e-9, gate

    # ---------------------------------------------------------------- statistics
    sub = np.asarray(dates); bench = H.reindex(dates).to_numpy(float); assert np.isfinite(bench).all(); idxret = bench[1:] / bench[:-1] - 1
    def worst20(nx, ny):
        rel = np.r_[1., np.cumprod((nx[1:] / nx[:-1]) / (ny[1:] / ny[:-1]))]; r = rel[20:] / rel[:-20] - 1; i = int(np.argmin(r)); return float(r[i]), str(sub[i + 20])
    rows = []
    for name in ARMS:
        d = res[name]; rc = recs[name]; nav = d.nav.to_numpy(); r = nav[1:] / nav[:-1] - 1; rel = (1 + r) / (1 + idxret); nA = res['A'].nav.to_numpy()
        x = d.member_nav_weight.iloc[1:]; xe = d.member_nav_weight_exec.iloc[1:]
        row = dict(arm=name, final_nav=float(nav[-1]), ann_return=float(np.prod(1 + r) ** (252 / len(r)) - 1), ann_active_vs_H00905=float(np.prod(rel) ** (252 / len(r)) - 1),
                   te_vs_H00905=float(np.std(rel - 1, ddof=1) * np.sqrt(252)), realised_beta_vs_H00905=float(np.cov(r, idxret)[0, 1] / np.var(idxret, ddof=1)),
                   target_beta_final_mean=float(rc.beta_final.mean()), target_beta_untilted_mean=float(rc.beta_untilted.mean()),
                   turnover_two_way_mean=float(d.turnover.iloc[1:].mean()), holdings_mean=float(d.holdings.mean()), cash_mean=float(d.cash_weight.mean()),
                   c500_nav_mean=float(x.mean()), c500_nav_min=float(x.min()), c500_nav_min_date=str(d.date.iloc[1:][x.idxmin()]), c500_nav_days_lt80=int((x < .80).sum()), c500_nav_days_lt85=int((x < .85).sum()),
                   c500_nav_exec_mean=float(xe.mean()), c500_nav_exec_min=float(xe.min()), c500_nav_exec_days_lt80=int((xe < .80).sum()), c500_nav_exec_days_lt85=int((xe < .85).sum()),
                   c500_target_mean=float(rc.target_c500_share.mean()), tilt_spec_te_median=float(rc.tilt_spec_te.median()), tilt_spec_te_mean=float(rc.tilt_spec_te.mean()),
                   tilt_active_share_mean=float(rc.tilt_active_share.mean()), beta_tilt_days_on=int((rc.pos != 0).sum()), beta_tilt_days_up=int((rc.pos > 0).sum()), beta_tilt_days_down=int((rc.pos < 0).sum()),
                   beta_tilt_stopped_days=int(rc.get('stopped', pd.Series(dtype=bool)).fillna(False).astype(bool).sum()), names_mean=float(rc.names.mean()),
                   bf_w_final_mean=float(rc.bf_w_final.mean()))
        row['worst20_vs_H00905'], row['worst20_vs_H00905_end'] = worst20(nav, bench)
        if name != 'A':
            st, _ = V1.paired(nav, nA, sub, 1); row.update(vs_A_ann=st['annualized_relative_cagr'], vs_A_lo=st['boot95_low'], vs_A_hi=st['boot95_high'], vs_A_te=st['tracking_error'], vs_A_ir=st['information_ratio'])
            for yy in (2025, 2026):
                k = np.flatnonzero(pd.to_datetime(sub).year == yy); k = k[k > 0] if yy == 2025 else k; lo = max(k[0] - 1, 0)
                s2, _ = V1.paired(nav[lo:k[-1] + 1], nA[lo:k[-1] + 1], sub[lo:k[-1] + 1], 1); row.update({f'vs_A_{yy}': s2['annualized_relative_cagr'], f'vs_A_{yy}_lo': s2['boot95_low'], f'vs_A_{yy}_hi': s2['boot95_high']})
            row['worst20_vs_A'], row['worst20_vs_A_end'] = worst20(nav, nA)
        rows.append(row)
    S = pd.DataFrame(rows); S.to_csv(DEST / 'arm_summary.csv', index=False)
    # pairwise increments along the stack (each change given the others)
    PAIRS = [('K1', 'A'), ('T1', 'A'), ('BA', 'A'), ('K1_T1', 'A'), ('K1_T1_BA', 'A'), ('K1_T1', 'K1'), ('K1_T1', 'T1'), ('K1_T1_BA', 'K1_T1')]
    comp = []
    for x_, y_ in PAIRS:
        nx, ny = res[x_].nav.to_numpy(), res[y_].nav.to_numpy(); st, halves = V1.paired(nx, ny, sub, 1)
        row = dict(arm=x_, versus=y_, **{k: st[k] for k in ('days', 'annualized_relative_cagr', 'boot95_low', 'boot95_high', 'tracking_error', 'information_ratio')}, **{f'half_{k}_pct': v for k, v in halves.items()})
        for yy in (2025, 2026):
            k = np.flatnonzero(pd.to_datetime(sub).year == yy); k = k[k > 0] if yy == 2025 else k; lo = max(k[0] - 1, 0)
            s2, _ = V1.paired(nx[lo:k[-1] + 1], ny[lo:k[-1] + 1], sub[lo:k[-1] + 1], 1); row.update({f'y{yy}': s2['annualized_relative_cagr'], f'y{yy}_lo': s2['boot95_low'], f'y{yy}_hi': s2['boot95_high']})
        row['worst20'], row['worst20_end'] = worst20(nx, ny); row['turnover_arm'] = float(res[x_].turnover.iloc[1:].mean()); row['turnover_versus'] = float(res[y_].turnover.iloc[1:].mean()); comp.append(row)
    C = pd.DataFrame(comp); C.to_csv(DEST / 'paired_comparisons.csv', index=False)

    # ---------------------------------------------------------------- K1 diagnostics
    kd = pd.DataFrame([dict(date=e['day'], signal_day=e['prior'], **e['rec']) for e in ev])
    rk = recs['K1_T1_BA'][['date', 'bf_in_union', 'bf_w_skeleton', 'bf_w_postveto', 'nopx_in_union', 'outside_union_names', 'bf_w_final']]
    ra = recs['A'][['date', 'outside_union_names']].rename(columns={'outside_union_names': 'outside_union_names_base'})
    kd = kd.merge(rk, on='date').merge(ra, on='date'); kd.to_csv(DEST / 'k1_backfill_daily.csv', index=False)
    cols = ['zero_rows', 'backfilled_rows', 'no_price_rows', 'bf_in_union', 'nopx_in_union', 'bf_w_skeleton', 'bf_w_postveto', 'bf_w_final']
    KD = kd[cols].describe(percentiles=[.05, .25, .5, .75, .95]).T; KD['year2025_mean'] = kd[kd.date < '2026'][cols].mean(); KD['year2026_mean'] = kd[kd.date >= '2026'][cols].mean()
    KD.to_csv(DEST / 'k1_backfill_distribution.csv')
    nop = pd.Series([c for s in kd.no_price_names for c in s.split(',') if c]).value_counts().rename('days_no_price'); nop.to_csv(DEST / 'k1_no_price_names.csv')
    log('k1 diag')

    # PCF replica (no signal) vs 563030 official NAV, before / after backfill
    navf = pd.read_csv(OUT / 'fund_nav_official/independent_daily_nav.csv', dtype={'date': str}).set_index('date').reinvested_wealth
    rep = []
    for dd in sorted(pcall):
        m = meta.loc[dd]; vd = m.valuation_date
        if dd < '2023-08-31' or dd > END or dd not in daypos or vd not in daypos or dd not in navf.index or vd not in navf.index: continue
        base, k1, bf, nopx, rec = skeletons(dd); jd, jv = daypos[dd], daypos[vd]
        rr = lambda s: np.nan_to_num(close[jd, [lookup[c] for c in s.index]] / close[jv, [lookup[c] for c in s.index]] - 1)
        k1s = k1 * base.sum() / k1.sum()
        rep.append(dict(date=dd, fund=float(navf[dd] / navf[vd] - 1), pre=float((base * rr(base)).sum()), post=float((k1s * rr(k1s)).sum()),
                        post_unscaled_sum=float(k1.sum()), pre_sum=float(base.sum()), bf_share=float(k1.reindex(list(bf)).sum() / k1.sum()), report=rec['report'],
                        zero_rows=rec['zero_rows'], backfilled_rows=rec['backfilled_rows'] - rec['no_price_rows']))
    RP = pd.DataFrame(rep); RP.to_csv(DEST / 'k1_pcf_replica_vs_nav_daily.csv', index=False)
    def gapstats(g, tag):
        out = dict(window=tag, days=len(g), pre_sum_mean=float(g.pre_sum.mean()), bf_share_mean=float(g.bf_share.mean()))
        for k in ('pre', 'post'):
            x = g[k] - g.fund; out[f'{k}_mean_gap_ann'] = float(x.mean() * 252); out[f'{k}_te_ann'] = float(x.std(ddof=1) * np.sqrt(252)); out[f'{k}_mad_daily_bp'] = float(x.abs().mean() * 1e4)
            out[f'{k}_corr'] = float(np.corrcoef(g[k], g.fund)[0, 1])
            out[f'{k}_cum_gap'] = float(np.prod(1 + g[k]) / np.prod(1 + g.fund) - 1)
        return out
    RPS = [gapstats(RP, 'all_2023-08-31_on'), gapstats(RP[(RP.date >= '2025-01-02') & (RP.date <= '2026-09-17')], 'replay_window')]
    for yy in sorted(set(RP.date.str[:4])): RPS.append(gapstats(RP[RP.date.str[:4] == yy], yy))
    RPS = pd.DataFrame(RPS); RPS.to_csv(DEST / 'k1_pcf_replica_vs_nav_summary.csv', index=False)
    # secondary: replica weights vs report full holdings at each period end (point in time backfill)
    wr = []
    for j_ in sorted(HOLDINGS.glob('holdings_*.json')):
        hm = json.loads(j_.read_text(encoding='utf-8')); P = hm['period_end']; hold = pd.read_csv(j_.with_suffix('.csv'), dtype={'code6': str})
        cand = [dd for dd in sorted(pcall) if meta.loc[dd].valuation_date >= P]
        if not cand: continue
        dd = cand[0]; base, k1, bf, _, rec = skeletons(dd)
        rw = pd.Series(hold.pct_nav.to_numpy() / 100, index=hold.code6.map(symbol)); rw = rw / rw.sum()
        def hl1(s): s = s / s.sum(); u = s.index.union(rw.index); return float((s.reindex(u).fillna(0) - rw.reindex(u).fillna(0)).abs().sum() / 2)
        wr.append(dict(period_end=P, pcf_date=dd, report_used_for_backfill=rec['report'], names_report=len(rw), names_pre=len(base), names_post=len(k1), zero_rows=rec['zero_rows'],
                       backfilled=len(bf), half_l1_pre=hl1(base), half_l1_post=hl1(k1),
                       report_weight_of_zero_rows=float(rw.reindex([symbol(c) for c in pcall[dd].code[pcall[dd].quantity == 0]]).fillna(0).sum())))
    pd.DataFrame(wr).to_csv(DEST / 'k1_replica_vs_report_weights.csv', index=False)
    log('replica')

    # STAR 200-share minimum: console lot rounding on K1_T1_BA targets at ~9,000,000 CNY
    W = Ws['K1_T1_BA']; lot = []
    for t, e in enumerate(ev):
        T = e['prior']; jT = daypos[T]; w = W[t]; h = np.flatnonzero(w > 0); p = rawclose[jT, h]; s = sym[h]; ok = np.isfinite(p) & (p > 0)
        want = SMALL_NAV * w[h] / np.where(ok, p, np.nan); qty = np.floor(want / 100) * 100; star = np.char.startswith(s.astype(str), '688'); qty[star & (qty < 200)] = 0
        near = np.round(want / 100) * 100; near[star & (want < 100)] = 0; near[star & (want >= 100) & (near < 200)] = 200
        err = w[h] - np.nan_to_num(qty * p / SMALL_NAV); errn = np.abs(w[h] - np.nan_to_num(near * p / SMALL_NAV)); isbf = np.isin(s, list(e['bf']))
        lot.append(dict(date=e['day'], names=len(h), bf_names=int(isbf.sum()), bf_star=int((isbf & star).sum()), bf_weight=float(w[h][isbf].sum()),
                        bf_star_below200=int((isbf & star & (want < 200)).sum()), bf_weight_lost_floor=float(err[isbf].sum()), bf_weight_kept_floor=float(np.nan_to_num(qty * p / SMALL_NAV)[isbf].sum()),
                        bf_abs_err_nearest=float(errn[isbf].sum()), other_star_below200=int((~isbf & star & (want < 200)).sum()), other_weight_lost_floor=float(err[~isbf].sum()),
                        all_weight_lost_floor=float(err.sum()), all_abs_err_nearest=float(errn.sum()), star_weight_lost_floor=float(err[star].sum()),
                        bf_min_lot_value_cny_median=float(np.median(np.where(star, 200, 100)[isbf] * p[isbf])) if isbf.any() else np.nan))
    L = pd.DataFrame(lot); L.to_csv(DEST / 'k1_star_lot_error_daily.csv', index=False)
    L.describe(percentiles=[.05, .5, .95]).T.to_csv(DEST / 'k1_star_lot_error_summary.csv')

    receipt = dict(seconds=round(time.time() - t0), events=T_, first=dates[0], last=dates[-1], lambda_used=LAM, lambda_calibration=calA, lambda_k1_skeleton_sensitivity=calK,
                   stop_used_for_stop_arms=STOP, bt_stop_in_live_module=BT.STOP, reproduction_gate='arm A NAV vs construction_replay arm_A_daily.csv < 1e-9 (asserted)',
                   capital=CRP.CAPITAL, fee=CRP.FEE, cap_fraction=CAPF, min_weight=MIN_W, k=K, deadband_cny=CRP.DEADBAND, sets=CRP.SETS)
    (DEST / 'run_receipt.json').write_text(json.dumps(receipt, indent=1, ensure_ascii=False))
    pd.set_option('display.width', 300); pd.set_option('display.max_columns', 80)
    print(S.round(4).T.to_string()); print(C.round(4).T.to_string()); print(KD.round(4).to_string()); print(RPS.round(4).T.to_string()); print(pd.DataFrame(wr).round(4).to_string()); print(L.describe().round(4).T.to_string())


if __name__ == '__main__':
    main()
