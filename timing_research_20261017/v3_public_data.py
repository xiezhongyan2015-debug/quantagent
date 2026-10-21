"""v3 公开数据（2026-10-17）：中美 10 年国债、美元兑离岸人民币、人民币贷款（由月度新增倒推余额同比）、中证全指（万得全A 代理）、GPR 日度地缘风险。
输出 outputs/timing_v3_20261017/public/*.csv。来源：东方财富 datacenter / push2his、腾讯行情、Caldara & Iacoviello GPR（matteoiacoviello.com）。"""
import json, time, sys
from pathlib import Path
import pandas as pd, requests

ROOT = Path(__file__).resolve().parents[1]; OUT = ROOT / 'outputs/timing_v3_20261017/public'; OUT.mkdir(parents=True, exist_ok=True)
H = {'User-Agent': 'Mozilla/5.0'}


def em_report(name, sort):
    rows, p = [], 1
    while True:
        j = requests.get('https://datacenter-web.eastmoney.com/api/data/v1/get', params=dict(reportName=name, columns='ALL', sortColumns=sort, sortTypes=-1, pageSize=500, pageNumber=p), headers=H, timeout=30).json()
        r = j['result']; rows += r['data']
        if p >= r['pages']: break
        p += 1; time.sleep(0.3)
    return pd.DataFrame(rows)


def yields():
    x = em_report('RPTA_WEB_TREASURYYIELD', 'SOLAR_DATE')
    x['date'] = pd.to_datetime(x.SOLAR_DATE)
    y = x.rename(columns={'EMM00166466': 'cn10y', 'EMG00001310': 'us10y', 'EMM00588704': 'cn2y'})[['date', 'cn10y', 'us10y', 'cn2y']].sort_values('date')
    y.to_csv(OUT / 'yields.csv', index=False); return y


def usdcnh():
    j = requests.get('https://push2his.eastmoney.com/api/qt/stock/kline/get', params=dict(secid='133.USDCNH', fields1='f1,f2,f3', fields2='f51,f52,f53', klt=101, fqt=0, beg='20100101', end='20261231'), headers=H, timeout=30).json()
    k = [r.split(',') for r in j['data']['klines']]
    df = pd.DataFrame(k, columns=['date', 'open', 'close']); df['date'] = pd.to_datetime(df.date); df['close'] = df.close.astype(float)
    df[['date', 'close']].to_csv(OUT / 'usdcnh.csv', index=False); return df


def loans(anchor_month='2026-09', anchor_balance=2834400.0):
    """monthly new RMB loans (亿元) -> balance by backward accumulation from the published Sept-2026 balance 283.44 万亿 -> YoY.
    Proxy for 兴证's 中长期贷款余额同比 (not available publicly as a series)."""
    x = em_report('RPT_ECONOMY_RMB_LOAN', 'REPORT_DATE')
    x['month'] = pd.to_datetime(x.REPORT_DATE).dt.to_period('M'); s = x.set_index('month').RMB_LOAN.astype(float).sort_index()
    s = s[~s.index.duplicated()]
    full = pd.period_range(s.index.min(), s.index.max(), freq='M'); s = s.reindex(full)
    bal = pd.Series(index=full, dtype=float); a = pd.Period(anchor_month, 'M'); bal[a] = anchor_balance
    for p in reversed(full[full < a]): bal[p] = bal[p + 1] - s[p + 1]
    for p in full[full > a]: bal[p] = bal[p - 1] + s[p]
    yoy = bal / bal.shift(12) - 1
    out = pd.DataFrame(dict(new_loans=s, balance=bal, yoy=yoy)); out.index.name = 'month'; out.to_csv(OUT / 'loans.csv'); return out


def csi_all():
    out = []; s = pd.Timestamp('2009-01-01')
    while s < pd.Timestamp('2026-12-31'):
        e = s + pd.Timedelta(days=1100)
        j = requests.get('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get', params={'param': f'sh000985,day,{s.date()},{e.date()},800,qfq'}, timeout=20).json()
        k = j['data']['sh000985']; out += (k.get('day') or k.get('qfqday') or []); s = e + pd.Timedelta(days=1)
    df = pd.DataFrame([r[:6] for r in out], columns=['date', 'open', 'close', 'high', 'low', 'volume']).drop_duplicates('date')
    df.to_csv(OUT / 'csi_all_000985.csv', index=False); return df


def gpr(xls_reader_python=None):
    p = OUT / 'data_gpr_daily_recent.xls'
    r = requests.get('https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls', headers=H, timeout=60); p.write_bytes(r.content)
    return p


if __name__ == '__main__':
    for f in (yields, usdcnh, loans, csi_all, gpr):
        try:
            r = f(); print(f.__name__, 'ok', (r.shape if hasattr(r, 'shape') else r))
        except Exception as e:
            print(f.__name__, 'FAILED', type(e).__name__, e)
