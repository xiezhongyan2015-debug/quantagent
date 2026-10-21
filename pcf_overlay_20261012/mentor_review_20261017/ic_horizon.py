"""RankIC of the live-chain signals against labels of different horizons and price bases (导师: 训练标签要和调仓节奏对齐，
可得收益是 VWAP 标签 t+1 -> t+6).  GRU: frozen out-of-sample scores 2025-01 -> 2026-09 (wf2026 = live 'ctrl5', oldrank = teammate
'his5', ten, all15), y4 head, seed mean, cross-sectional z within CSI500 members, raw and EMA(0.2) (the live post-processing minus
the neutralisation).  c5: score_pct 2024-06 -> 2026-09 on the CSI500 bucket, the 300/500/1000 union and the 1000-only bucket,
daily labels from the all-A panel.  ICIR = mean/std of daily RankIC; ICIR_ann = ICIR * sqrt(252).
Output: outputs/mentor_review_20261017/ic_horizon_gru.csv, ic_horizon_c5.csv"""
import numpy as np, pandas as pd
from scipy.stats import rankdata
OUT = 'outputs/mentor_review_20261017'; DS = 'gru_research/artifacts/dataset'; TZ = 'Asia/Shanghai'
H = [1, 2, 3, 5, 10]
def rank_ic_rows(S, L, mask, min_n=100):
    """S, L: (days, n) arrays; mask: bool (days, n). Returns per-day Spearman."""
    out = np.full(S.shape[0], np.nan)
    for i in range(S.shape[0]):
        m = mask[i] & np.isfinite(S[i]) & np.isfinite(L[i])
        if m.sum() < min_n: continue
        out[i] = np.corrcoef(rankdata(S[i, m]), rankdata(L[i, m]))[0, 1]
    return out
def summarize(ic, dates, name, label):
    s = pd.Series(ic, index=dates).dropna(); r = dict(signal=name, label=label, n=len(s), ic_mean=s.mean(), ic_std=s.std(), icir=s.mean() / s.std(), icir_ann=s.mean() / s.std() * np.sqrt(252), ic_pos_share=(s > 0).mean())
    for y in sorted(set(s.index.year)): r[f'ic_{y}'] = s[s.index.year == y].mean()
    return r
def labels_from_daily(open_, close, vwap):
    L = {}
    for h in H:
        L[f'close->close +{h}d'] = close.shift(-h) / close - 1
        L[f'open(t+1)->open(t+1+{h})'] = open_.shift(-1 - h) / open_.shift(-1) - 1
        L[f'vwap(t+1)->vwap(t+1+{h})'] = vwap.shift(-1 - h) / vwap.shift(-1) - 1
    return L
def zscore_rows(S, mask):
    Z = np.full_like(S, np.nan)
    for i in range(S.shape[0]):
        m = mask[i] & np.isfinite(S[i])
        if m.sum() < 50: continue
        x = S[i, m]; Z[i, m] = (x - x.mean()) / (x.std() + 1e-12)
    return Z
def ema_rows(Z, alpha=0.2):
    E = np.full_like(Z, np.nan); prev = np.full(Z.shape[1], np.nan)
    for i in range(Z.shape[0]):
        cur = Z[i]; upd = np.where(np.isfinite(cur), np.where(np.isfinite(prev), alpha * cur + (1 - alpha) * prev, cur), prev); E[i] = upd; prev = upd
    return E
# ---------------- GRU (members, hourly panel) ----------------
times = pd.to_datetime(np.load(f'{DS}/times.npy'), utc=True).tz_convert(TZ); hc = np.load(f'{DS}/hour_complete.npy'); mem = np.load(f'{DS}/members.npy')
oh = np.load(f'{DS}/hour_ohlcv.npy', mmap_mode='r'); dates = times.normalize(); udates = pd.DatetimeIndex(sorted(set(dates)))
pos = {d: np.where(dates == d)[0] for d in udates}
n = oh.shape[1]; D = len(udates); open_ = np.full((D, n), np.nan, np.float32); close = np.full((D, n), np.nan, np.float32); vwap = np.full((D, n), np.nan, np.float32); mem_d = np.zeros((D, n), bool); comp = np.zeros((D, n), bool)
for k, d in enumerate(udates):
    ix = pos[d]
    if len(ix) != 4: continue
    o = np.asarray(oh[ix]); c4 = o[:, :, 3]; v4 = o[:, :, 4]; ok = hc[ix].all(axis=0) & np.isfinite(c4).all(axis=0) & (np.nansum(v4, axis=0) > 0)
    open_[k] = o[0, :, 0]; close[k] = o[-1, :, 3]; vw = np.nansum(c4 * v4, axis=0) / np.where(np.nansum(v4, axis=0) > 0, np.nansum(v4, axis=0), np.nan); vwap[k] = np.where(ok, vw, np.nan); comp[k] = ok; mem_d[k] = mem[ix[-1]]
O, C, V = (pd.DataFrame(x, index=udates) for x in (open_, close, vwap)); L = {k: v.to_numpy() for k, v in labels_from_daily(O, C, V).items()}
sets = {s: np.load(f'outputs/gru_live_extend_20261012/scores_frozen/{s}.npz') for s in ['wf2025', 'wf2026', 'oldrank']}
st = pd.to_datetime(sets['wf2026']['times'], utc=True).tz_convert(TZ); assert all((pd.to_datetime(z['times'], utc=True) == pd.to_datetime(sets['wf2026']['times'], utc=True)).all() for z in sets.values())
sc = {s: np.asarray(z['scores'])[:, :, :, 3].mean(axis=1) for s, z in sets.items()}   # (1680, n) y4 seed mean
rows = []
for slot in [15, 14]:
    sel = np.where(st.hour == slot)[0]; sdates = st[sel].normalize(); di = udates.get_indexer(sdates); assert (di >= 0).all()
    mask = mem_d[di]
    z = {s: zscore_rows(sc[s][sel], mask) for s in sc}
    ens = {'ctrl5(wf2026, live)': z['wf2026'], 'his5(oldrank)': z['oldrank'], 'ten': np.nanmean(np.stack([z['wf2026'], z['oldrank']]), axis=0), 'all15': np.nanmean(np.stack([z['wf2026'], z['oldrank'], z['wf2025']]), axis=0)}
    for name, Z in ens.items():
        for variant, S in [('raw', Z), ('ema0.2', ema_rows(Z))]:
            if slot == 14 and (name not in ('ctrl5(wf2026, live)', 'all15') or variant == 'ema0.2'): continue
            for lab, Lm in L.items():
                rows.append(summarize(rank_ic_rows(S, Lm[di], mask), sdates.tz_localize(None), f'GRU {name} {variant} @{slot}:00', lab))
gru = pd.DataFrame(rows); gru.to_csv(f'{OUT}/ic_horizon_gru.csv', index=False)
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 500)
print('=== GRU, CSI500 members, 2025-01 -> 2026-09 (out-of-sample scores)')
print(gru[gru.label.isin(['close->close +1d', 'open(t+1)->open(t+1+1)', 'vwap(t+1)->vwap(t+1+1)', 'close->close +5d', 'open(t+1)->open(t+1+5)', 'vwap(t+1)->vwap(t+1+5)', 'vwap(t+1)->vwap(t+1+10)'])][['signal', 'label', 'n', 'ic_mean', 'icir', 'icir_ann', 'ic_pos_share', 'ic_2025', 'ic_2026']].round(4).to_string())
# ---------------- c5 (all-A panel) ----------------
P = 'c5_core/data/A_Layer_Live/panel'
c5 = pd.read_parquet('给队友_c5分数_20261011/c5_scores_20240601_20260928.parquet'); c5 = c5[c5.segment != 'live'].copy(); c5['date'] = pd.to_datetime(c5.date.astype(str).str[:10]); c5['sym'] = c5.code.str[-2:] + c5.code.str[:6]
syms = sorted(c5.sym.unique()); cols = [s for s in syms]
pc = pd.read_parquet(f'{P}/close.parquet'); cols = [c for c in cols if c in pc.columns]; pc = pc[cols]
po = pd.read_parquet(f'{P}/open.parquet')[cols]; pv = pd.read_parquet(f'{P}/vwap.parquet')[cols]; ph = pd.read_parquet(f'{P}/high.parquet')[cols]; pl = pd.read_parquet(f'{P}/low.parquet')[cols]
ratio = (pv / pc); print('\nvwap/close ratio quantiles (adjusted check):', ratio.stack().quantile([.001, .01, .5, .99, .999]).round(3).to_dict())
if (ratio.stack().abs().between(0.8, 1.25)).mean() < 0.999:
    print('vwap looks UNADJUSTED -> using adjusted (O+H+L+C)/4 as the VWAP proxy'); pv = (po + ph + pl + pc) / 4
Lc = {k: v for k, v in labels_from_daily(po, pc, pv).items()}
piv = c5.pivot_table(index='date', columns='sym', values='score_pct', aggfunc='first').reindex(columns=cols); bucket = c5.pivot_table(index='date', columns='sym', values='bucket', aggfunc='first').reindex(columns=cols)
cd = piv.index; di = pc.index.get_indexer(cd); ok = di >= 0; cd = cd[ok]; di = di[ok]; S = piv.to_numpy()[ok]; Bk = bucket.to_numpy()[ok]
rows = []
for pool, m in [('csi500 bucket', Bk == 'csi500'), ('300+500+1000 union', np.isfinite(S)), ('other_csi1000 bucket', Bk == 'other_csi1000')]:
    for lab, Lm in Lc.items():
        rows.append(summarize(rank_ic_rows(S, Lm.to_numpy()[di], m), cd, f'c5 score_pct [{pool}]', lab))
c5t = pd.DataFrame(rows); c5t.to_csv(f'{OUT}/ic_horizon_c5.csv', index=False)
print('\n=== c5, 2024-06 -> 2026-09')
print(c5t[c5t.label.isin(['close->close +1d', 'open(t+1)->open(t+1+1)', 'vwap(t+1)->vwap(t+1+1)', 'close->close +5d', 'vwap(t+1)->vwap(t+1+5)', 'close->close +10d', 'vwap(t+1)->vwap(t+1+10)'])][['signal', 'label', 'n', 'ic_mean', 'icir', 'icir_ann', 'ic_pos_share', 'ic_2024', 'ic_2025', 'ic_2026']].round(4).to_string())
