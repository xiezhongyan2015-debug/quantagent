"""Assemble new hourly rows into the frozen feature layout and score them with the frozen T2026 control ensemble.

Reuses: gru_research.minute_data.add_features_and_labels (24 hour features), gru_research.assemble.normalize_cross_section,
gru_market_attention_20261007.prepare_context.market_context, gru_market_attention_20261007.experiment.MarketGRU.
Validation: (A) frozen 2026-09-22/23 rows re-scored here must reproduce test_scores.npz; (B) optional rebuilt overlap rows
(Sina minutes) scored in place of the frozen rows quantify the public-source microstructure noise.
Outputs: outputs/gru_live_extend_20261012/scores/{scores_new.npz, validation.json, panel_ext.npz}
"""
import json, sys, os
from pathlib import Path
import numpy as np, pandas as pd, torch
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
os.environ.setdefault('GRU_DATA', str(ROOT / 'gru_research/artifacts/dataset'))
from gru_research import minute_data as MD
from gru_research.assemble import normalize_cross_section
from gru_market_attention_20261007.prepare_context import market_context
from gru_market_attention_20261007.experiment import MarketGRU
from gru_live_extend_20261012.hourly_extend import read_frozen
from gru_live_extend_20261012.score_frozen_models import uniform_rank, OUR_SEEDS, HIS_SEEDS, FACTOR_COLS
import hashlib
ART = ROOT / 'gru_research/artifacts'; DATASET = ART / 'dataset'; CTX = ROOT / 'gru_market_attention_20261007'
HOURLY = ROOT / 'outputs/gru_live_extend_20261012/hourly'; DAILY = ROOT / 'outputs/gru_live_extend_20261012/daily'
OUT = ROOT / 'outputs/gru_live_extend_20261012/scores'; OUT.mkdir(parents=True, exist_ok=True)
TZ = 'Asia/Shanghai'; TAIL_DAYS = 100; SEEDS = [11, 22, 33, 44, 55]
OLDRANK = ROOT / 'gru_live_extend_20261012/models/oldrank'   # teammate's five seeds (2026-10-15), hashes checked against his manifest
MODEL_SETS = ['wf2026', 'wf2025', 'oldrank']                 # scores_all order: 5 + 5 + 5
CAL = json.loads((ROOT / '实盘日更/config/calendar_2026.json').read_text(encoding='utf-8'))
def next_session(d):
    later = [x for x in CAL if x > d]
    if not later: raise ValueError('exchange calendar has no session after ' + d)
    return later[0]


def local_ns(date, h, m): return int(pd.Timestamp(f'{date} {h:02d}:{m:02d}', tz=TZ).value)


def main(rebuilt_overlap=False):
    symbols = json.loads((DATASET / 'symbols.json').read_text(encoding='utf-8')); U = len(symbols); sidx = {s: i for i, s in enumerate(symbols)}
    meta = json.loads((DATASET / 'meta.json').read_text(encoding='utf-8')); groups = {k: list(v) for k, v in meta['groups'].items()}
    ctx_meta = json.loads((CTX / 'context_manifest.json').read_text(encoding='utf-8')); n_ind = ctx_meta['n_industries']
    times = np.load(DATASET / 'times.npy'); X = np.load(DATASET / 'X.npy', mmap_mode='r'); complete = np.load(DATASET / 'hour_complete.npy', mmap_mode='r')
    members = np.load(DATASET / 'members.npy', mmap_mode='r'); ohlcv = np.load(DATASET / 'hour_ohlcv.npy', mmap_mode='r'); ctx = np.load(CTX / 'context.npz')
    calendar = json.loads((ART / 'minute_calendar.json').read_text(encoding='utf-8'))['dates']
    new_rows = pd.read_parquet(HOURLY / 'new_hourly_rows.parquet'); slow_new = pd.read_parquet(DAILY / 'slow_new_rows.parquet')
    manifest = json.loads((ART / 'slow_manifest.json').read_text(encoding='utf-8')); slow_names = list(manifest['daily16']) + list(manifest['factors']) + list(manifest['fund16']) + list(manifest['ages2'])
    new_dates = sorted(new_rows.date.unique()); tail_dates = calendar[-TAIL_DAYS:]; all_dates = tail_dates + new_dates
    MD.next_known_open_time = lambda last_day: local_ns(next_session(last_day), 9, 31)
    T0 = len(times) - 4 * TAIL_DAYS; times_tail = times[T0:]; times_new = np.array([local_ns(d, h, m) for d in new_dates for h, m in [(10, 30), (11, 30), (14, 0), (15, 0)]], np.int64)
    Tn = len(times_new); assert np.array_equal(times_tail, np.array([local_ns(d, h, m) for d in tail_dates for h, m in [(10, 30), (11, 30), (14, 0), (15, 0)]]))
    # ---- hour features for the new rows (per symbol: frozen tail rows + new rows -> frozen formulas)
    hour_raw = np.full((Tn, U, 24), np.nan, np.float32); comp_new = np.zeros((Tn, U), bool); ohlcv_new = np.full((Tn, U, 10), np.nan, np.float32)
    exec_raw = np.full((Tn, U), np.nan, np.float32); exec_ok = np.zeros((Tn, U), bool); overlap_hour = {}
    rebuilt = pd.read_parquet(HOURLY / 'rebuilt_overlap_rows.parquet') if rebuilt_overlap else None
    ohlcv_cols = ['open', 'high', 'low', 'close', 'volume', 'raw_open', 'raw_high', 'raw_low', 'raw_close', 'adjustment_factor']
    tpos = {int(t): i for i, t in enumerate(times_new)}
    for sym, g in new_rows.groupby('symbol'):
        u = sidx[sym]; frozen = read_frozen(sym, tail_dates)
        if frozen is None: frozen = pd.DataFrame(columns=g.columns)
        if rebuilt is not None:
            rb = rebuilt[rebuilt.symbol == sym]
            if len(rb): frozen = pd.concat([frozen[~frozen.date.isin(rb.date.unique())], rb[frozen.columns.intersection(rb.columns)]], ignore_index=True)
        frame = pd.concat([frozen, g[frozen.columns.intersection(g.columns)]], ignore_index=True).sort_values('signal_time')
        if frame.empty: continue
        f = MD.add_features_and_labels(frame.assign(symbol=sym), all_dates)
        sel = f[f.signal_time.isin(tpos)]
        rows = np.array([tpos[int(t)] for t in sel.signal_time])
        hour_raw[rows, u] = sel[MD.HOUR_FEATURES].to_numpy(np.float32); comp_new[rows, u] = sel.hour_complete.to_numpy(bool)
        ohlcv_new[rows, u] = sel[ohlcv_cols].to_numpy(np.float32); exec_raw[rows, u] = sel.execution_raw_price.to_numpy(np.float32); exec_ok[rows, u] = sel.execution_tradable.to_numpy(bool)
        if rebuilt is not None:
            ov = f[f.date.isin(rebuilt.date.unique())]
            for r in ov.itertuples(): overlap_hour.setdefault(int(r.signal_time), {})[u] = (np.array([getattr(r, c) for c in MD.HOUR_FEATURES], np.float32), bool(r.hour_complete))
    # ---- membership (carry the last frozen session's constituents), slow rows, industry (weekly PIT snapshot strictly before the date)
    mem_last = np.asarray(members[-1]); members_new = np.tile(mem_last, (Tn, 1))
    slow_raw = np.full((len(new_dates), U, len(slow_names)), np.nan, np.float32)
    for r in slow_new.itertuples(index=False):
        if r.date in new_dates: slow_raw[new_dates.index(r.date), sidx[r.symbol]] = np.array(r[2:], np.float32)
    mk = pd.read_parquet(ROOT / '中证500_历年成分股_基本面_2010-2026_iFinD_PIT/weekly_market_and_industry.parquet'); mk['date'] = pd.to_datetime(mk.asof_date).values.astype('datetime64[D]')
    vocab = {k: int(v) for k, v in json.loads(ctx_meta['industry_vocabulary'].replace("'", '"')).items()} if isinstance(ctx_meta['industry_vocabulary'], str) else ctx_meta['industry_vocabulary']
    industry_new = np.zeros((len(new_dates), U), np.int16); dates64 = np.array(new_dates, 'datetime64[D]')
    for s, g in mk.groupby('thscode'):
        if s not in sidx: continue
        g = g.sort_values('date'); gd = g.date.to_numpy().astype('datetime64[D]'); ix = np.searchsorted(gd, dates64, side='left') - 1; ok = ix >= 0
        codes = g.sw_l1_provider_code.map(vocab).fillna(0).to_numpy(np.int16); industry_new[ok, sidx[s]] = codes[ix[ok]]
    industry_new = np.repeat(industry_new, 4, axis=0)
    # ---- normalised X for new rows, extended arrays
    X_new = np.full((Tn, U, X.shape[2]), np.nan, np.float32)
    for i in range(Tn):
        X_new[i, :, :24], _ = normalize_cross_section(hour_raw[i], members_new[i]); X_new[i, :, 24:], _ = normalize_cross_section(slow_raw[i // 4], members_new[i])
    X_ext = np.concatenate([np.asarray(X[T0:]), X_new]); comp_ext = np.concatenate([np.asarray(complete[T0:]), comp_new]); mem_ext = np.concatenate([np.asarray(members[T0:]), members_new])
    cpos = {int(t): i for i, t in enumerate(ctx['times'])}; c0 = cpos[int(times_tail[0])]; assert np.array_equal(ctx['times'][c0:c0 + len(times_tail)], times_tail), 'context.npz must cover the dataset tail'
    ind_ext = np.concatenate([ctx['industry'][c0:c0 + len(times_tail)], industry_new]); ohlcv_ext = np.concatenate([np.asarray(ohlcv[T0:]), ohlcv_new]); times_ext = np.concatenate([times_tail, times_new])
    if rebuilt is not None:   # swap the frozen overlap hours for the rebuilt ones (validation B)
        tix = {int(t): i for i, t in enumerate(times_ext)}
        for t, d in overlap_hour.items():
            i = tix[t]; raw_row = np.full((U, 24), np.nan, np.float32); comp_row = np.asarray(comp_ext[i]).copy()
            for u, (vals, c) in d.items(): raw_row[u] = vals; comp_row[u] = c
            X_ext[i, :, :24], _ = normalize_cross_section(raw_row, mem_ext[i]); comp_ext[i] = comp_row
    market_ext = market_context(ohlcv_ext, mem_ext, comp_ext)
    if rebuilt is None: np.savez_compressed(OUT / 'panel_ext.npz', times=times_ext, close_adj=ohlcv_ext[:, :, 3], members=mem_ext, complete=comp_ext, exec_raw_new=exec_raw, exec_ok_new=exec_ok, times_new=times_new, slow_new=slow_raw, slow_names=np.array(slow_names), new_dates=np.array(new_dates))
    market_check = float(np.nanmax(np.abs(market_ext[40 * 4:len(times_tail)] - ctx['market'][c0 + 40 * 4:c0 + len(times_tail)]))) if rebuilt is None else None
    hours = pd.to_datetime(times_ext, utc=True).tz_convert(TZ).hour; slots = np.array([{10: 0, 11: 1, 14: 2, 15: 3}[int(h)] for h in hours], np.int64)
    bad = np.vstack([np.zeros((1, U), np.int32), np.cumsum(~comp_ext, axis=0, dtype=np.int32)])
    def mask(t): return ((bad[t + 1] - bad[t + 1 - 80]) == 0) & mem_ext[t]
    # ---- scoring: five frozen T2026 control checkpoints (live set, 'scores') + T2025 five + teammate OLD_RANK five ('scores_all')
    torch.set_num_threads(max(2, os.cpu_count() // 2)); models = []; models_all = []
    for seed in SEEDS:
        st = torch.load(ROOT / f'outputs/gru_walkforward_20261010/T2026/control_seed{seed}/best.pt', map_location='cpu', weights_only=False)
        assert st['signature']['variant'] == 'control' and st['signature']['dataset'] == meta_fp(meta)
        m = MarketGRU(groups, n_ind, 'control'); m.load_state_dict(st['model']); m.eval(); models.append(m); models_all.append(('wf2026', seed, m, False))
    for seed in OUR_SEEDS:
        st = torch.load(ROOT / f'outputs/gru_walkforward_20261010/T2025/control_seed{seed}/best.pt', map_location='cpu', weights_only=False)
        assert st['signature']['variant'] == 'control'
        m = MarketGRU(groups, n_ind, 'control'); m.load_state_dict(st['model']); m.eval(); models_all.append(('wf2025', seed, m, False))
    card = json.loads((OLDRANK / 'MODEL_CARD.json').read_text(encoding='utf-8'))
    for seed in HIS_SEEDS:
        f = OLDRANK / f'rank_seed{seed}.pt'; want = card['weights'][str(seed)]['sha256']
        assert hashlib.sha256(f.read_bytes()).hexdigest() == want, f'OLD_RANK checkpoint hash mismatch: seed {seed}'
        m = MarketGRU(groups, n_ind, 'control'); m.load_state_dict(torch.load(f, map_location='cpu', weights_only=True), strict=True); m.eval(); models_all.append(('oldrank', seed, m, True))
    X_rank = X_ext.copy()
    for i in range(len(X_ext)): X_rank[i, :, FACTOR_COLS] = uniform_rank(np.asarray(X_ext[i])[:, FACTOR_COLS].astype(np.float32), np.asarray(mem_ext[i]))
    sel = [t for t in range(len(times_tail) - 8, len(times_ext)) if t >= 79]      # last two frozen sessions (validation) + all new rows
    scores = np.full((len(sel), len(SEEDS), U, 4), np.nan, np.float32); scores_all = np.full((len(sel), len(models_all), U, 4), np.nan, np.float32); counts = []
    with torch.inference_mode():
        for j, t in enumerate(sel):
            mk_ = mask(t); n = int(mk_.sum()); counts.append(n)
            if n < 100: continue
            x = torch.from_numpy(np.ascontiguousarray(X_ext[t - 79:t + 1, mk_].transpose(1, 0, 2))); xr = torch.from_numpy(np.ascontiguousarray(X_rank[t - 79:t + 1, mk_].transpose(1, 0, 2)))
            ind = torch.from_numpy(np.ascontiguousarray(ind_ext[t - 79:t + 1, mk_].T.astype(np.int64)))
            sl = torch.from_numpy(np.ascontiguousarray(np.broadcast_to(slots[t - 79:t + 1], (n, 80)))); mkt = torch.from_numpy(market_ext[[t]].copy())
            for k, (_, _, m, use_rank) in enumerate(models_all): scores_all[j, k, mk_] = m(xr if use_rank else x, ind, sl, mkt, [n]).numpy()
            scores[j] = scores_all[j, :len(SEEDS)]
    sel_times = times_ext[np.array(sel)]
    np.savez_compressed(OUT / ('scores_rebuilt_overlap.npz' if rebuilt is not None else 'scores_new.npz'), scores=scores, times=sel_times, seeds=np.array(SEEDS), counts=np.array(counts), symbols=np.array(symbols),
                        scores_all=scores_all, model_set=np.array([a for a, _, _, _ in models_all]), model_seed=np.array([b for _, b, _, _ in models_all]))
    # ---- validation A: frozen rows must reproduce the exported test scores
    report = dict(mode='rebuilt_overlap' if rebuilt is not None else 'frozen_tail', new_dates=new_dates, counts=dict(zip([str(pd.Timestamp(t, tz='UTC').tz_convert(TZ)) for t in sel_times], counts)), market_context_tail_maxabs_diff=market_check)
    clock = pd.to_datetime(times, utc=True).tz_convert(TZ)
    per = []
    for k, seed in enumerate(SEEDS):
        a = np.load(ROOT / f'outputs/gru_walkforward_20261010/T2026/control_seed{seed}/test_scores.npz'); tt = {int(t): i for i, t in enumerate(a['times'])}
        for j, t in enumerate(sel_times):
            if int(t) in tt:
                ref = a['scores'][tt[int(t)], :, 3]; mine = scores[j, k, :, 3]; ok = np.isfinite(ref) & np.isfinite(mine)
                per.append(dict(seed=seed, time=str(pd.Timestamp(int(t), tz='UTC').tz_convert(TZ)), n=int(ok.sum()), maxabs=float(np.max(np.abs(ref[ok] - mine[ok]))) if ok.any() else None,
                                rank_corr=float(pd.Series(ref[ok]).corr(pd.Series(mine[ok]), method='spearman')) if ok.sum() > 10 else None, n_ref_only=int((np.isfinite(ref) & ~np.isfinite(mine)).sum()), n_mine_only=int((~np.isfinite(ref) & np.isfinite(mine)).sum())))
    report['validation_vs_test_scores'] = per
    ens = []
    for j, t in enumerate(sel_times):
        refs = []
        for seed in SEEDS:
            a = np.load(ROOT / f'outputs/gru_walkforward_20261010/T2026/control_seed{seed}/test_scores.npz'); tt = {int(x): i for i, x in enumerate(a['times'])}
            if int(t) in tt: refs.append(a['scores'][tt[int(t)], :, 3].astype(float))
        if len(refs) != len(SEEDS): continue
        from pcf_overlay_20261012.replay import zscore as _z
        ref = np.mean([_z(r) for r in refs], axis=0); mine = np.mean([_z(scores[j, k, :, 3].astype(float)) for k in range(len(SEEDS))], axis=0); ok = np.isfinite(ref) & np.isfinite(mine)
        ens.append(dict(time=str(pd.Timestamp(int(t), tz='UTC').tz_convert(TZ)), n=int(ok.sum()), ensemble_rank_corr=float(pd.Series(ref[ok]).corr(pd.Series(mine[ok]), method='spearman'))))
    report['ensemble_validation'] = ens
    (OUT / ('validation_rebuilt_overlap.json' if rebuilt is not None else 'validation.json')).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(json.dumps(dict(mode=report['mode'], counts=report['counts'], market_check=market_check, validation=per[:4] + per[-2:]), ensure_ascii=False, default=str))


def meta_fp(meta):
    import hashlib; return hashlib.sha256(json.dumps(meta, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


if __name__ == '__main__':
    main(rebuilt_overlap='--rebuilt-overlap' in sys.argv)
