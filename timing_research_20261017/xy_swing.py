"""兴证金工《相似几何系列二》自适应波段划分 + 点位效率，按公开公式复刻（2026-10-17）。

公式来源：XYQuantResearch 2021-09-17 公众号原文（公式图）：
  TR = max(H-L, H-C[-1], C[-1]-L)；ATR = 100 日均值；delta_t = Rate * ATR，Rate = 2
  DIF/DEA = MACD(12, 26, 9)；Integral[t] = 当前同号段内 sum(DIF-DEA)（DIF==DEA 时为 0）
  Dir[t] = 1  若 Integral >= delta 或 (Dir[t-1]==1 且 Integral >= -delta)
         = -1 若 Integral <= -delta 或 (Dir[t-1]==-1 且 Integral <= delta)
  Except[t] = -1 若 Dir 未变且 (Dir==1 且 收盘<=前低) 或 (Dir==-1 且 收盘>=前高)；
             处于异常时，Dir 转向或同向破前极值（上行破前高/下行破前低）即退出
  Status = Dir * Except；Status 翻转时把上一段的极值记为确认端点（不重绘）。
点位效率（H-L-P 为例）：time = (T_P - T_L) / (T_L - T_H)（自然日），price = |C_P - C_L| / |C_H - C_L|。
概率口径原文未公开公式，这里用"已完成同向波段的终点价格效率"的经验分布：P(本段继续) = 已完成段中效率 > 当前值的比例。
用法：python xy_swing.py <csv: date,open,close,high,low[,volume]> [name]
"""
import sys
import numpy as np
import pandas as pd


def ema(x, n):
    return x.ewm(span=n, adjust=False).mean()


def swing(df, rate=2.0, atr_n=100):
    c, h, l = df.close.values, df.high.values, df.low.values
    prev_c = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.c_[h - l, np.abs(h - prev_c), np.abs(prev_c - l)], axis=1)
    atr = pd.Series(tr).rolling(atr_n).mean().values
    dif = ema(df.close, 12) - ema(df.close, 26); dea = ema(dif, 9); d = (dif - dea).values
    n = len(c); dir_ = np.zeros(n, int); exc = np.ones(n, int); integ = np.zeros(n)
    pivots = []  # (idx, 'H'/'L', price, confirm_idx): confirm_idx = bar on which the pivot became known (Status flip)
    last_hi = last_lo = np.nan
    status_prev = 0; ext_i = 0
    for t in range(n):
        if t > 0 and np.sign(d[t]) == np.sign(d[t - 1]) and d[t] != 0:
            integ[t] = integ[t - 1] + d[t]
        else:
            integ[t] = d[t] if d[t] != 0 else 0.0
        if np.isnan(atr[t]):
            dir_[t] = 0; continue
        delta = rate * atr[t]; pd_ = dir_[t - 1] if t > 0 else 0
        if integ[t] >= delta or (pd_ == 1 and integ[t] >= -delta): dir_[t] = 1
        elif integ[t] <= -delta or (pd_ == -1 and integ[t] <= delta): dir_[t] = -1
        else: dir_[t] = pd_ if pd_ != 0 else (1 if integ[t] >= 0 else -1)
        # exception state
        if t > 0 and exc[t - 1] == -1:
            if dir_[t] != dir_[t - 1]: exc[t] = 1
            elif dir_[t] == 1 and not np.isnan(last_hi) and c[t] > last_hi: exc[t] = 1
            elif dir_[t] == -1 and not np.isnan(last_lo) and c[t] < last_lo: exc[t] = 1
            else: exc[t] = -1
        else:
            same = t > 0 and dir_[t] == dir_[t - 1]
            if same and ((dir_[t] == 1 and not np.isnan(last_lo) and c[t] <= last_lo) or (dir_[t] == -1 and not np.isnan(last_hi) and c[t] >= last_hi)):
                exc[t] = -1
            else:
                exc[t] = 1
        st = dir_[t] * exc[t]
        if status_prev == 0:
            status_prev, ext_i = st, t; continue
        if st != status_prev:  # confirm the extreme of the finished leg
            kind = 'H' if status_prev == 1 else 'L'
            pivots.append((ext_i, kind, c[ext_i], t))
            if kind == 'H': last_hi = c[ext_i]
            else: last_lo = c[ext_i]
            status_prev, ext_i = st, t
        else:
            if (st == 1 and c[t] >= c[ext_i]) or (st == -1 and c[t] <= c[ext_i]): ext_i = t
    out = df.copy(); out['dir'] = dir_; out['except'] = exc; out['status'] = dir_ * exc
    piv = pd.DataFrame(pivots, columns=['i', 'kind', 'price', 'confirm_i']); piv['date'] = df.index[piv.i]
    return out, piv, ext_i


def efficiency(df, piv, t):
    """point efficiency at bar t using the last two pivots confirmed strictly before t"""
    p = piv[piv.i < t]
    if len(p) < 2: return None
    a, b = p.iloc[-2], p.iloc[-1]
    dt = lambda i, j: (df.index[j] - df.index[i]).days
    return dict(order=f"{a.kind}-{b.kind}-P", time_eff=dt(b.i, t) / max(dt(a.i, b.i), 1),
                price_eff=abs(df.close.iloc[t] - b.price) / abs(b.price - a.price), last_pivot=str(b.date.date()), last_pivot_px=b.price,
                prev_pivot=str(a.date.date()), prev_pivot_px=a.price)


if __name__ == '__main__':
    f = sys.argv[1]; name = sys.argv[2] if len(sys.argv) > 2 else f
    df = pd.read_csv(f, parse_dates=['date']).set_index('date').sort_index()
    out, piv, ext_i = swing(df)
    t = len(df) - 1
    e = efficiency(df, piv, t)
    legs = piv.copy(); legs['amp'] = legs.price.pct_change()
    print(f'== {name}: {len(piv)} confirmed pivots, last 6:'); print(piv.tail(6)[['date', 'kind', 'price']].to_string(index=False))
    print('current status', out.status.iloc[-1], 'dir', out.dir.iloc[-1], 'except', out['except'].iloc[-1], 'running extreme', df.index[ext_i].date(), round(df.close.iloc[ext_i], 2))
    print('point efficiency', {k: (round(v, 3) if isinstance(v, float) else v) for k, v in e.items()})
    # completed-leg efficiency distribution, same direction as current leg
    rows = []
    for k in range(2, len(piv)):
        a, b, cpt = piv.iloc[k - 2], piv.iloc[k - 1], piv.iloc[k]
        rows.append(dict(dir=cpt.kind, eff=abs(cpt.price - b.price) / abs(b.price - a.price), amp=cpt.price / b.price - 1))
    R = pd.DataFrame(rows)
    cur_dir = 'L' if e['order'].startswith('L-H') else 'H'  # L-H-P = falling leg (will end at an L)
    same = R[R.dir == cur_dir]
    print(f'completed {"falling" if cur_dir == "L" else "rising"} legs: n={len(same)}, eff median {same.eff.median():.2f}, p75 {same.eff.quantile(.75):.2f}, p90 {same.eff.quantile(.9):.2f}; '
          f'P(leg continues past current eff) = {(same.eff > e["price_eff"]).mean():.2f}; amp median {same.amp.median():.3f}')


def report_prob(df, min_points=250, thr=0.30, out_piv=None):
    """S3x（2026-10-18 起驱动实盘 beta 倾斜）：系列二 4.2 节的"上涨概率"口径。
    历史样本点 = 同样 H-L-P / L-H-P 顺序的每一个交易日（端点按极值日分段，且该段终点在 t 日或之前已确认）；
    P(本段继续) = 样本点里价格效率高于当前点的比例；p < thr 时，下跌段 → +1（偏高 beta），上涨段 → −1。
    沪深 300 2021-09-15 校验：本地 84.3%（研报 87.67%，研报样本从 2005 起、本地从 2010 起）。
    p_xt = 只看时间效率 ≥ 当前点的样本点（研报"叠加时间效率"），仅记录。"""
    out, piv, _ = (swing(df) if out_piv is None else out_piv)
    n = len(df); c = df.close.values; days = (df.index - df.index[0]).days.values
    pv = piv.sort_values('i').reset_index(drop=True); pi, pc, pk, pp = pv.i.values, pv.confirm_i.values, pv.kind.values, pv.price.values
    code = {'HL': 1, 'LH': 2}; order = np.zeros(n, int); pe = np.full(n, np.nan); te = np.full(n, np.nan); avail = np.full(n, np.iinfo(np.int64).max)
    for t in range(n):
        k = np.searchsorted(pi, t, side='left')
        if k < 2 or k >= len(pi): continue
        a, b = k - 2, k - 1
        order[t] = code.get(pk[a] + pk[b], 0); pe[t] = abs(c[t] - pp[b]) / abs(pp[b] - pp[a])
        te[t] = (days[t] - days[pi[b]]) / max(days[pi[b]] - days[pi[a]], 1); avail[t] = pc[k]
    pvc = piv.sort_values('confirm_i').reset_index(drop=True); ci, ii, kk, pr = pvc.confirm_i.values, pvc.i.values, pvc.kind.values, pvc.price.values
    P = np.full(n, np.nan); PT = np.full(n, np.nan); S = np.zeros(n); eff = np.full(n, np.nan)
    for t in range(n):
        m = np.searchsorted(ci, t, side='right')
        if m < 2: continue
        a, b = m - 2, m - 1
        cur = abs(c[t] - pr[b]) / abs(pr[b] - pr[a]); eff[t] = cur
        cur_te = (days[t] - days[ii[b]]) / max(days[ii[b]] - days[ii[a]], 1)
        ok = (avail <= t) & (order == code.get(kk[a] + kk[b], -1))
        if ok.sum() < min_points: continue
        P[t] = float((pe[ok] > cur).mean())
        okt = ok & (te >= cur_te)
        if okt.sum() >= 30: PT[t] = float((pe[okt] > cur).mean())
        if P[t] < thr: S[t] = 1 if kk[b] == 'H' else -1
    return pd.DataFrame(dict(S3x=S, p_x=P, p_xt=PT, eff_x=eff), index=df.index)
