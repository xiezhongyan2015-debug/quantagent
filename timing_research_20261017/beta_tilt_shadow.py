"""P3 beta 倾斜（协议：reports/P3_beta倾斜影子登记协议v2_2026-10-17.md）。本脚本只读目标、只写记录文件。
2026-10-19 起实盘：beta_tilt_live.py 在未倾斜目标上调用本脚本，再用 delta 经 live_targets --overlay 生成实盘目标；
此后盈亏按 delta_realized_<T>.csv（整手后的实际权重差）记，没有时退回 delta_<T>.csv。
2026-10-19 信号日起（用户 10-18 决定）：倾斜方向只由 S3x 驱动（xy_swing.report_prob，系列二原文的概率口径），触发后持有 35 天；
S7 照算照记（pos_S7 列），只作影子对照。止损、数据保护不变（S3x 不可得 → pos 0）。

每个信号日 T（发布 T+1 目标之后运行）：
  1 算 S2/S3/S4/S5/S7（中证500，因果），持有 35 日的 pos，并执行段内止损（−1.0pp）与数据保护
  2 对 T+1 实盘目标按 60 日 beta 五分组倾斜 a = min(0.10, 0.5·W_Q1, 0.5·W_Q5)，写 shadow_target_<T+1>.json 与 delta_<T+1>.csv
  3 记 T 日影子盈亏：Σ(w_影子 − w_实盘)·r_i(T)，扣倾斜换手成本（万二双边×2）
用法：beta_tilt_shadow.py <T> --next <T+1> --target <target_T+1.json> [--panel DIR] [--cache DIR] [--out DIR] [--prev-target <target_T.json>] [--no-fetch]
"""
import sys, json, warnings
from pathlib import Path
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import dashboard as D  # noqa: E402
import lppls  # noqa: E402
import xy_swing  # noqa: E402
from p2_signals import swing_history, breadth_history  # noqa: E402

HOLD, A_MAX, STOP, FEE, COST_MULT, BETA_N, BETA_MIN = 35, 0.10, None, 0.0002, 2, 60, 40   # STOP was -0.010 until 2026-10-19


def weights(path):
    j = json.loads(Path(path).read_text(encoding='utf-8')); rows = j.get('targets') or []
    return pd.Series({r['code']: float(r['weight']) for r in rows if r.get('weight') is not None}, dtype=float), j


def signals(T, panel, cache, fetch):
    bars = D.load_index('000905', T, cache, panel, fetch)
    sh, _ = swing_history(bars)
    lp = D.lppls_block('000905', bars, cache)                       # updates the daily LPPLS cache up to T
    h = pd.read_csv(cache / 'lppls_000905.csv', parse_dates=['date']).drop_duplicates('date').set_index('date').sort_index()
    h = h.reindex(bars.index).ffill(limit=5)
    pk_p, pk_n = h.pos.rolling(40, min_periods=1).max(), h.neg.rolling(40, min_periods=1).max()
    S4 = pd.Series(0.0, index=bars.index); S4[(pk_n > 0.3) & (pk_n - h.neg > 0.2)] = 1; S4[(pk_p > 0.3) & (pk_p - h.pos > 0.2)] = -1
    close = D.read_panel(panel, 'close').loc[:T]; mem = D.read_panel(panel, 'member_csi500').loc[:T].fillna(0).astype(bool)
    br = breadth_history(close, mem.reindex_like(close).fillna(False), bars)
    S = pd.DataFrame(dict(S2=sh.S2, S3=sh.S3, p_extend=sh.p_extend, S4=S4, S5=br.S5.reindex(bars.index))).loc[:T]
    S['S3_ok'] = S.p_extend.notna()
    X = xy_swing.report_prob(bars); S['S3x'] = X.S3x.reindex(S.index).fillna(0); S['p_x'] = X.p_x.reindex(S.index)   # 2026-10-18: live driver
    S['S7'] = np.sign(S[['S2', 'S3', 'S4', 'S5']].fillna(0).sum(axis=1))
    return S, bars, lp


def held(S7):
    """sign of the last nonzero S7 within the last HOLD bars (incl. today); trigger date and remaining days"""
    nz = S7[S7 != 0]
    if nz.empty: return 0, None, 0
    last_d = nz.index[-1]; age = len(S7.loc[last_d:]) - 1
    if age >= HOLD: return 0, None, 0
    sign = int(nz.iloc[-1])
    # episode start = first bar of the current run of the same held sign
    held_series = S7.where(S7 != 0).ffill(limit=HOLD - 1).fillna(0)
    run = held_series[::-1]; k = 0
    while k < len(run) and np.sign(run.iloc[k]) == sign: k += 1
    start = held_series.index[len(held_series) - k]
    return sign, start, HOLD - age - 1


def betas(panel, bars, T, codes):
    ret = D.read_panel(panel, 'ret').loc[:T].iloc[-BETA_N:]
    m = bars.close.pct_change().reindex(ret.index)
    cols = [c for c in codes if c in ret.columns]; R = ret[cols]
    ok = R.notna().sum() >= BETA_MIN
    b = R.loc[:, ok].apply(lambda s: s.cov(m) / m[s.notna()].var())
    return b


def tilt(w, b, pos):
    out = w.copy(); info = dict(a=0.0, W_Q1=None, W_Q5=None)
    bb = b.reindex(w.index).dropna()
    if pos == 0 or len(bb) < 25: return out, info
    q = pd.qcut(bb.rank(method='first'), 5, labels=False)
    q1, q5 = q[q == 0].index, q[q == 4].index; W1, W5 = float(w[q1].sum()), float(w[q5].sum())
    a = min(A_MAX, 0.5 * W1, 0.5 * W5); lo, hi = (q1, q5) if pos > 0 else (q5, q1); Wlo, Whi = (W1, W5) if pos > 0 else (W5, W1)
    out[lo] = w[lo] * (1 - a / Wlo); out[hi] = w[hi] * (1 + a / Whi)
    info.update(a=round(a, 4), W_Q1=round(W1, 4), W_Q5=round(W5, 4))
    return out, info


def upsert(f, row, key):
    df = pd.read_csv(f, dtype={key: str}) if f.exists() else pd.DataFrame()
    if len(df) and key in df: df = df[df[key] != row[key]]
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True).sort_values(key); df.to_csv(f, index=False); return df


def main():
    a = sys.argv[1:]; T = a[0]; NEXT = D.opt(a, '--next'); target = D.opt(a, '--target')
    panel = D.opt(a, '--panel', D.default_panel()); cache = Path(D.opt(a, '--cache', str(HERE / 'cache'))); out = Path(D.opt(a, '--out', str(HERE / 'out/beta_shadow')))
    prev_target = D.opt(a, '--prev-target'); fetch = '--no-fetch' not in a
    out.mkdir(parents=True, exist_ok=True); cache.mkdir(parents=True, exist_ok=True)
    pnl_f, log_f = out / 'beta_shadow_pnl.csv', out / 'beta_shadow_log.csv'
    S, bars, lp = signals(T, panel, cache, fetch)
    notes = []
    # ---- 3 (first, so today's P&L feeds the stop rule): realised shadow-minus-live on T for the target executed on T
    d_today = out / T / f'delta_{T}.csv'; d_real = out / T / f'delta_realized_{T}.csv'
    if d_real.exists(): d_today = d_real
    if d_today.exists():
        dlt = pd.read_csv(d_today, dtype={'code': str}).set_index('code').delta
        r = D.read_panel(panel, 'ret')
        if pd.Timestamp(T) in r.index:
            rt = r.loc[pd.Timestamp(T)].reindex(dlt.index).fillna(0.0)
            prev = sorted(p.parent for p in out.glob('*/delta_*.csv') if p.parent.name < T)
            prev = [d / f'delta_realized_{d.name}.csv' if (d / f'delta_realized_{d.name}.csv').exists() else d / f'delta_{d.name}.csv' for d in prev[-1:]]
            dprev = pd.read_csv(prev[-1], dtype={'code': str}).set_index('code').delta if prev else pd.Series(dtype=float)
            u = dlt.index.union(dprev.index); turn = float((dlt.reindex(u).fillna(0) - dprev.reindex(u).fillna(0)).abs().sum())
            gross = float((dlt * rt).sum()); net = gross - turn * FEE * COST_MULT
            meta = json.loads((out / T / f'meta_{T}.json').read_text(encoding='utf-8'))
            upsert(pnl_f, dict(date=T, pos=meta['pos'], episode=meta.get('episode_start') or '', gross=gross, turnover=turn, net=net), 'date')
        else:
            notes.append('面板无当日收益，影子盈亏未记')
    pnl = pd.read_csv(pnl_f, dtype={'date': str, 'episode': str}) if pnl_f.exists() else pd.DataFrame(columns=['date', 'episode', 'net'])
    # ---- 1 signals and held position (+ stop and data guards)
    pos, start, remaining = held(S.S3x)          # 2026-10-18: S3x drives the tilt
    pos_s7, start_s7, _ = held(S.S7)             # shadow record only
    st_now = S.iloc[-1]
    ep_cum = float(pnl[pnl.episode == (str(start.date()) if start is not None else '')].net.sum()) if start is not None and len(pnl) else 0.0
    # 2026-10-19 user instruction: in-episode stop removed (every stop variant lowered returns without cutting the worst drawdown,
    # reports/易方达骨架上的增强优化.md). The episode P&L is still recorded; the size cap a <= 0.10 stays as the ex-ante budget.
    if STOP is not None and pos != 0 and ep_cum < STOP: notes.append(f'段内止损：本段累计 {ep_cum:+.2%} < −1.0%，pos 置 0'); pos = 0
    if not np.isfinite(st_now.p_x): notes.append('S3x 不可得，pos 置 0'); pos = 0
    # ---- 2 tilt the released target for NEXT
    w, j = weights(target)
    if (j.get('generated_from') or {}).get('overlay'): raise SystemExit('目标已带 overlay（已倾斜）：只能在未倾斜目标上计算，避免二次倾斜')
    b = betas(panel, bars, T, list(w.index)); cov = float(w.reindex(b.index).sum() / w.sum()) if len(b) else 0.0
    if pos != 0 and cov < 0.80: notes.append(f'beta 覆盖率 {cov:.0%} < 80%，pos 置 0'); pos = 0
    ws, info = tilt(w, b, pos)
    mem = D.read_panel(panel, 'member_csi500').loc[:T].iloc[-1].fillna(0).astype(bool)
    share = lambda x: float(x[[c for c in x.index if mem.get(c, False)]].sum() / x.sum()) if x.sum() > 0 else np.nan
    beta_p = lambda x: float((x.reindex(b.index).fillna(0) * b).sum() / x.reindex(b.index).fillna(0).sum()) if len(b) else np.nan
    dlt = (ws - w)
    nd = out / NEXT; nd.mkdir(parents=True, exist_ok=True)
    jj = dict(j); jj['strategy_id'] = str(j.get('strategy_id', '')) + '+beta_tilt_shadow_v2'; jj['targets'] = [dict(code=c, weight=round(float(x), 6)) for c, x in ws.sort_values(ascending=False).items()]
    (nd / f'shadow_target_{NEXT}.json').write_text(json.dumps(jj, ensure_ascii=False, indent=1), encoding='utf-8')
    dlt.rename('delta').rename_axis('code').reset_index().to_csv(nd / f'delta_{NEXT}.csv', index=False)
    row = dict(date=T, exec_date=NEXT, S2=int(st_now.S2), S3=int(st_now.S3), p_extend=round(float(st_now.p_extend), 3) if pd.notna(st_now.p_extend) else None,
               S4=int(st_now.S4), S5=int(st_now.S5) if pd.notna(st_now.S5) else None, S7=int(st_now.S7), S3x=int(st_now.S3x), p_x=round(float(st_now.p_x), 3) if pd.notna(st_now.p_x) else None,
               driver='S3x', pos_S7=int(pos_s7), pos=pos,
               episode_start=str(start.date()) if (start is not None and pos != 0) else '', remaining=remaining if pos != 0 else 0, **info,
               beta_live=round(beta_p(w), 3), beta_shadow=round(beta_p(ws), 3), beta_diff=round(beta_p(ws) - beta_p(w), 3), beta_coverage=round(cov, 3),
               csi500_live=round(share(w), 4), csi500_shadow=round(share(ws), 4), max_weight_shadow=round(float(ws.max()), 4), l1_half=round(float(dlt.abs().sum() / 2), 4),
               names=int((w > 0).sum()), episode_cum_net=round(ep_cum, 5), cum_net=round(float(pnl.net.sum()) if len(pnl) else 0.0, 5), notes='；'.join(notes))
    (nd / f'meta_{NEXT}.json').write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding='utf-8')
    upsert(log_f, row, 'date')
    print(json.dumps(row, ensure_ascii=False))


def summary_line(out):
    """one line for the daily report (used by dashboard.py)"""
    f = Path(out) / 'beta_shadow_log.csv'
    if not f.exists(): return None
    r = pd.read_csv(f).sort_values('date').iloc[-1]
    p = {1: '偏高beta', -1: '偏低beta', 0: '不倾斜'}[int(r.pos)]
    ap = Path(out) / str(r.exec_date) / f'applied_{r.exec_date}.json'; live = json.loads(ap.read_text(encoding='utf-8')) if ap.exists() else None
    tag = '（实盘，v2）' if live is None else ('（实盘已执行）' if live.get('applied') else f"（实盘未倾斜：{live.get('reason', '')[:40]}）")
    drv = str(r.get('driver')) if 'driver' in r and isinstance(r.get('driver'), str) else 'S7'
    s = f"beta 倾斜{tag}，信号 {drv}：{r.exec_date} {p}"
    if int(r.pos) != 0: s += f"（{r.episode_start} 起，剩 {int(r.remaining)} 天），组合 beta {r.beta_live:.2f}→{r.beta_shadow:.2f}"
    if 'pos_S7' in r and pd.notna(r.pos_S7): s += f"；S7 影子 {({1: '偏高', -1: '偏低', 0: '不倾斜'})[int(r.pos_S7)]}"
    s += f"；倾斜累计贡献 {float(r.cum_net) * 1e4:+.0f}bp（成本加倍，10-19 起为实盘）"
    if isinstance(r.notes, str) and r.notes: s += f"；{r.notes}"
    return s


if __name__ == '__main__':
    main()
