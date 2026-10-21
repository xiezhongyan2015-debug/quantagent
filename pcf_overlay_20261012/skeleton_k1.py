"""K1 (2026-10-19, user instruction): put back the PCF rows that 563030 lists with zero shares.

The PCF writes 0 shares ("必须现金替代", amount 0) for every holding worth less than 100 shares per creation unit
(high-priced names); the other names are scaled up ~1.1-1.2x so the basket still adds up. At 2026-06-30 these
zero rows were 16.7% of the fund's NAV. This module rebuilds them from the fund's own periodic reports:

  per-unit shares of a zero row = report shares / fund total shares x creation unit

Rules (point in time, no guessing):
  * only rows that are IN TODAY'S PCF with quantity 0 are filled (the fund still lists the name, so it still holds it);
    names absent from the PCF are never added, names with positive PCF quantity are never changed;
  * the report used is the latest one whose availability date (period end + AVAIL_DAYS) is on or before the PCF day;
  * a zero row missing from that report stays at zero.
The normal live_targets normalisation then scales the listed names back down, so the skeleton has the true mix.

Also: sqrtb_tilt(), the T1 tilt w = max(w0 + lam*sqrt(w0/mean w0)*(z - mean z), 0), renormalised to the same total.
"""
import re, json
from pathlib import Path
import numpy as np
import pandas as pd

AVAIL_DAYS = {'06-30': 62, '12-31': 91}   # interim reports are due by end of August, annual reports by end of March
ROW = re.compile(r'^\s*\d+\s+(\d{6})\s+(\S+)\s+([\d,]+)\s+([\d,]+\.\d+)\s+([\d\.]+)\s*$', re.M)


def parse_report(text):
    """Full stock holdings (index + active sections) of a 563030 interim/annual report text."""
    shares = re.findall(r'报告期末基金份额总额\s+([\d,\.]+)\s*份', text)
    end = re.findall(r'报告截止日\s*(\d{4})\s*年\s*(\d+)\s*月\s*(\d+)\s*日', text)
    if not shares or not end:
        raise ValueError('report lacks fund shares or period end')
    rows = [dict(code6=c, name=n, shares=int(s.replace(',', '')), value=float(v.replace(',', '')), pct_nav=float(p)) for c, n, s, v, p in ROW.findall(text)]
    if not rows:
        raise ValueError('no holdings rows parsed')
    df = pd.DataFrame(rows).groupby('code6', as_index=False).agg(name=('name', 'first'), shares=('shares', 'sum'), value=('value', 'sum'), pct_nav=('pct_nav', 'sum'))
    y, m, d = end[0]; period_end = f'{int(y):04d}-{int(m):02d}-{int(d):02d}'
    avail = (pd.Timestamp(period_end) + pd.Timedelta(days=AVAIL_DAYS[period_end[5:]])).strftime('%Y-%m-%d')
    return df, dict(period_end=period_end, available_from=avail, fund_shares=float(shares[0].replace(',', '')), rows=len(df), pct_nav_sum=float(df.pct_nav.sum()))


def write_holdings(report_txt, out_dir):
    df, meta = parse_report(Path(report_txt).read_text(encoding='utf-8', errors='replace'))
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    stem = 'holdings_' + meta['period_end'].replace('-', '')
    df.to_csv(out_dir / f'{stem}.csv', index=False)
    (out_dir / f'{stem}.json').write_text(json.dumps({**meta, 'source': Path(report_txt).name}, ensure_ascii=False, indent=1), encoding='utf-8')
    return meta


def latest_report(holdings_dir, pcf_day):
    """(holdings DataFrame, meta) of the newest report available on pcf_day, or (None, None)."""
    best = None
    for j in sorted(Path(holdings_dir).glob('holdings_*.json')):
        meta = json.loads(j.read_text(encoding='utf-8'))
        if meta['available_from'] <= pcf_day and (best is None or meta['period_end'] > best[1]['period_end']):
            best = (j.with_suffix('.csv'), meta)
    if best is None:
        return None, None
    return pd.read_csv(best[0], dtype={'code6': str}), best[1]


def backfill(pcf, cu, holdings, meta):
    """pcf: DataFrame with code6, quantity (live_targets.parse_pcf). Returns (new pcf, info). Only zero rows change."""
    pcf = pcf.copy(); pcf['backfilled'] = False
    if holdings is None:
        return pcf, dict(backfill='none_available')
    per_unit = holdings.set_index('code6').shares / meta['fund_shares'] * cu
    zero = pcf.quantity.eq(0) & pcf.code6.isin(per_unit.index)
    pcf.loc[zero, 'quantity'] = pcf.loc[zero, 'code6'].map(per_unit).to_numpy()
    pcf.loc[zero, 'backfilled'] = True
    info = dict(backfill_report=meta['period_end'], backfill_available_from=meta['available_from'], backfill_rows=int(zero.sum()),
                zero_rows=int(pcf.quantity.eq(0).sum() + zero.sum()), zero_rows_not_in_report=int((pcf.quantity == 0).sum()))
    return pcf, info


def sqrtb_tilt(w, z, lam):
    """T1: additive tilt proportional to sqrt(w0); same total, long-only."""
    tot = float(w.sum()); m = float(w.mean())
    zc = z - z.mean()
    w2 = (w + lam * np.sqrt(w / m) * zc).clip(lower=0.0)
    return w2 / w2.sum() * tot
