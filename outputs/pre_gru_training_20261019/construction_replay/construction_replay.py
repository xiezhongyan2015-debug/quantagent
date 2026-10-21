"""实盘构造回放（零 GPU，2026-10-19）：Z2（GRU 后处理里 EMA 与中性化的顺序）和 H85（500 成分占净值 >= 85% 硬约束）。

运行（项目根目录）：.pcf-research-venv/bin/python -I outputs/pre_gru_training_20261019/construction_replay/construction_replay.py
只读项目文件，只写本目录。不连云机，不联网。

构造（每个信号日 T，次日开盘执行；与 10-19 实盘相同的环节）：
  PCF 骨架（563030，stock_replicable，T 日 PCF × 估值日收盘 / NAVperCU；去掉 300/500/1000 并集外的票（c5 面板 PIT 成分），
     剩余归一到 cap_fraction 0.949，同 live_targets）
  -> c5 排雷（vetoed，或 excluded 且当日 raw_score 有限，置零）
  -> 同行业回填（申万一级，PIT：iFinD 周度行业表里 asof<=T 的最新一行，与实盘 industry_sw_l1_20260924.json 同源同编码；
     无行业的被剔除权重按比例回填全部剩余）
  -> GRU(all15) z 与 c5 z 取可得均值，w ∝ b(1+0.5·clip(z,±2))，总仓位不变
  -> 去掉 < 0.05% 的票并归一到 0.949（live_targets --min-weight）
  -> [H85 臂：enforce_csi500_floor]
  -> beta 倾斜（P3 协议 + 补充 4：S3x 触发后持有 35 日，c5 面板 60 日 beta 五分组，a=min(.1,.5W_Q1,.5W_Q5)，
     段内止损 -1.0pp（倾斜盈亏按意图 delta×执行日收益、换手按万二×2 计），S3x 不可得或 beta 覆盖 <80% 则不倾斜；
     overlay 语义同 live_targets：w+delta 截 0、去 <0.05%、总量不变）
  -> [H85 臂：倾斜后再 enforce 一次]
  -> 1 万元死区（live 语义：已持有且目标>0 且 |目标-持有|×价 < 1 万元则不动；清仓、新进一律交易；守卫：
     股票仓位/500 占比偏离完整目标 >0.5 个点或低于 0.905/0.805（H85 臂 0.85）时按缺口从大到小逐只释放）
  -> 账本：1000 万，次日开盘价成交，万二/边，现金不足时等比缩买单（optimizer_overlay_v4.simulate 的账本逻辑）。
GRU 后处理三种（Z2）：
  A 现行：各模型 head4 z 均值 -> 成分内中性化（vol 秩、换手秩、log 市值、beta60）-> EMA 0.2 -> 成分内 z
  B 先 EMA 再中性化：均值 -> EMA 0.2（在原始均值上）-> 中性化 -> z
  C A 之后再中性化一次：A 的 EMA 行 -> 当日暴露再投影 -> z（EMA 状态仍是 A 的）
标定闸门：旧框架口径（按比例回填、骨架 .98 上限、1000 元死区、无倾斜）重建 ensemble_replay_v1 的 V5_P1_all15，NAV 须逐日一致。
"""
from pathlib import Path
import json, sys, time
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT / 'timing_research_20261017'))
from scipy.special import ndtri
from scipy.stats import rankdata
from pcf_overlay_20261012.data import OUT, END, symbol
from pcf_overlay_20261012 import replay as R
from pcf_overlay_20261012.optimizer_overlay_v4 import C5_PATH
from pcf_overlay_20261012 import wide_pool_optimizer_v1 as V1
from pcf_overlay_20261012.ensemble_replay import build_signals, FROZEN, V4, TZ
from gru_beta_neutral_20261008.replay import neutralize
import beta_tilt_shadow as BT          # held(), tilt() and constants of the live module, used as is
from constraint85_draft import enforce_csi500_floor

CAPITAL = 10_000_000.; FEE = .0002; CAPF = 0.949; MIN_W = 0.0005; K = .5; DEADBAND = 10_000.
SETS = ['wf2025', 'wf2026', 'oldrank']   # all15
PANEL = ROOT / 'c5_core/data/A_Layer_Live/panel'
DEST = HERE


def dot(c): return c[2:] + '.' + c[:2]


# ------------------------------------------------------------------ GRU signals, three post-processing orders
def gru_variants(symbols):
    DATA = ROOT / 'gru_research/artifacts/dataset'; model_symbols = json.load(open(DATA / 'symbols.json')); lookup = {s: i for i, s in enumerate(symbols)}; mapping = np.array([lookup[s] for s in model_symbols])
    times = np.load(DATA / 'times.npy'); members = np.load(DATA / 'members.npy', mmap_mode='r'); tpos = {int(t): i for i, t in enumerate(times)}
    labels = np.load(DATA / 'labels.npy', mmap_mode='r')
    q = np.load(ROOT / 'outputs/gru_beta_neutral_20261008/risk_context.npz'); qpos = {int(t): i for i, t in enumerate(q['times'])}
    fro = {k: np.load(FROZEN / f'{k}.npz')['scores'][:, :, :, 3].astype(np.float32) for k in SETS}; ft = np.load(FROZEN / f'{SETS[0]}.npz')['times']
    clock = pd.to_datetime(ft, utc=True).tz_convert(TZ); out = {v: {} for v in 'ABC'}; lastA = lastB = None; diag = []
    for i in range(len(ft)):
        if clock[i].hour != 15: continue
        raw = np.mean([R.zscore(fro[k][i, m].astype(float)) for k in SETS for m in range(5)], axis=0); t = int(ft[i]); j = qpos[t]; ti = tpos[t]; mem = np.asarray(members[ti])
        expo = np.stack([q['volatility'][j], q['turnover'][j], q['log_cap'][j], q['beta60'][j]], axis=1); rawm = np.where(mem, raw, np.nan)   # expo stays float32 as in the live chain
        rowA = neutralize(rawm, expo)
        if lastA is not None: rowA = np.where(np.isfinite(rowA) & np.isfinite(lastA), .2 * rowA + .8 * lastA, rowA)
        lastA = rowA.copy()
        emaB = rawm if lastB is None else np.where(np.isfinite(rawm) & np.isfinite(lastB), .2 * rawm + .8 * lastB, rawm); lastB = emaB.copy()
        rows = dict(A=rowA, B=neutralize(np.where(mem, emaB, np.nan), expo), C=neutralize(np.where(mem, rowA, np.nan), expo))
        day = str(clock[i].date()); y = np.asarray(labels[ti, :, 3], float)
        ok = mem & np.isfinite(expo).all(axis=1); vr = rankdata(expo[ok, 0]); tr = rankdata(expo[ok, 1])
        rec = dict(date=day)
        for v, row in rows.items():
            z = R.zscore(np.where(mem, row, np.nan)); full = np.full(len(symbols), np.nan); full[mapping] = z; out[v][day] = full
            zz = z[ok]; f = np.isfinite(zz)
            # Pearson with the neutralisation design columns (vol rank, turnover rank, log cap, beta60), as in diagnose_existing.py
            for c, nm in enumerate(('vol_rank', 'turn_rank', 'logcap', 'beta60')):
                xc = vr if c == 0 else tr if c == 1 else expo[ok, c].astype(float); rec[f'{v}_corr_{nm}'] = np.corrcoef(zz[f], xc[f])[0, 1]
            g = mem & np.isfinite(z) & np.isfinite(y); rec[f'{v}_rankic_y4'] = np.corrcoef(rankdata(z[g]), rankdata(y[g]))[0, 1] if g.sum() >= 100 else np.nan
        zA = out['A'][day]
        for v in 'BC':
            g = np.isfinite(zA) & np.isfinite(out[v][day]); rec[f'{v}_rankcorr_vs_A'] = np.corrcoef(rankdata(zA[g]), rankdata(out[v][day][g]))[0, 1]
        diag.append(rec)
    # risk context mapped to the price universe, for PCF-weighted exposures (standardised over members as in neutralize)
    return out, pd.DataFrame(diag)


def risk_rows(symbols):
    DATA = ROOT / 'gru_research/artifacts/dataset'; model_symbols = json.load(open(DATA / 'symbols.json')); lookup = {s: i for i, s in enumerate(symbols)}; mapping = np.array([lookup[s] for s in model_symbols])
    times = np.load(DATA / 'times.npy'); clock = pd.to_datetime(times, utc=True).tz_convert(TZ); members = np.load(DATA / 'members.npy', mmap_mode='r')
    q = np.load(ROOT / 'outputs/gru_beta_neutral_20261008/risk_context.npz'); qpos = {int(t): i for i, t in enumerate(q['times'])}
    by15 = {str(clock[i].date()): i for i in np.flatnonzero(clock.hour == 15)}
    def X(day):
        ti = by15[day]; j = qpos[int(times[ti])]; mem = np.asarray(members[ti])
        e = np.stack([q['volatility'][j], q['turnover'][j], q['log_cap'][j], q['beta60'][j]], axis=1).astype(float); ok = mem & np.isfinite(e).all(axis=1)
        x = np.full(e.shape, np.nan); xx = e[ok].copy(); xx[:, 0] = rankdata(xx[:, 0]) / len(xx); xx[:, 1] = rankdata(xx[:, 1]) / len(xx)
        mu, sd = xx.mean(axis=0), xx.std(axis=0); allx = e.copy()
        # non-members: rank against the member distribution (so PCF names that left the index still get an exposure)
        for c in (0, 1): allx[:, c] = np.searchsorted(np.sort(e[ok, c]), e[:, c], side='right') / ok.sum()
        x = (allx - mu) / sd; x[~np.isfinite(e).all(axis=1)] = np.nan
        full = np.full((len(symbols), 4), np.nan); full[mapping] = x; return full
    return X, by15, members, mapping


# ------------------------------------------------------------------ industry (PIT 申万一级, same source/codes as the live JSON)
def industry_pit():
    mk = pd.read_parquet(ROOT / '中证500_历年成分股_基本面_2010-2026_iFinD_PIT/weekly_market_and_industry.parquet').dropna(subset=['sw_l1_provider_code'])
    mk['asof_date'] = mk.asof_date.astype(str).str[:10]; mk = mk.sort_values('asof_date'); cur = {}; snaps = {}
    for d, g in mk.groupby('asof_date', sort=True):
        cur.update(dict(zip(g.thscode, g.sw_l1_provider_code.astype(str))))
        if d >= '2024-05-01': snaps[d] = dict(cur)
    keys = sorted(snaps)
    def at(day):
        k = [x for x in keys if x <= day]; return snaps[k[-1]]
    return at


# ------------------------------------------------------------------ beta for the tilt (identical formula to beta_tilt_shadow.betas)
class Betas:
    def __init__(self):
        r = pd.read_parquet(PANEL / 'ret.parquet'); r.index = pd.to_datetime(r.index); r.columns = [dot(c) for c in r.columns]
        idx = pd.read_parquet(ROOT / 'outputs/timing_p2_20261017/index_000905.parquet'); self.bars = idx
        self.m = idx.close.pct_change().reindex(r.index); self.r = r
    def at(self, T, codes):
        R_ = self.r.loc[:T].iloc[-BT.BETA_N:]; m = self.m.reindex(R_.index); cols = [c for c in codes if c in R_.columns]; X = R_[cols].to_numpy(float); mv = m.to_numpy(float)[:, None]
        ok = np.isfinite(X) & np.isfinite(mv); n = ok.sum(axis=0); n1 = np.maximum(n - 1, 1)
        xm = np.where(ok, X, 0).sum(0) / np.maximum(n, 1); mm = np.where(ok, mv, 0).sum(0) / np.maximum(n, 1)
        cov = np.where(ok, (X - xm) * (mv - mm), 0).sum(0) / n1
        okm = np.isfinite(X) & np.isfinite(mv); mm2 = np.where(okm, mv, 0).sum(0) / np.maximum(okm.sum(0), 1); var = np.where(okm, (mv - mm2) ** 2, 0).sum(0) / np.maximum(okm.sum(0) - 1, 1)
        b = pd.Series(cov / var, index=cols); return b[(np.isfinite(X).sum(0) >= BT.BETA_MIN)]


class Union:
    """allowed universe of the live gate: CSI300 | CSI500 | CSI1000 members (c5 panel, last row <= T), as live_targets' --members"""
    def __init__(self):
        ms = [pd.read_parquet(PANEL / f'member_{k}.parquet').fillna(0) > 0 for k in ('csi300', 'csi500', 'csi1000')]
        u = ms[0] | ms[1] | ms[2]; u.index = pd.to_datetime(u.index); u.columns = [dot(c) for c in u.columns]; self.u = u
    def at(self, T):
        row = self.u.loc[:T].iloc[-1]; return set(row.index[row.to_numpy()])


def s3x_series():
    s = pd.read_parquet(ROOT / 'outputs/timing_p2_20261017/s3x/s3x_000905.parquet'); s.index = pd.to_datetime(s.index); return s


# ------------------------------------------------------------------ ledger (v4 simulate + live deadband semantics)
def simulate(dates, W, signal_marks, exec_prices, fill, closes, mem_sig, mem_exec, deadband=DEADBAND, legacy=False, floor_member=0.805):
    T_, U = W.shape; units = np.zeros(U); marks = np.zeros(U); cash = CAPITAL; rows = []
    for t, day in enumerate(dates):
        sig = signal_marks[t]; valid = np.isfinite(sig) & (sig > 0); marks[valid] = sig[valid]; nav = cash + units @ marks
        wfull = W[t]; desired = np.divide(wfull * nav, marks, out=np.zeros(U), where=marks > 0); desired[~valid] = units[~valid]
        released = 0
        if legacy:
            delta = desired - units; delta[np.abs(delta) * marks < 1000] = 0
        else:
            keep = (units > 1e-10) & (desired > 1e-10) & (np.abs(desired - units) * marks < deadband)
            mem = mem_sig[t]; val = lambda q, m=None: float((q * marks)[m].sum() if m is not None else (q * marks).sum())
            full = val(desired) / nav; full_m = val(desired, mem) / nav
            upper = nav * (full + .005); fs = max(.905, full - .005); fm = max(floor_member, full_m - .005)
            for _ in range(int(keep.sum())):
                book = np.where(keep, units, desired); over = val(book) - upper; short = nav * fs - val(book); short_m = nav * fm - val(book, mem)
                if over > 0: cand = keep & (units > desired); gap = (units - desired) * marks
                elif short > 0: cand = keep & (units < desired); gap = (desired - units) * marks
                elif short_m > 0: cand = keep & (units < desired) & mem; gap = (desired - units) * marks
                else: break
                if not cand.any(): break
                i = int(np.argmax(np.where(cand, gap, -np.inf))); keep[i] = False; released += 1
            delta = np.where(keep, 0., desired - units)
        px = exec_prices[t]; can = fill[t] & np.isfinite(px) & (px > 0)
        px = np.where(can, px, marks); sell = np.where(can, np.maximum(-delta, 0), 0)
        proceeds = sell * px; cash += float((proceeds * (1 - FEE)).sum()); units -= sell
        buy = np.where(can, np.maximum(delta, 0), 0); cost = float((buy * px).sum() * (1 + FEE))
        if cost > cash: buy *= max(cash, 0) / cost
        paid = buy * px; cash -= float(paid.sum() * (1 + FEE)); units += buy
        observed = np.isfinite(closes[t]) & (closes[t] > 0); marks[observed] = closes[t, observed]
        equity = units * marks; nav = cash + equity.sum()
        rows.append(dict(date=day, nav=nav / CAPITAL, cash_weight=cash / nav, fee_cny=float((paid.sum() + proceeds.sum()) * FEE),
                         turnover=float((paid.sum() + proceeds.sum()) / nav), holdings=int((units > 1e-10).sum()),
                         max_stock_nav_weight=float(equity.max() / nav), member_nav_weight=float(equity[mem_sig[t]].sum() / nav),
                         member_nav_weight_exec=float(equity[mem_exec[t]].sum() / nav), target_member_weight=float(wfull[mem_sig[t]].sum()),
                         target_member_weight_exec=float(wfull[mem_exec[t]].sum()), target_stock_weight=float(wfull.sum()), deadband_released=released))
    return pd.DataFrame(rows)


def main():
    t0 = time.time()
    pc = pd.read_parquet(OUT / 'pcf_components.parquet'); meta = pd.read_csv(OUT / 'pcf_metadata.csv', dtype={'date': str, 'valuation_date': str}).set_index('date')
    prices = pd.read_parquet(OUT / 'daily_prices.parquet'); symbols = json.load(open(OUT / 'price_symbols.json')); U = len(symbols); lookup = {s: i for i, s in enumerate(symbols)}
    all_days = sorted(set(prices.date)); all_days = [d for d in all_days if '2023-03-01' <= d <= END]; daypos = {d: i for i, d in enumerate(all_days)}
    wide = lambda f: prices.pivot(index='date', columns='symbol', values=f).reindex(index=all_days, columns=symbols).apply(pd.to_numeric, errors='coerce').to_numpy(float)
    rawclose = wide('close'); op = wide('open_adjusted'); close = wide('close_adjusted'); volume = wide('volume'); valid = wide('valid') == 1; isST = wide('isST')
    can_same = valid & (volume > 0) & (isST != 1); can = np.vstack([can_same[:1], can_same[:-1]])
    ret = np.full_like(close, np.nan); ret[1:] = close[1:] / close[:-1] - 1
    sig, diag = gru_variants(symbols); diag.to_csv(DEST / 'z2_signal_diagnostics_daily.csv', index=False); print('signals', round(time.time() - t0), 's', flush=True)
    ref_all15 = build_signals(SETS, symbols)   # the existing replay's all15 chain = variant A
    mx = max(float(np.nanmax(np.abs(sig['A'][d] - ref_all15[d]))) for d in ref_all15); assert mx < 1e-9, f'variant A differs from ensemble_replay all15 by {mx}'
    Xat, by15, frozen_member, mapping = risk_rows(symbols); ind_at = industry_pit(); betas = Betas(); S3 = s3x_series(); union = Union()
    c5 = pd.read_parquet(C5_PATH); c5 = c5[c5.segment != 'live']; s_c5 = {}; veto_c5 = {}; bad_c5 = {}
    for day, g in c5.groupby('date'):
        z = np.full(U, np.nan); v = np.zeros(U, bool); gg = g[g.code.isin(lookup.keys())]; z[[lookup[c] for c in gg.code]] = ndtri(np.clip(gg.score_pct.to_numpy(), 1e-4, 1 - 1e-4))
        bad = gg[(gg.state == 'vetoed') | ((gg.state == 'excluded') & np.isfinite(gg.raw_score.to_numpy(dtype=float)))]; v[[lookup[c] for c in bad.code]] = True; s_c5[day] = z; veto_c5[day] = v; bad_c5[day] = set(bad.code)
    def blend(a, b): return np.where(np.isfinite(a) & np.isfinite(b), (a + b) / 2, np.where(np.isfinite(a), a, b))
    hb = pd.read_parquet(ROOT / 'outputs/cluster_sync_20261011/teammate_latest_uploads/unpacked/pcf_alpha_materials_20261011/benchmarks/H00905_daily.parquet')[['date', 'close']]
    hr = pd.read_csv(ROOT / 'outputs/gru_beta_neutral_20261008/benchmark_sources/H00905_daily.csv')[['date', 'close']]
    for f in (hb, hr): f['date'] = pd.to_datetime(f.date).dt.strftime('%Y-%m-%d')
    H = pd.concat([hb, hr]).drop_duplicates('date', keep='last').set_index('date').close.astype(float)
    # ---------------- events
    v4_dates = pd.read_csv(V4 / 'P0_daily.csv').date.tolist(); pcgroups = {d: g[g.stock_replicable] for d, g in pc.groupby('date')}; last_c5 = None
    ev = []
    for day in v4_dates:
        j = daypos[day]; prior = all_days[j - 1]
        if prior in s_c5: last_c5 = prior
        else: s_c5[prior] = s_c5[last_c5]; veto_c5[prior] = veto_c5[last_c5]; bad_c5[prior] = bad_c5[last_c5]
        if prior not in sig['A']: continue
        g = pcgroups[prior]; m = meta.loc[prior]; codes = [symbol(c) for c in g.code]; ids = np.array([lookup[c] for c in codes]); prev = rawclose[daypos[m.valuation_date], ids]
        raw = pd.Series(g.quantity.to_numpy() * prev / float(m.nav_per_cu), index=codes)
        ms = np.zeros(U, bool); ms[mapping] = frozen_member[by15[prior]]; me = np.zeros(U, bool); me[mapping] = frozen_member[by15[day]] if day in by15 else frozen_member[by15[prior]]
        ev.append(dict(day=day, prior=prior, j=j, raw=raw, ids=ids, ms=ms, me=me))
    dates = [e['day'] for e in ev]; T_ = len(ev); print('events', T_, dates[0], dates[-1], flush=True)
    signal_marks = np.asarray([close[e['j'] - 1] for e in ev]); execp = np.asarray([op[e['j']] for e in ev]); fills = np.asarray([can[e['j']] for e in ev]); closes = np.asarray([close[e['j']] for e in ev])
    mem_sig = np.asarray([e['ms'] for e in ev]); mem_exec = np.asarray([e['me'] for e in ev]); rexec = np.asarray([ret[e['j']] for e in ev])
    sym = np.asarray(symbols)

    def vec(s):
        out = np.zeros(U); out[[lookup[c] for c in s.index]] = s.to_numpy(); return out

    # ---------------- calibration gate: old framework all15 (prorata, .98 cap, 1000 CNY band, no tilt)
    Wc = np.zeros((T_, U))
    for t, e in enumerate(ev):
        ref = vec(e['raw']).copy()
        if ref.sum() > .98: ref *= .98 / ref.sum()
        v5 = ref.copy(); v5[veto_c5[e['prior']]] = 0.; v5 = v5 / v5.sum() * ref.sum()
        Wc[t] = R.target_weights(v5, blend(sig['A'][e['prior']], s_c5[e['prior']]), .5)
    cal = simulate(dates, Wc, signal_marks, execp, fills, closes, mem_sig, mem_exec, legacy=True)
    old = pd.read_csv(OUT / 'ensemble_replay_v1/V5_P1_all15_daily.csv'); assert old.date.tolist() == cal.date.tolist()
    cal_dev = float(np.max(np.abs(old.nav.to_numpy() / cal.nav.to_numpy() - 1))); print('calibration max NAV rel dev', cal_dev, flush=True); assert cal_dev < 1e-9
    cal.to_csv(DEST / 'calibration_V5_P1_all15_daily.csv', index=False)

    # ---------------- live-like arms
    def build_arm(variant, tilt_on, h85=None, h_target=.86, h_floor=.85):
        """h85: None | 'sig' (signal-date membership) | 'exec' (execution-date membership = announced list); h_target: level restored to"""
        W = np.zeros((T_, U)); recs = []; pnl = []; prev_delta = pd.Series(dtype=float)
        for t, e in enumerate(ev):
            T = e['prior']; ind = ind_at(T); allowed = union.at(T); inu = e['raw'].index.isin(list(allowed))
            w0 = e['raw'][inu]; w0 = w0 / w0.sum() * CAPF   # live: names outside the 300/500/1000 union dropped, the rest carries the whole sleeve
            if variant == 'P0':
                w = w0[w0 >= MIN_W]; w = w / w.sum() * CAPF; W[t] = vec(w); recs.append(dict(date=e['day'], dropped_outside_union_names=int((~inu).sum()), dropped_outside_union_weight=float(e['raw'][~inu].sum() / e['raw'].sum()), target_c500_share=float(w[w.index.isin(list(set(sym[e['ms']])))].sum()))); continue
            bad = bad_c5[T]; removed = w0[w0.index.isin(bad)]; keep = w0[~w0.index.isin(bad)].copy(); pooled = 0.; same = 0.
            for code, wt in removed.items():
                gi = ind.get(code); peers = [c for c in keep.index if gi is not None and ind.get(c) == gi]
                if peers and keep[peers].sum() > 0: keep[peers] += wt * keep[peers] / keep[peers].sum(); same += wt
                else: pooled += wt
            if pooled > 0: keep += pooled * keep / keep.sum()
            w = keep / keep.sum() * CAPF
            zfull = blend(sig[variant][T], s_c5[T]); z = pd.Series(zfull[[lookup[c] for c in w.index]], index=w.index).fillna(0.).clip(-2, 2)
            tot = w.sum(); w = w * (1 + K * z); w = w / w.sum() * tot
            w = w[w >= MIN_W]; w = w / w.sum() * CAPF
            c500 = set(sym[e['ms'] if h85 != 'exec' else e['me']]); rec = dict(date=e['day'], refill_same=same, refill_pooled=pooled, removed=float(removed.sum()))
            if h85:
                w, ci = enforce_csi500_floor(w, c500, ind, floor=h_floor, target=h_target); rec.update(h85_applied_pre=ci['applied'], h85_share_before_pre=ci['share_before'], h85_moved_pre=ci['moved'], h85_moved_same_pre=ci['moved_same_industry'])
            b = betas.at(T, list(w.index)); rec['beta_untilted'] = float((w.reindex(b.index) * b).sum() / w.reindex(b.index).sum())
            delta = pd.Series(0., index=w.index); pos = 0
            if tilt_on:
                SS = S3.loc[:T]; pos, start, remaining = BT.held(SS.S3x.fillna(0)); epkey = str(start.date()) if start is not None else ''
                ep_cum = sum(p['net'] for p in pnl if p['episode'] == epkey) if (start is not None) else 0.
                if pos != 0 and ep_cum < BT.STOP: pos = 0; rec['stopped'] = True
                if not np.isfinite(SS.p_x.iloc[-1]): pos = 0
                cov = float(w.reindex(b.index).sum() / w.sum()) if len(b) else 0.
                if pos != 0 and cov < .80: pos = 0
                ws, info = BT.tilt(w, b, pos); d = ws - w
                if pos != 0 and float(d.abs().sum()) > 0:
                    tot = w.sum(); w2 = (w + d).clip(lower=0.); w2 = w2[w2 >= MIN_W]; w2 = w2 / w2.sum() * tot
                    if h85:
                        w2, ci2 = enforce_csi500_floor(w2, c500, ind, floor=h_floor, target=h_target); rec.update(h85_applied_post=ci2['applied'], h85_moved_post=ci2['moved'])
                    delta = w2.reindex(w2.index.union(w.index)).fillna(0) - w.reindex(w2.index.union(w.index)).fillna(0); w = w2
                rec.update(pos=pos, a=info.get('a', 0.), episode=epkey if pos != 0 else '')
                # P&L of the tilt on the execution day (live: delta x close-to-close return of that day, cost doubled)
                rt = pd.Series(rexec[t][[lookup[c] for c in delta.index]], index=delta.index).fillna(0.)
                u = delta.index.union(prev_delta.index); turn = float((delta.reindex(u).fillna(0) - prev_delta.reindex(u).fillna(0)).abs().sum())
                net = float((delta * rt).sum()) - turn * BT.FEE * BT.COST_MULT; pnl.append(dict(date=e['day'], episode=epkey if pos != 0 else '', net=net, pos=pos)); prev_delta = delta
            rec['beta_final'] = float((w.reindex(b.index).fillna(0) * b).sum() / w.reindex(b.index).fillna(0).sum())
            rec['target_c500_share'] = float(w[w.index.isin(c500)].sum()); W[t] = vec(w)
            # PCF-weighted exposures: final target vs PCF skeleton (both normalised over names with risk data)
            X = Xat(T); wf = W[t]; bf = vec(w0); okx = np.isfinite(X).all(axis=1) & ((wf > 0) | (bf > 0))
            wn = wf[okx] / wf[okx].sum(); bn = bf[okx] / bf[okx].sum(); act = (wn - bn) @ X[okx]
            rec.update(act_vol=act[0], act_turn=act[1], act_logcap=act[2], act_beta60=act[3])
            zg = sig[variant][T]; okz = okx & np.isfinite(zg); bz = bf[okz] / bf[okz].sum(); zc = zg[okz] - bz @ zg[okz]; Xc = X[okz] - bz @ X[okz]
            cv = (bz * zc) @ Xc; rec.update(gru_bcov_vol=cv[0], gru_bcov_turn=cv[1], gru_bcov_logcap=cv[2], gru_bcov_beta60=cv[3])
            recs.append(rec)
        return W, pd.DataFrame(recs), pd.DataFrame(pnl)

    ARMS = {'P0': ('P0', False, None), 'A': ('A', True, None), 'B': ('B', True, None), 'C': ('C', True, None),
            'A_nt': ('A', False, None), 'B_nt': ('B', False, None), 'C_nt': ('C', False, None),
            'A_H85': ('A', True, 'sig'), 'A_H85x': ('A', True, 'exec'), 'A_H87x': ('A', True, 'exec', .87), 'A_H86t86x': ('A', True, 'exec', .86, .86), 'A_nt_H85': ('A', False, 'sig')}
    res = {}; recs = {}
    for name, spec in ARMS.items():
        v, tl, h = spec[:3]; W, rc, pn = build_arm(v, tl, h, *spec[3:])
        d = simulate(dates, W, signal_marks, execp, fills, closes, mem_sig, mem_exec, floor_member=.85 if h else .805)
        d.to_csv(DEST / f'arm_{name}_daily.csv', index=False); rc.to_csv(DEST / f'arm_{name}_construction.csv', index=False)
        if len(pn): pn.to_csv(DEST / f'arm_{name}_tilt_pnl.csv', index=False)
        res[name] = d; recs[name] = rc; print('ARM', name, round(float(d.nav.iloc[-1]), 4), round(time.time() - t0), 's', flush=True)

    # ---------------- statistics (2025-01-03 .. last event; the ledger starts on the first 2025 signal)
    sub = np.asarray(dates); bench = H.reindex(dates).to_numpy(float); assert np.isfinite(bench).all()
    idxret = bench[1:] / bench[:-1] - 1
    def arm_stats(name):
        d = res[name]; nav = d.nav.to_numpy(); r = nav[1:] / nav[:-1] - 1; rel = (1 + r) / (1 + idxret)
        p0 = res['P0'].nav.to_numpy(); r0 = p0[1:] / p0[:-1] - 1
        beta = float(np.cov(r, idxret)[0, 1] / np.var(idxret, ddof=1)); rc = recs[name]
        out = dict(arm=name, days=len(r), ann_return=float(np.prod(1 + r) ** (252 / len(r)) - 1), ann_active_vs_H00905=float(np.prod(rel) ** (252 / len(r)) - 1),
                   te_vs_H00905=float(np.std(rel - 1, ddof=1) * np.sqrt(252)), te_vs_P0=float(np.std((1 + r) / (1 + r0) - 1, ddof=1) * np.sqrt(252)),
                   ann_active_vs_P0=float(np.prod((1 + r) / (1 + r0)) ** (252 / len(r)) - 1), realised_beta_vs_H00905=beta,
                   turnover_two_way_mean=float(d.turnover.iloc[1:].mean()), holdings_mean=float(d.holdings.mean()), cash_mean=float(d.cash_weight.mean()))
        for col, tag in (('member_nav_weight', 'sig'), ('member_nav_weight_exec', 'exec')):
            x = d[col].iloc[1:]; out.update({f'c500_nav_{tag}_mean': float(x.mean()), f'c500_nav_{tag}_min': float(x.min()), f'c500_nav_{tag}_p5': float(x.quantile(.05)),
                                             f'c500_nav_{tag}_days_lt85': int((x < .85).sum()), f'c500_nav_{tag}_days_lt80': int((x < .80).sum()), f'c500_nav_{tag}_min_date': str(d.date.iloc[1:][x.idxmin()])})
        out['c500_target_mean'] = float(d.target_member_weight.mean()); out['c500_target_min'] = float(d.target_member_weight.min())
        for c in ('beta_final', 'beta_untilted', 'act_vol', 'act_turn', 'act_logcap', 'act_beta60', 'gru_bcov_vol', 'gru_bcov_turn', 'gru_bcov_logcap', 'gru_bcov_beta60'):
            if c in rc: out[c + '_mean'] = float(rc[c].mean())
        if 'pos' in rc: out['tilt_days_pos'] = int((rc.pos != 0).sum()); out['tilt_days_up'] = int((rc.pos > 0).sum()); out['tilt_stopped_days'] = int(rc.get('stopped', pd.Series(dtype=bool)).fillna(False).sum())
        if 'h85_applied_pre' in rc: out['h85_binding_days'] = int(rc.h85_applied_pre.fillna(False).astype(bool).sum() + 0); out['h85_moved_mean_when_binding'] = float(rc.loc[rc.h85_applied_pre.fillna(False).astype(bool), 'h85_moved_pre'].mean()); out['h85_same_industry_share'] = float(rc.h85_moved_same_pre.sum() / max(rc.h85_moved_pre.sum(), 1e-12))
        return out
    S = pd.DataFrame([arm_stats(k) for k in ARMS]); S.to_csv(DEST / 'arm_summary.csv', index=False)
    PAIRS = [('B', 'A'), ('C', 'A'), ('B_nt', 'A_nt'), ('C_nt', 'A_nt'), ('A', 'A_nt'), ('A', 'P0'), ('A_H85', 'A'), ('A_H85x', 'A'), ('A_H87x', 'A'), ('A_H86t86x', 'A'), ('A_nt_H85', 'A_nt'), ('A_H85', 'P0'), ('A_H85x', 'P0')]
    comp = []
    for x, y in PAIRS:
        nx, ny = res[x].nav.to_numpy(), res[y].nav.to_numpy(); st, _ = V1.paired(nx, ny, sub, 1)
        lg = np.log(nx[1:] / nx[:-1]) - np.log(ny[1:] / ny[:-1]); ser = pd.Series(lg, index=pd.to_datetime(sub[1:])); row = dict(arm=x, versus=y, **{k: st[k] for k in ('days', 'annualized_relative_cagr', 'boot95_low', 'boot95_high', 'tracking_error', 'information_ratio', 'w50_p10', 'w50_worst', 'w50_win')})
        for yy in (2025, 2026):
            k = np.flatnonzero(pd.to_datetime(sub).year == yy); k = k[k > 0] if yy == 2025 else k
            lo = max(k[0] - 1, 0); s2, _ = V1.paired(nx[lo:k[-1] + 1], ny[lo:k[-1] + 1], sub[lo:k[-1] + 1], 1)
            row.update({f'y{yy}_cagr': s2['annualized_relative_cagr'], f'y{yy}_lo': s2['boot95_low'], f'y{yy}_hi': s2['boot95_high'], f'y{yy}_days': s2['days']})
        row.update(arm_turnover=float(res[x].turnover.iloc[1:].mean()), versus_turnover=float(res[y].turnover.iloc[1:].mean())); comp.append(row)
    C = pd.DataFrame(comp); C.to_csv(DEST / 'paired_comparisons.csv', index=False)
    dg = diag[diag.date >= dates[0]].copy(); dg['year'] = dg.date.str[:4]; DG = dg.groupby('year').mean(numeric_only=True); DG.loc['all'] = dg.mean(numeric_only=True); DG.to_csv(DEST / 'z2_signal_diagnostics_by_year.csv')
    from scipy.stats import rankdata as _r  # noqa
    receipt = dict(seconds=round(time.time() - t0), events=T_, first=dates[0], last=dates[-1], calibration_max_nav_rel_dev=cal_dev, variant_A_vs_ensemble_replay_all15_max_abs=mx,
                   capital=CAPITAL, fee=FEE, cap_fraction=CAPF, min_weight=MIN_W, k=K, deadband_cny=DEADBAND, sets=SETS)
    (DEST / 'run_receipt.json').write_text(json.dumps(receipt, indent=1, ensure_ascii=False))
    pd.set_option('display.width', 300); pd.set_option('display.max_columns', 80)
    print(C.round(4).to_string(index=False)); print(S.round(4).T.to_string()); print(DG.round(4).T.to_string())


if __name__ == '__main__':
    main()
