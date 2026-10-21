"""日报仪表盘 v3 块：兴证择时体系按"趋势族 / 拐点族"分组 + 《择时的艺术》合成 ART + 3.0 三维仓位（只读，不交易）。2026-10-17
口径与复刻偏差见 reports/择时研究v3_复刻审查与分族检验_2026-10-17.md。由 dashboard.py 调用：v3_block(T, D, panel, cache, external)。
趋势族：T1 非对称信用扩张（人民币贷款余额同比代理）、T2 汇率趋势（USDCNH 对 20 个交易日前）、T4–T6 3.0 战术（取 dashboard 已算的值）、T7 中证500 波段方向
拐点族：E1 性价比 2.0 涨跌周期、E2 LPPLS 负泡沫消散（中证全指）、E3 地缘政治极端（GPR）、E4 拓扑 2 维孔洞（申万二级）；补充：段延伸、异常态（非兴证模型成员）
合成：ART = sign(1 + T1 + T2 + E1 + E2 + E3 + E4)（通胀预期无数据记 0）；三维：W = 性价比3.0 分位（上月末）+ 0.1×(T4+T5+T6) + 0.1×E2 → Sigmoid
数据：东方财富（国债、新增贷款、USDCNH）、GPR（matteoiacoviello.com）、Tushare sw_daily（申万二级当日）、c5 面板（股息率延伸）；历史种子在 <cache>/v3/
"""
import io, json, time, sys, os
from pathlib import Path
import numpy as np, pandas as pd, requests

# cloud: pyRipser (compiled core of ripser 0.6.12, numpy-only) and pure-python xlrd live in an extras folder used by the GRU venv
_EXTRAS = os.environ.get('QD_TIMING_EXTRAS', 'C:/ChoiceBridge/runtime/timing-extras')
if Path(_EXTRAS).exists() and _EXTRAS not in sys.path: sys.path.insert(0, _EXTRAS)


def _h2(D):
    """number of 2-dim persistence pairs of the Vietoris-Rips filtration of distance matrix D (identical to ripser(D, distance_matrix=True, maxdim=2))"""
    try:
        from ripser import ripser
        return len(ripser(D, distance_matrix=True, maxdim=2)['dgms'][2])
    except ImportError:
        from pyRipser import doRipsFiltrationDM as DRFDM
        n = D.shape[0]; I, J = np.meshgrid(np.arange(n), np.arange(n))
        return len(DRFDM(np.array(D[I > J], dtype=np.float32), 2, np.inf, 2, 0)['births_and_deaths_by_dim'][2]) // 2

H = {'User-Agent': 'Mozilla/5.0'}


def _em(name, sort, pages=None, size=500):
    rows, p = [], 1
    while True:
        j = requests.get('https://datacenter-web.eastmoney.com/api/data/v1/get', params=dict(reportName=name, columns='ALL', sortColumns=sort, sortTypes=-1, pageSize=size, pageNumber=p), headers=H, timeout=30).json()
        r = j['result']; rows += r['data']
        if p >= r['pages'] or (pages and p >= pages): break
        p += 1; time.sleep(0.3)
    return pd.DataFrame(rows)


def rolling_pct(s, n, minp):
    return s.rolling(n, min_periods=minp).apply(lambda w: (w[:-1] < w[-1]).mean() + 0.5 * (w[:-1] == w[-1]).mean() if len(w) > 1 else np.nan, raw=True)


def g_sigmoid(q, a=10):
    s = lambda x: 1 / (1 + np.exp(-a * (x - 0.5))); return float((s(q) - s(0)) / (s(1) - s(0)))


# ---------------------------------------------------------------- data with caches
def yields(c):
    f = c / 'yields.csv'; old = pd.read_csv(f, parse_dates=['date']) if f.exists() else None
    x = _em('RPTA_WEB_TREASURYYIELD', 'SOLAR_DATE', pages=None if old is None else 1)
    x['date'] = pd.to_datetime(x.SOLAR_DATE); x = x.rename(columns={'EMM00166466': 'cn10y', 'EMG00001310': 'us10y'})[['date', 'cn10y', 'us10y']]
    y = x if old is None else pd.concat([old[old.date < x.date.min()], x])
    y = y.drop_duplicates('date').sort_values('date'); y.to_csv(f, index=False); return y.set_index('date')


def loans(c):
    x = _em('RPT_ECONOMY_RMB_LOAN', 'REPORT_DATE'); x['month'] = pd.to_datetime(x.REPORT_DATE).dt.to_period('M')
    s = x.drop_duplicates('month').set_index('month').RMB_LOAN.astype(float).sort_index(); s = s.reindex(pd.period_range(s.index.min(), s.index.max(), freq='M'))
    a = pd.Period('2026-09', 'M'); bal = pd.Series(index=s.index, dtype=float); bal[a] = 2834400.0  # 2026-09 人民币贷款余额 283.44 万亿（亿元）
    for p in reversed(s.index[s.index < a]): bal[p] = bal[p + 1] - s[p + 1]
    for p in s.index[s.index > a]: bal[p] = bal[p - 1] + s[p]
    out = pd.DataFrame(dict(yoy=bal / bal.shift(12) - 1)); out.to_csv(c / 'loans.csv'); return out.yoy


def usdcnh(c):
    f = c / 'usdcnh.csv'
    try:
        j = requests.get('https://push2his.eastmoney.com/api/qt/stock/kline/get', params=dict(secid='133.USDCNH', fields1='f1', fields2='f51,f53', klt=101, fqt=0, beg='20190101', end='20991231'), headers=H, timeout=30).json()
        k = [r.split(',') for r in j['data']['klines']]; df = pd.DataFrame(k, columns=['date', 'close']); df['close'] = df.close.astype(float); df.to_csv(f, index=False)
    except Exception:
        if not f.exists(): raise
    df = pd.read_csv(f, parse_dates=['date']); return df.set_index('date').close


def gpr(c):
    f = c / 'gpr_daily.csv'
    try:
        r = requests.get('https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls', headers=H, timeout=90)
        x = pd.read_excel(io.BytesIO(r.content)); x = x[['date', 'GPRD']].dropna(); x.to_csv(f, index=False)
    except Exception:
        if not f.exists(): raise
    return pd.read_csv(f, parse_dates=['date']).set_index('date').GPRD.sort_index()


def div_series(c, panel, days):
    """全 A 市值加权股息率（%）：研究期种子（daily_basic，至 2026-09-18）+ c5 面板 fd_v_dv_ttm 延伸，在衔接日按比例拼接"""
    seed = pd.read_csv(c / 'div_seed.csv', parse_dates=['date']).set_index('date').DIV
    try:
        dv = pd.read_parquet(Path(panel, 'panel/fd_v_dv_ttm.parquet')); mv = pd.read_parquet(Path(panel, 'panel/total_market_cap.parquet'))
        dv.index = pd.to_datetime(dv.index); mv.index = pd.to_datetime(mv.index)
        p = (dv.fillna(0) * mv).sum(axis=1) / mv.sum(axis=1)
        k = seed.index[-1]; ratio = seed.iloc[-1] / p.reindex([k]).ffill().iloc[-1] if k in p.index else 1.0
        ext = p[p.index > k] * ratio; s = pd.concat([seed, ext])
    except Exception:
        s = seed
    return s.reindex(days.union(s.index)).ffill().reindex(days)


def sw_l2(c, T, ts_secret):
    """申万二级收盘：种子 + 每日 Tushare sw_daily(trade_date) 增量"""
    f = c / 'sw_l2_close.parquet'; P = pd.read_parquet(f); P.index = pd.to_datetime(P.index)
    need = pd.Timestamp(T)
    if need not in P.index and ts_secret and Path(ts_secret).exists():
        cfg = json.loads(Path(ts_secret).read_text(encoding='utf-8'))
        for k in range(3):
            try:
                j = requests.post(cfg['url'], json=dict(api_name='sw_daily', token=cfg['token'], params=dict(trade_date=need.strftime('%Y%m%d')), fields=''), timeout=120).json()
                d = pd.DataFrame(j['data']['items'], columns=j['data']['fields'])
                d = d[d.ts_code.str[:6].isin(P.columns)]
                if len(d) >= 100:
                    row = d.set_index(d.ts_code.str[:6]).close.astype(float).reindex(P.columns)
                    P.loc[need] = row; P = P.sort_index(); P.to_parquet(f)
                break
            except Exception:
                time.sleep(5)
    return P


def tda_h2(P, upto, ndays=4):
    R = P.loc[:upto].pct_change(fill_method=None)
    out = {}
    for k in range(len(R) - ndays + 1, len(R) + 1):
        W = R.iloc[k - 30:k]; W = W.loc[:, W.notna().all()]
        if W.shape[1] < 115: continue
        C = np.clip(np.corrcoef(W.values.T), -1, 1); D = np.sqrt(np.maximum(2 * (1 - C), 0)); np.fill_diagonal(D, 0)
        out[R.index[k - 1]] = _h2(D)
    return pd.Series(out)


# ---------------------------------------------------------------- signals
def credit_signal(yoy, T):
    pub = {m: (m + 1).to_timestamp() + pd.Timedelta(days=14) for m in yoy.index}
    avail = [m for m in yoy.dropna().index if pub[m] <= pd.Timestamp(T)]; y = yoy.loc[avail]
    dn = 0
    for k in range(len(y) - 1, 0, -1):
        if y.iloc[k] < y.iloc[k - 1]: dn += 1
        else: break
    s = 1 if y.iloc[-1] > y.iloc[-2] else (-1 if dn <= 12 else 0)
    return s, float(y.iloc[-1]), str(avail[-1]), dn, y


def v3_block(T, D, panel, cache, external_latest=None, ts_secret='C:/ChoiceBridge/live/secrets/tushare.json'):
    c = Path(cache) / 'v3'; c.mkdir(parents=True, exist_ok=True)
    V, err = {}, {}
    days = pd.bdate_range('2015-01-01', T)
    # ---- trend family
    try:
        yoy = loans(c); s, lvl, m, dn, ytab = credit_signal(yoy, T); V['T1'] = dict(s=s, yoy=round(lvl, 4), month=m, down_months=dn)
    except Exception as e: err['T1'] = repr(e); ytab = None
    try:
        fx = usdcnh(c).loc[:T]; s = 1 if fx.iloc[-1] < fx.iloc[-21] else -1; V['T2'] = dict(s=s, now=float(fx.iloc[-1]), ago20=float(fx.iloc[-21]), asof=str(fx.index[-1].date()))
    except Exception as e: err['T2'] = repr(e); fx = None
    tb = D.get('tactical') or {}
    for k, name in (('T4', 'activity'), ('T5', 'cr10'), ('T6', 'pb_momentum')):
        if name in tb and 'signal' in tb[name]: V[k] = dict(s=int(tb[name]['signal']))
    sw = (D.get('swing') or {}).get('000905')
    if sw: V['T7'] = dict(s=1 if not sw.get('falling') else -1)
    # ---- extreme family
    try:
        y = yields(c); dv = div_series(c, panel, days)
        yy = y.reindex(days.union(y.index)).ffill().reindex(days)
        cr = pd.Series(np.nan, index=days)
        if ytab is not None:
            pub = {mm: (mm + 1).to_timestamp() + pd.Timedelta(days=14) for mm in ytab.index}
            for mm in ytab.index:  # credit Δ over 2 months as known on each day
                i = list(ytab.index).index(mm)
                if i >= 2: cr[cr.index >= pub[mm]] = ytab.iloc[i] - ytab.iloc[i - 2]
        DIVadj = dv * (1 + cr.ffill() * 10)
        E2v = DIVadj - (0.5 * yy.cn10y + 0.5 * yy.us10y)
        fxd = fx.reindex(days.union(fx.index)).ffill().reindex(days) if fx is not None else None
        w = 0.3 + 0.4 * rolling_pct(fxd, 252, 120) if fxd is not None else 0.5
        E3v = DIVadj - ((1 - w) * yy.cn10y + w * yy.us10y)
        Q2 = rolling_pct(E2v.dropna(), 1000, 500); Q3 = rolling_pct(E3v.dropna(), 1000, 500)
        # 涨跌周期 state machine on Q2
        st, t0 = 0, None
        for i, (d, q) in enumerate(Q2.items()):
            if np.isnan(q): continue
            if q >= 0.9: st, t0 = 1, i
            elif q <= 0.1: st, t0 = -1, i
            elif st == 1 and (i - t0 > 252 or q < 0.5): st = 0
            elif st == -1 and (i - t0 > 252 or q > 0.5): st = 0
        me = Q3.index.to_series().groupby([Q3.index.year, Q3.index.month]).max()
        last_me = [d for d in me if d < pd.Timestamp(T).replace(day=1)]
        V['E1'] = dict(s=st, Q2=round(float(Q2.iloc[-1]), 3)); V['Q3'] = dict(now=round(float(Q3.iloc[-1]), 3), month_end=round(float(Q3.loc[last_me[-1]]), 3) if last_me else None, w_us=round(float(w.iloc[-1]) if hasattr(w, 'iloc') else w, 3), div=round(float(dv.iloc[-1]), 3))
    except Exception as e: err['E1'] = repr(e)
    lp = (D.get('lppls') or {}).get('000985')
    if lp: V['E2'] = dict(s=1 if lp['neg_rule']['fired'] else 0, neg=lp['neg'], peak40=lp['neg_rule']['peak'])
    try:
        g = gpr(c); q = g.rolling(3650, min_periods=1500).apply(lambda w_: (w_[:-1] < w_[-1]).mean(), raw=True)
        on = False
        for d in g.index:
            if d >= pd.Timestamp(T): break
            if not on and q[d] > 0.99 and g[d] > 200: on = True
            elif on and (q[d] < 0.8 or g[d] < 100): on = False
        gl = g[g.index < pd.Timestamp(T)]
        V['E3'] = dict(s=-1 if on else 0, gpr=round(float(gl.iloc[-1]), 1), q10y=round(float(q[gl.index[-1]]), 3), asof=str(gl.index[-1].date()))
    except Exception as e: err['E3'] = repr(e)
    try:
        P = sw_l2(c, T, ts_secret); h2 = tda_h2(P, T)
        V['E4'] = dict(s=1 if (len(h2) and (h2 <= 1).any()) else 0, h2=int(h2.iloc[-1]) if len(h2) else None, asof=str(h2.index[-1].date()) if len(h2) else None, nodes=int(P.loc[:T].iloc[-1].notna().sum()))
    except Exception as e: err['E4'] = repr(e)
    sw500 = (D.get('swing') or {}).get('000905') or {}
    if sw500:
        V['X3'] = dict(s=(1 if sw500['falling'] else -1) if (sw500.get('p_leg_extends') is not None and sw500['p_leg_extends'] < 0.30) else 0, p=sw500.get('p_leg_extends'))
        V['X2'] = dict(s=1 if 'MACD未转' in sw500.get('state', '') and sw500.get('falling') else (-1 if 'MACD未转' in sw500.get('state', '') else 0))
    gv = lambda k: (V.get(k) or {}).get('s', 0) or 0
    trend = [gv(k) for k in ('T1', 'T2', 'T4', 'T5', 'T6')]
    V['TREND'] = dict(sum=int(sum(trend)), s=int(np.sign(sum(trend))))
    ext = [gv(k) for k in ('E1', 'E2', 'E3', 'E4')]
    V['EXTREME'] = dict(sum=int(sum(ext)), s=int(np.sign(sum(ext))))
    art = 1 + gv('T1') + gv('T2') + gv('E1') + gv('E2') + gv('E3') + gv('E4')
    V['ART'] = dict(sum=int(art), s=int(np.sign(art)))
    tac = gv('T4') + gv('T5') + gv('T6')
    anc = (V.get('Q3') or {}).get('month_end'); xy = None
    if external_latest and '兴证股债性价比3.0分位' in external_latest:
        try: xy = float(str(external_latest['兴证股债性价比3.0分位']['value']).strip('%')) / 100
        except Exception: xy = None
    V['W3D'] = {lab: dict(anchor=a, W=round(float(np.clip(a + 0.1 * tac + 0.1 * gv('E2'), 0, 1)), 3), g=round(g_sigmoid(float(np.clip(a + 0.1 * tac + 0.1 * gv('E2'), 0, 1))), 3))
                for lab, a in (('ours', anc), ('xingzheng', xy)) if a is not None}
    return dict(values=V, errors=err, lines=lines_v3(V, err))


def lines_v3(V, err):
    f = lambda k: {1: '+1', -1: '−1', 0: '0'}.get((V.get(k) or {}).get('s'), '—')
    t1, t2, e1, e3, e4, q3 = (V.get(k, {}) for k in ('T1', 'T2', 'E1', 'E3', 'E4', 'Q3'))
    L = []
    L.append(f"趋势族 {V['TREND']['sum']:+d}：信用 {f('T1')}" + (f"（贷款同比{t1['yoy']:.1%}，代理）" if 'yoy' in t1 else '') + f"、汇率 {f('T2')}" + (f"（USDCNH {t2['now']:.4f}，20日前 {t2['ago20']:.4f}）" if 'now' in t2 else '')
             + f"、成交额 {f('T4')}、集中度 {f('T5')}、PB动量 {f('T6')}；中证500波段 {f('T7')}（不计入）")
    L.append(f"拐点族 {V['EXTREME']['sum']:+d}：性价比周期 {f('E1')}" + (f"（2.0分位{e1['Q2']:.0%}）" if 'Q2' in e1 else '') + f"、LPPLS负泡沫 {f('E2')}、地缘 {f('E3')}" + (f"（GPR {e3['gpr']:.0f}，10年分位{e3['q10y']:.0%}）" if 'gpr' in e3 else '')
             + f"、拓扑 {f('E4')}" + (f"（H2={e4['h2']}）" if e4.get('h2') is not None else '') + f"；补充（非兴证）：段延伸 {f('X3')}、异常态 {f('X2')}")
    w = V.get('W3D', {}); ws = '；'.join(f"{'我们' if k == 'ours' else '兴证'}中枢{v['anchor']:.0%}→{v['g']:.0%}" for k, v in w.items())
    L.append(f"兴证合成 ART {f('ART')}（1+信用+汇率+周期+泡沫+地缘+拓扑={V['ART']['sum']:+d}，通胀预期缺）；3.0三维仓位（Sigmoid）：{ws}" + (f"；性价比3.0 我们{q3['now']:.0%}" if 'now' in q3 else ''))
    if err: L.append('v3 部分失败：' + '、'.join(err))
    return L
