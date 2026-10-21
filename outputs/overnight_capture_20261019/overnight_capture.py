"""隔夜跳空捕获回放（零 GPU，2026-10-19）。协议见同目录 PROTOCOL.json（先冻结、后运行）。

问题：把每天本来就要做的调仓从 T+1 开盘提前到 T 日收盘集合竞价成交，能拿回多少隔夜跳空收益，扣费后是否为正。
运行（项目根目录）：.pcf-research-venv/bin/python -I outputs/overnight_capture_20261019/overnight_capture.py
只读项目文件，只写本目录。不连云机，不联网。

构造完全复用 construction_replay.py 的臂 A（同一套 helper：risk_rows / industry_pit / Betas / Union / s3x_series、
c5 排雷与同行业回填、w∝b(1+0.5z)、beta 倾斜、1 万元死区）。这里的改动只有：
  - 臂的输入日期（GRU 时点 15/14、c5/S3x/beta/止损用 T 还是 T−1）；
  - 账本：开盘或收盘成交、涨跌停检查、买卖分开的费率、收盘被挡单挪到次日开盘；
  - 复权价：iFinD 后复权优先、baostock 补缺的“混合链”（单段不混源）。
标定闸门：baostock 价 + 万二/边 + 无涨跌停检查 + 开盘执行，BASE 的 NAV 必须与 construction_replay/arm_A_daily.csv 逐日一致。
"""
from pathlib import Path
import bisect, glob, hashlib, json, sys, time
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[1]
CR = ROOT / 'outputs/pre_gru_training_20261019/construction_replay'
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(CR)); sys.path.insert(0, str(ROOT / 'timing_research_20261017'))
from scipy.special import ndtri
from pcf_overlay_20261012.data import OUT, END, symbol
from pcf_overlay_20261012.optimizer_overlay_v4 import C5_PATH
from pcf_overlay_20261012 import wide_pool_optimizer_v1 as V1
from pcf_overlay_20261012.ensemble_replay import build_signals, V4
import beta_tilt_shadow as BT
import construction_replay as CRM   # helpers reused as is (its main() is not run)

CAPITAL = CRM.CAPITAL; CAPF = CRM.CAPF; MIN_W = CRM.MIN_W; K = CRM.K; DEADBAND = CRM.DEADBAND
SETS = CRM.SETS
IFIND = ROOT / '中证500_历年成分股_日频_2010-2026_iFinD_后复权/bars'
DEST = HERE
COM = .0002; STAMP = .0005; IMPACT = .0005
COSTS = {   # (open_buy, open_sell, close_buy, close_sell)
    'main': (COM, COM + STAMP, COM, COM + STAMP),
    'stress_close_impact': (COM, COM + STAMP, COM + IMPACT, COM + STAMP + IMPACT),
    'stress_all_impact': (COM + IMPACT, COM + STAMP + IMPACT, COM + IMPACT, COM + STAMP + IMPACT),
    'alt_competition': (COM, COM, COM, COM),
}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ------------------------------------------------------------------ prices
def hybrid_prices(all_days, symbols, op_bs, cl_bs):
    """iFinD CPS:3 hfq ratios for open->close and close->next-open segments, baostock adjusted where iFinD lacks an end.
    Same NaN pattern as baostock; anchored at each stock's first baostock price."""
    D, U = op_bs.shape; dpos = {d: i for i, d in enumerate(all_days)}; spos = {s: i for i, s in enumerate(symbols)}
    O = np.full((D, U), np.nan); C = O.copy(); months = sorted({d[:7] for d in all_days}); nfiles = 0
    for f in sorted(glob.glob(str(IFIND / '*/*/*.parquet'))):
        if Path(f).parent.name not in months: continue
        d = pd.read_parquet(f, columns=['thscode', 'date', 'open', 'close']); d = d[d.date.isin(dpos) & d.thscode.isin(spos)]; nfiles += 1
        O[d.date.map(dpos).to_numpy(), d.thscode.map(spos).to_numpy()] = d.open.to_numpy(float)
        C[d.date.map(dpos).to_numpy(), d.thscode.map(spos).to_numpy()] = d.close.to_numpy(float)
    O[~(O > 0)] = np.nan; C[~(C > 0)] = np.nan
    pb = np.empty((2 * D, U)); pb[0::2] = op_bs; pb[1::2] = cl_bs; pb[~(pb > 0)] = np.nan
    pi = np.empty((2 * D, U)); pi[0::2] = O; pi[1::2] = C
    ph = np.full_like(pb, np.nan); n_if = n_bs = 0
    for u in range(U):
        k = np.flatnonzero(np.isfinite(pb[:, u]))
        if len(k) == 0: continue
        rb = pb[k[1:], u] / pb[k[:-1], u]; ri = pi[k[1:], u] / pi[k[:-1], u]; use = np.isfinite(ri)
        r = np.where(use, ri, rb); n_if += int(use.sum()); n_bs += int((~use).sum())
        ph[k, u] = pb[k[0], u] * np.r_[1., np.cumprod(r)]
    info = dict(ifind_files=nfiles, segments_from_ifind=n_if, segments_from_baostock=n_bs, ifind_share=n_if / max(n_if + n_bs, 1))
    return ph[0::2], ph[1::2], info


def limit_flags(prices, all_days, symbols, close_h):
    """close / open at the up- or down-limit, from baostock raw prices; ex-right preclose from baostock preclose or the hybrid return."""
    wide = lambda f: prices.pivot(index='date', columns='symbol', values=f).reindex(index=all_days, columns=symbols).apply(pd.to_numeric, errors='coerce').to_numpy(float)
    o, c, pc, st = wide('open'), wide('close'), wide('preclose'), wide('isST')
    prev = pd.DataFrame(close_h).ffill().shift(1).to_numpy(); rh = close_h / prev - 1
    pc_ex = np.where(np.isfinite(pc) & (pc > 0), pc, c / (1 + rh))
    rate = np.array([.30 if s.endswith('.BJ') else .20 if s[:3] in ('300', '301', '688', '689') else .10 for s in symbols])
    R_ = np.broadcast_to(rate, c.shape).copy(); R_[(st == 1) & (R_ == .10)] = .05
    up = np.floor(pc_ex * (1 + R_) * 100 + .5 + 1e-9) / 100; dn = np.floor(pc_ex * (1 - R_) * 100 + .5 + 1e-9) / 100
    cu = np.isfinite(c) & np.isfinite(up) & (c >= up - .005); cd = np.isfinite(c) & np.isfinite(dn) & (c <= dn + .005)
    ou = np.isfinite(o) & np.isfinite(up) & (o >= up - .005); od = np.isfinite(o) & np.isfinite(dn) & (o <= dn + .005)
    pct = wide('pctChg'); chk = np.isfinite(pct) & (R_ == .10) & np.isfinite(pc)
    sanity = dict(close_up_rate=float(cu[np.isfinite(c)].mean()), close_dn_rate=float(cd[np.isfinite(c)].mean()), open_up_rate=float(ou[np.isfinite(o)].mean()), open_dn_rate=float(od[np.isfinite(o)].mean()),
                  agree_pctchg_ge_9p9_main_board=float(((pct >= 9.9) == cu)[chk].mean()), n_check=int(chk.sum()))
    return cu, cd, ou, od, sanity


# ------------------------------------------------------------------ ledger
def ledger(dates, W, P, mode, fees, mem_sig, mem_exec, limits=True, floor_member=.805):
    """construction_replay.simulate generalised. mode 'open': trade at P['open'][t] (= open of execution day j).
    mode 'close': trade at P['sigclose'][t] (= close of signal day T = j-1); legs blocked there are carried to open j.
    Marks at close j afterwards. fees = (open_buy, open_sell, close_buy, close_sell)."""
    ob, os_, cb, cs = fees
    T_, U = W.shape; units = np.zeros(U); marks = np.zeros(U); cash = CAPITAL; rows = []
    for t, day in enumerate(dates):
        sig = P['sigclose'][t]; valid = np.isfinite(sig) & (sig > 0); marks[valid] = sig[valid]; nav = cash + units @ marks
        wfull = W[t]; desired = np.divide(wfull * nav, marks, out=np.zeros(U), where=marks > 0); desired[~valid] = units[~valid]
        released = 0
        keep = (units > 1e-10) & (desired > 1e-10) & (np.abs(desired - units) * marks < DEADBAND)
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
        want_buy = np.maximum(delta, 0); want_sell = np.maximum(-delta, 0)
        po = P['open'][t]; pcl = P['sigclose'][t]
        can_o = P['fill_open'][t] & np.isfinite(po) & (po > 0)
        nolim = np.zeros(U, bool)
        can_o_buy = can_o & ~(P['ou'][t] if limits else nolim); can_o_sell = can_o & ~(P['od'][t] if limits else nolim)
        gapdiff = np.where(np.isfinite(po) & np.isfinite(pcl), po - pcl, 0.)   # open_j - close_T, adjusted
        rec = dict(date=day, nav_pre=nav, intent_buy=float((want_buy * marks).sum() / nav), intent_sell=float((want_sell * marks).sum() / nav))
        fee_c = 0.; traded = 0.
        if mode == 'open':
            px = np.where(can_o, po, marks); sell = np.where(can_o_sell, want_sell, 0)
            proceeds = sell * px; cash += float((proceeds * (1 - os_)).sum()); units -= sell
            buy = np.where(can_o_buy, want_buy, 0); cost = float((buy * px).sum() * (1 + ob))
            if cost > cash: buy *= max(cash, 0) / cost
            paid = buy * px; cash -= float(paid.sum() * (1 + ob)); units += buy
            fee_c += float(paid.sum() * ob + proceeds.sum() * os_); traded += float(paid.sum() + proceeds.sum())
            rec.update(blocked_open_buy=float((np.where(~can_o_buy, want_buy, 0) * marks).sum() / nav), blocked_open_sell=float((np.where(~can_o_sell, want_sell, 0) * marks).sum() / nav),
                       blocked_open_buy_limit=float((np.where(can_o & ~can_o_buy, want_buy, 0) * marks).sum() / nav), blocked_open_sell_limit=float((np.where(can_o & ~can_o_sell, want_sell, 0) * marks).sum() / nav),
                       # counterfactual: same filled shares at close T instead of open j (positive = closing execution better)
                       cf_gap_buy=float((buy * gapdiff).sum() / nav), cf_gap_sell=float(-(sell * gapdiff).sum() / nav),
                       open_traded=float((paid.sum() + proceeds.sum()) / nav), close_traded=0.)
        else:
            can_c = P['fill_close'][t] & np.isfinite(pcl) & (pcl > 0)
            can_c_buy = can_c & ~(P['cu'][t] if limits else nolim); can_c_sell = can_c & ~(P['cd'][t] if limits else nolim)
            sell = np.where(can_c_sell, want_sell, 0); proceeds = sell * pcl; proceeds[~np.isfinite(proceeds)] = 0
            cash += float((proceeds * (1 - cs)).sum()); units -= sell
            buy = np.where(can_c_buy, want_buy, 0); cost = float(np.nansum(buy * pcl) * (1 + cb))
            if cost > cash: buy *= max(cash, 0) / cost
            paid = np.where(buy > 0, buy * pcl, 0.); cash -= float(paid.sum() * (1 + cb)); units += buy
            fee_c += float(paid.sum() * cb + proceeds.sum() * cs); traded += float(paid.sum() + proceeds.sum())
            cbuy = np.where(~can_c_buy, want_buy, 0); csell = np.where(~can_c_sell, want_sell, 0)   # carried to open j
            s2 = np.where(can_o_sell, csell, 0); px = np.where(can_o, po, marks); pro2 = s2 * px; cash += float((pro2 * (1 - os_)).sum()); units -= s2
            b2 = np.where(can_o_buy, cbuy, 0); cost2 = float((b2 * px).sum() * (1 + ob))
            if cost2 > cash: b2 *= max(cash, 0) / cost2
            paid2 = b2 * px; cash -= float(paid2.sum() * (1 + ob)); units += b2
            fee_c += float(paid2.sum() * ob + pro2.sum() * os_); traded += float(paid2.sum() + pro2.sum())
            rec.update(blocked_close_buy=float((cbuy * marks).sum() / nav), blocked_close_sell=float((csell * marks).sum() / nav),
                       blocked_close_buy_limit=float((np.where(can_c & ~can_c_buy, want_buy, 0) * marks).sum() / nav), blocked_close_sell_limit=float((np.where(can_c & ~can_c_sell, want_sell, 0) * marks).sum() / nav),
                       carried_filled_open=float((paid2.sum() + pro2.sum()) / nav), carried_dropped=float(((cbuy - b2) * marks).sum() / nav + ((csell - s2) * marks).sum() / nav),
                       gap_buy=float((buy * gapdiff).sum() / nav), gap_sell=float(-(sell * gapdiff).sum() / nav),
                       close_traded=float((paid.sum() + proceeds.sum()) / nav), open_traded=float((paid2.sum() + pro2.sum()) / nav))
        closes = P['close'][t]; observed = np.isfinite(closes) & (closes > 0); marks[observed] = closes[observed]
        equity = units * marks; nav = cash + equity.sum()
        rec.update(nav=nav / CAPITAL, cash_weight=cash / nav, fee_cny=fee_c, turnover=traded / nav, holdings=int((units > 1e-10).sum()),
                   member_nav_weight=float(equity[mem_sig[t]].sum() / nav), member_nav_weight_exec=float(equity[mem_exec[t]].sum() / nav),
                   target_member_weight=float(wfull[mem_sig[t]].sum()), target_stock_weight=float(wfull.sum()), deadband_released=released)
        rows.append(rec)
    return pd.DataFrame(rows)


def block_mean_ci(x, n_boot=2000, block=20, seed=20261011):
    x = np.asarray(x, float); n = len(x); rng = np.random.default_rng(seed)
    ix = ((rng.integers(0, n, size=(n_boot, int(np.ceil(n / block)), 1)) + np.arange(block)) % n).reshape(n_boot, -1)[:, :n]
    m = x[ix].mean(axis=1) * 252; lo, hi = np.quantile(m, [.025, .975]); return float(x.mean() * 252), float(lo), float(hi)


POSTHOC = '--posthoc' in sys.argv


def posthoc(dates, targets, build_arm, rex, P_h, mem_sig, mem_exec, log):
    """协议外诊断（结果出来后加的，只用于归因，不进入决策）：拆开 RZ 相对 BASE 的来源，以及不带 beta 倾斜时的时点效应。
    只写 posthoc_*.csv，不覆盖协议产物。"""
    T = dict(targets)
    T['T14i'] = build_arm('H14', False, False, rex)            # 14:00 GRU, inputs as BASE
    T['T15L'] = build_arm('H15', True, True, rex)              # 15:00 GRU, c5/S3x/beta/stop lagged as RZ
    T['T15_nt'] = build_arm('H15', False, False, rex, False); T['T15c_nt'] = build_arm('H15', True, False, rex, False)
    T['T14_nt'] = build_arm('H14', True, False, rex, False); T['T15L_nt'] = build_arm('H15lag', True, False, rex, False)
    log('posthoc targets built')
    arms = {'BASE': ('T15', 'open'), 'RZ': ('T14', 'close'), 'RZ_open': ('T14', 'open'), 'H14_inputsT_open': ('T14i', 'open'), 'H15_inputsLag_open': ('T15L', 'open'),
            'BASE_nt': ('T15_nt', 'open'), 'UB0_nt': ('T15_nt', 'close'), 'UB_nt': ('T15c_nt', 'close'), 'RZ_nt': ('T14_nt', 'close'), 'RZ_open_nt': ('T14_nt', 'open'), 'LAG_nt': ('T15L_nt', 'close')}
    sub = np.asarray(dates); yrs = pd.to_datetime(sub).year; out = []
    for cname in ('main', 'stress_close_impact'):
        res = {a: ledger(dates, T[tk][0], P_h, mode, COSTS[cname], mem_sig, mem_exec) for a, (tk, mode) in arms.items()}
        for x, y in [('H14_inputsT_open', 'BASE'), ('H15_inputsLag_open', 'BASE'), ('RZ_open', 'H14_inputsT_open'), ('RZ_open', 'H15_inputsLag_open'),
                     ('UB0_nt', 'BASE_nt'), ('UB_nt', 'BASE_nt'), ('RZ_nt', 'BASE_nt'), ('RZ_open_nt', 'BASE_nt'), ('RZ_nt', 'RZ_open_nt'), ('LAG_nt', 'BASE_nt'), ('BASE', 'BASE_nt')]:
            nx, ny = res[x].nav.to_numpy(), res[y].nav.to_numpy(); st, _ = V1.paired(nx, ny, sub, 1)
            row = dict(cost=cname, arm=x, versus=y, ann=st['annualized_relative_cagr'], lo=st['boot95_low'], hi=st['boot95_high'], te=st['tracking_error'])
            for yy in (2025, 2026):
                k = np.flatnonzero(yrs == yy); k = k[k > 0] if yy == 2025 else k; lo = max(k[0] - 1, 0)
                s2, _ = V1.paired(nx[lo:k[-1] + 1], ny[lo:k[-1] + 1], sub[lo:k[-1] + 1], 1); row.update({f'y{yy}': s2['annualized_relative_cagr'], f'y{yy}_lo': s2['boot95_low'], f'y{yy}_hi': s2['boot95_high']})
            out.append(row)
        if cname == 'main':
            dec = []
            for a in ('BASE_nt', 'UB0_nt', 'UB_nt', 'RZ_nt', 'LAG_nt'):
                d = res[a].iloc[1:]; cols = ('cf_gap_buy', 'cf_gap_sell') if arms[a][1] == 'open' else ('gap_buy', 'gap_sell')
                for nm, s_ in (('buy', d[cols[0]]), ('sell', d[cols[1]]), ('both', d[cols[0]] + d[cols[1]])):
                    m, lo, hi = block_mean_ci(s_.to_numpy()); dec.append(dict(arm=a, side=nm, ann=m, lo=lo, hi=hi))
                dec.append(dict(arm=a, side='turnover_two_way', ann=float(d.turnover.mean())))
            pd.DataFrame(dec).to_csv(DEST / 'posthoc_decomposition_nt.csv', index=False)
    tilt = [dict(targets=k, tilt_on_days=int((rc.pos != 0).sum()), tilt_up_days=int((rc.pos > 0).sum()), stopped_days=int(rc.get('stopped', pd.Series(dtype=object)).eq(True).sum())) for k, (W, rc) in T.items()]
    pd.DataFrame(tilt).to_csv(DEST / 'posthoc_tilt_state.csv', index=False)
    C = pd.DataFrame(out); C.to_csv(DEST / 'posthoc_comparisons.csv', index=False)
    pd.set_option('display.width', 250); print(C.round(4).to_string(index=False)); print(pd.DataFrame(tilt).to_string(index=False))


def main():
    t0 = time.time(); log = lambda *a: print(f'[{time.time() - t0:6.0f}s]', *a, flush=True)
    receipt = dict(protocol_sha256=sha(HERE / 'PROTOCOL.json'))
    pc = pd.read_parquet(OUT / 'pcf_components.parquet'); meta = pd.read_csv(OUT / 'pcf_metadata.csv', dtype={'date': str, 'valuation_date': str}).set_index('date')
    prices = pd.read_parquet(OUT / 'daily_prices.parquet'); symbols = json.load(open(OUT / 'price_symbols.json')); U = len(symbols); lookup = {s: i for i, s in enumerate(symbols)}
    all_days = sorted(set(prices.date)); all_days = [d for d in all_days if '2023-03-01' <= d <= END]; daypos = {d: i for i, d in enumerate(all_days)}
    wide = lambda f: prices.pivot(index='date', columns='symbol', values=f).reindex(index=all_days, columns=symbols).apply(pd.to_numeric, errors='coerce').to_numpy(float)
    rawclose = wide('close'); op = wide('open_adjusted'); close = wide('close_adjusted'); volume = wide('volume'); valid = wide('valid') == 1; isST = wide('isST')
    can_same = valid & (volume > 0) & (isST != 1)
    op_h, close_h, hinfo = hybrid_prices(all_days, symbols, op, close); receipt['hybrid_prices'] = hinfo; log('hybrid', hinfo)
    # agreement of hybrid vs baostock on the overnight gap (2025-)
    g_b = op[1:] / close[:-1] - 1; g_h = op_h[1:] / close_h[:-1] - 1; k = np.array([d >= '2025-01-01' for d in all_days[1:]]); ok = np.isfinite(g_b) & np.isfinite(g_h) & k[:, None]
    receipt['gap_hybrid_vs_baostock'] = dict(corr=float(np.corrcoef(g_b[ok], g_h[ok])[0, 1]), mean_abs_diff=float(np.mean(np.abs(g_b[ok] - g_h[ok]))), n=int(ok.sum()))
    cu, cd, ou, od, lsan = limit_flags(prices, all_days, symbols, close_h); receipt['limit_flags'] = lsan; log('limits', lsan)

    H15 = build_signals(SETS, symbols, slot=15); H14 = build_signals(SETS, symbols, slot=14); log('signals', len(H15), len(H14))
    Xat, by15, frozen_member, mapping = CRM.risk_rows(symbols); ind_at = CRM.industry_pit(); betas = CRM.Betas(); S3 = CRM.s3x_series(); union = CRM.Union()
    c5 = pd.read_parquet(C5_PATH); c5 = c5[c5.segment != 'live']; s_c5 = {}; bad_c5 = {}
    for day, g in c5.groupby('date'):
        z = np.full(U, np.nan); gg = g[g.code.isin(lookup.keys())]; z[[lookup[c] for c in gg.code]] = ndtri(np.clip(gg.score_pct.to_numpy(), 1e-4, 1 - 1e-4))
        bad = gg[(gg.state == 'vetoed') | ((gg.state == 'excluded') & np.isfinite(gg.raw_score.to_numpy(dtype=float)))]; s_c5[day] = z; bad_c5[day] = set(bad.code)
    c5_days = sorted(s_c5)
    def c5_at(day):   # latest c5 row dated <= day (same as construction_replay's forward fill)
        d = c5_days[bisect.bisect_right(c5_days, day) - 1]; return s_c5[d], bad_c5[d], d
    def blend(a, b): return np.where(np.isfinite(a) & np.isfinite(b), (a + b) / 2, np.where(np.isfinite(a), a, b))

    v4_dates = pd.read_csv(V4 / 'P0_daily.csv').date.tolist(); pcgroups = {d: g[g.stock_replicable] for d, g in pc.groupby('date')}; ev = []
    for day in v4_dates:
        j = daypos[day]; prior = all_days[j - 1]
        if prior not in H15: continue
        g = pcgroups[prior]; m = meta.loc[prior]; assert m.valuation_date < prior or m.valuation_date == prior
        codes = [symbol(c) for c in g.code]; ids = np.array([lookup[c] for c in codes]); prev = rawclose[daypos[m.valuation_date], ids]
        raw = pd.Series(g.quantity.to_numpy() * prev / float(m.nav_per_cu), index=codes)
        ms = np.zeros(U, bool); ms[mapping] = frozen_member[by15[prior]]; me = np.zeros(U, bool); me[mapping] = frozen_member[by15[day]] if day in by15 else frozen_member[by15[prior]]
        ev.append(dict(day=day, prior=prior, prior2=all_days[j - 2], j=j, raw=raw, ms=ms, me=me, valdate=m.valuation_date))
    dates = [e['day'] for e in ev]; T_ = len(ev); log('events', T_, dates[0], dates[-1])
    receipt['pcf_valuation_date_eq_signal_date_days'] = int(sum(e['valdate'] == e['prior'] for e in ev))
    mem_sig = np.asarray([e['ms'] for e in ev]); mem_exec = np.asarray([e['me'] for e in ev]); sym = np.asarray(symbols)
    J = np.array([e['j'] for e in ev])
    def pack(o, c):
        return dict(sigclose=c[J - 1], open=o[J], close=c[J], fill_open=can_same[J - 1], fill_close=can_same[J - 1], cu=cu[J - 1], cd=cd[J - 1], ou=ou[J], od=od[J])
    P_bs = pack(op, close); P_h = pack(op_h, close_h)
    ret_bs = np.full_like(close, np.nan); ret_bs[1:] = close[1:] / close[:-1] - 1; ret_h = np.full_like(close_h, np.nan); ret_h[1:] = close_h[1:] / close_h[:-1] - 1

    def vec(s):
        out = np.zeros(U); out[[lookup[c] for c in s.index]] = s.to_numpy(); return out

    def build_arm(gru, lag_c5, lag_inputs, rexec, tilt_on=True):
        """gru: 'H15' (signal day T), 'H14' (14:00 of T), 'H15lag' (15:00 of T-1). lag_c5: c5 of T-1. lag_inputs: S3x / beta / stop to T-1.
        Construction identical to construction_replay.build_arm(variant='A', tilt_on=True, h85=None)."""
        W = np.zeros((T_, U)); recs = []; pnl = []; prev_delta = pd.Series(dtype=float)
        for t, e in enumerate(ev):
            T = e['prior']; Tm1 = e['prior2']; ind = ind_at(T); allowed = union.at(T); inu = e['raw'].index.isin(list(allowed))
            w0 = e['raw'][inu]; w0 = w0 / w0.sum() * CAPF
            zc5, bad, c5d = c5_at(Tm1 if lag_c5 else T)
            removed = w0[w0.index.isin(bad)]; keep = w0[~w0.index.isin(bad)].copy(); pooled = 0.
            for code, wt in removed.items():
                gi = ind.get(code); peers = [c for c in keep.index if gi is not None and ind.get(c) == gi]
                if peers and keep[peers].sum() > 0: keep[peers] += wt * keep[peers] / keep[peers].sum()
                else: pooled += wt
            if pooled > 0: keep += pooled * keep / keep.sum()
            w = keep / keep.sum() * CAPF
            zg = H15[T] if gru == 'H15' else H14[T] if gru == 'H14' else H15.get(Tm1, np.full(U, np.nan))
            zfull = blend(zg, zc5); z = pd.Series(zfull[[lookup[c] for c in w.index]], index=w.index).fillna(0.).clip(-2, 2)
            tot = w.sum(); w = w * (1 + K * z); w = w / w.sum() * tot
            w = w[w >= MIN_W]; w = w / w.sum() * CAPF
            Tin = Tm1 if lag_inputs else T
            b = betas.at(Tin, list(w.index)); rec = dict(date=e['day'], c5_date=c5d)
            delta = pd.Series(0., index=w.index)
            SS = S3.loc[:Tin]; pos, start, remaining = BT.held(SS.S3x.fillna(0)); epkey = str(start.date()) if start is not None else ''
            known = pnl[:-1] if lag_inputs else pnl   # the last entry needs T's close
            ep_cum = sum(p['net'] for p in known if p['episode'] == epkey) if (start is not None) else 0.
            if pos != 0 and ep_cum < BT.STOP: pos = 0; rec['stopped'] = True
            if not np.isfinite(SS.p_x.iloc[-1]): pos = 0
            cov = float(w.reindex(b.index).sum() / w.sum()) if len(b) else 0.
            if pos != 0 and cov < .80: pos = 0
            if not tilt_on: pos = 0
            ws, info = BT.tilt(w, b, pos); d = ws - w
            if pos != 0 and float(d.abs().sum()) > 0:
                tot = w.sum(); w2 = (w + d).clip(lower=0.); w2 = w2[w2 >= MIN_W]; w2 = w2 / w2.sum() * tot
                delta = w2.reindex(w2.index.union(w.index)).fillna(0) - w.reindex(w2.index.union(w.index)).fillna(0); w = w2
            rec.update(pos=pos, a=info.get('a', 0.), episode=epkey if pos != 0 else '')
            rt = pd.Series(rexec[t][[lookup[c] for c in delta.index]], index=delta.index).fillna(0.)
            u = delta.index.union(prev_delta.index); turn = float((delta.reindex(u).fillna(0) - prev_delta.reindex(u).fillna(0)).abs().sum())
            net = float((delta * rt).sum()) - turn * BT.FEE * BT.COST_MULT; pnl.append(dict(date=e['day'], episode=epkey if pos != 0 else '', net=net, pos=pos)); prev_delta = delta
            c500 = set(sym[e['ms']]); rec['target_c500_share'] = float(w[w.index.isin(c500)].sum()); W[t] = vec(w); recs.append(rec)
        return W, pd.DataFrame(recs)

    # ---------------- calibration gate (baostock prices, 2bp/side, no limit checks, open execution) = construction_replay arm A
    Wcal, _ = build_arm('H15', False, False, ret_bs[J])
    cal = ledger(dates, Wcal, P_bs, 'open', (COM, COM, COM, COM), mem_sig, mem_exec, limits=False)
    old = pd.read_csv(CR / 'arm_A_daily.csv'); assert old.date.tolist() == cal.date.tolist()
    cal_dev = float(np.max(np.abs(old.nav.to_numpy() / cal.nav.to_numpy() - 1))); receipt['calibration_vs_construction_replay_arm_A_max_nav_rel_dev'] = cal_dev; log('calibration', cal_dev)
    assert cal_dev < 1e-9, cal_dev

    # ---------------- arms (hybrid prices)
    rex = ret_h[J]
    targets = {'T15': build_arm('H15', False, False, rex), 'T15c': build_arm('H15', True, False, rex), 'T14': build_arm('H14', True, True, rex), 'TLAG': build_arm('H15lag', True, True, rex)}
    log('targets built')
    if POSTHOC: return posthoc(dates, targets, build_arm, rex, P_h, mem_sig, mem_exec, log)
    for k, (W, rc) in targets.items(): rc.to_csv(DEST / f'targets_{k}_construction.csv', index=False)
    ARMS = {'BASE': ('T15', 'open'), 'UB': ('T15c', 'close'), 'RZ': ('T14', 'close'), 'UB0': ('T15', 'close'), 'RZ_open': ('T14', 'open'), 'LAG': ('TLAG', 'close')}
    res = {}
    for cname, fees in COSTS.items():
        for a, (tk, mode) in ARMS.items():
            d = ledger(dates, targets[tk][0], P_h, mode, fees, mem_sig, mem_exec); res[(cname, a)] = d
            d.to_csv(DEST / f'arm_{a}_{cname}_daily.csv', index=False)
        log('cost', cname, {a: round(float(res[(cname, a)].nav.iloc[-1]), 4) for a in ARMS})

    # ---------------- statistics
    sub = np.asarray(dates); yrs = pd.to_datetime(sub).year
    def paired(x, y):
        nx, ny = x.nav.to_numpy(), y.nav.to_numpy(); st, _ = V1.paired(nx, ny, sub, 1)
        row = dict(days=st['days'], ann=st['annualized_relative_cagr'], lo=st['boot95_low'], hi=st['boot95_high'], te=st['tracking_error'], ir=st['information_ratio'])
        for yy in (2025, 2026):
            k = np.flatnonzero(yrs == yy); k = k[k > 0] if yy == 2025 else k; lo = max(k[0] - 1, 0)
            s2, _ = V1.paired(nx[lo:k[-1] + 1], ny[lo:k[-1] + 1], sub[lo:k[-1] + 1], 1)
            row.update({f'y{yy}': s2['annualized_relative_cagr'], f'y{yy}_lo': s2['boot95_low'], f'y{yy}_hi': s2['boot95_high']})
        return row
    PAIRS = [('UB', 'BASE'), ('RZ', 'BASE'), ('UB0', 'BASE'), ('RZ_open', 'BASE'), ('RZ', 'RZ_open'), ('LAG', 'BASE'), ('RZ', 'LAG'), ('UB', 'UB0')]
    comp = []
    for cname in COSTS:
        for x, y in PAIRS:
            comp.append(dict(cost=cname, arm=x, versus=y, **paired(res[(cname, x)], res[(cname, y)])))
    C = pd.DataFrame(comp); C.to_csv(DEST / 'paired_comparisons.csv', index=False)

    summ = []
    for cname in COSTS:
        for a, (tk, mode) in ARMS.items():
            d = res[(cname, a)]; x = d.iloc[1:]; nav = d.nav.to_numpy(); r = nav[1:] / nav[:-1] - 1
            row = dict(cost=cname, arm=a, exec=mode, ann_return=float(np.prod(1 + r) ** (252 / len(r)) - 1), turnover_two_way_mean=float(x.turnover.mean()),
                       intent_two_way_mean=float((x.intent_buy + x.intent_sell).mean()), fee_bp_per_year=float((d.fee_cny / (d.nav * CAPITAL)).iloc[1:].mean() * 252 * 1e4),
                       cash_mean=float(x.cash_weight.mean()), holdings_mean=float(x.holdings.mean()),
                       c500_nav_mean=float(x.member_nav_weight.mean()), c500_nav_min=float(x.member_nav_weight.min()), c500_days_lt85=int((x.member_nav_weight < .85).sum()), c500_days_lt80=int((x.member_nav_weight < .80).sum()),
                       c500_exec_nav_min=float(x.member_nav_weight_exec.min()))
            ib, is_ = x.intent_buy.sum(), x.intent_sell.sum()
            if mode == 'close':
                row.update(fail_close_buy_share=float(x.blocked_close_buy.sum() / ib), fail_close_sell_share=float(x.blocked_close_sell.sum() / is_),
                           fail_close_buy_limit_share=float(x.blocked_close_buy_limit.sum() / ib), fail_close_sell_limit_share=float(x.blocked_close_sell_limit.sum() / is_),
                           carried_filled_open_share=float(x.carried_filled_open.sum() / (ib + is_)), carried_dropped_share=float(x.carried_dropped.sum() / (ib + is_)),
                           close_traded_share=float(x.close_traded.sum() / x.turnover.sum()))
            else:
                row.update(fail_open_buy_share=float(x.blocked_open_buy.sum() / ib), fail_open_sell_share=float(x.blocked_open_sell.sum() / is_),
                           fail_open_buy_limit_share=float(x.blocked_open_buy_limit.sum() / ib), fail_open_sell_limit_share=float(x.blocked_open_sell_limit.sum() / is_))
            summ.append(row)
    S = pd.DataFrame(summ); S.to_csv(DEST / 'arm_summary.csv', index=False)

    # ---------------- overnight decomposition (main cost)
    dec = []
    for a, (tk, mode) in ARMS.items():
        d = res[('main', a)].iloc[1:]; cols = ('cf_gap_buy', 'cf_gap_sell') if mode == 'open' else ('gap_buy', 'gap_sell')
        what = 'counterfactual: same filled open trades done at close T' if mode == 'open' else 'realised: close fills vs same shares at next open'
        for yy in ('all', 2025, 2026):
            dd = d if yy == "all" else d[pd.to_datetime(d.date).dt.year == yy]
            for nm, s in (('buy', dd[cols[0]]), ('sell', dd[cols[1]]), ('both', dd[cols[0]] + dd[cols[1]])):
                m, lo, hi = block_mean_ci(s.to_numpy())
                dec.append(dict(arm=a, measure=what, year=yy, side=nm, ann=m, lo=lo, hi=hi, per_traded_bp=float(s.sum() / ((dd.open_traded if mode == 'open' else dd.close_traded).sum()) * 1e4) if nm == 'both' else np.nan))
    DEC = pd.DataFrame(dec); DEC.to_csv(DEST / 'overnight_decomposition.csv', index=False)

    receipt.update(seconds=round(time.time() - t0), events=T_, first=dates[0], last=dates[-1], capital=CAPITAL, costs=COSTS, sets=SETS, deadband=DEADBAND,
                   inputs={str(p.relative_to(ROOT)): sha(p) for p in [CR / 'construction_replay.py', CR / 'arm_A_daily.csv', OUT / 'daily_prices.parquet', OUT / 'pcf_components.parquet', Path(C5_PATH)]})
    (DEST / 'run_receipt.json').write_text(json.dumps(receipt, indent=1, ensure_ascii=False, default=str))
    pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40)
    print(C.round(4).to_string(index=False)); print(S.round(4).T.to_string()); print(DEC.round(5).to_string(index=False))


if __name__ == '__main__':
    main()
