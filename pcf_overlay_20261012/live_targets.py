"""Daily target generator for the live account, v2 (gated): PCF skeleton, optional c5 veto, optional signal tilt.

Changes against v1 (answers 连接与自动交易准备检查 items 1/2/4/5):
  * stock weight = min(console target_fraction, capital_limit / NAV) - 0.001, so the console's parse_targets never rejects the file;
  * names outside the allowed index union (300/500/1000) are dropped; CSI500 share of the target is a soft gate (default 0.70);
  * precheck is written FIRST and is a release gate: the target file is only written when every gate passes;
    otherwise rejected_<exec>.json is written and the exit code is 2;
  * c5: only rows dated signal_date are used; 'excluded' removes a name only when raw_score is finite that day; 'vetoed' as is;
  * signal: only rows dated signal_date; z must already be the final neutralised (and blended) input - nothing is mixed here;
  * calendar check (exec = next session after signal), valuation-price date check, duplicate / non-finite checks,
    console lot rounding (100 shares; SH688 >= 200 or zero), single-security cap, post-lot stock fraction, turnover versus a
    dated account snapshot (missing holdings prices are a failure, never zero);
  * --fallback regenerates one layer down (V5_tilt -> V5 -> P0) when only the layer-specific inputs fail.
Never places orders. Never reads prices after the signal date.
"""
import argparse, datetime as dt, json, hashlib, sys
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
from share_units import compatible_target, odd_reference, target_allowed
import skeleton_k1   # 2026-10-19: K1 zero-row backfill and the sqrt(b) tilt (user instruction)

LAYER_ORDER = ['P0', 'V5', 'V5_tilt']; LOWER = {'V5_tilt': 'V5', 'V5': 'P0', 'tilt': 'P0', 'P0': None}


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def iso(d8): d8 = str(d8); return d8 if '-' in d8 else f'{d8[:4]}-{d8[4:6]}-{d8[6:]}'


def to_dotcode(c):
    c = str(c).strip().upper()
    if c[:2] in ('SH', 'SZ') and c[2:].isdigit(): return c[2:] + '.' + c[:2]
    if '.' in c:
        a, b = c.split('.'); return (a + '.' + b) if a.isdigit() else (b + '.' + a)
    c = c.zfill(6); return c + ('.SH' if c.startswith(('5', '6', '9')) else '.SZ')


def console_code(dot): return dot[-2:] + dot[:6]


def parse_pcf(path):
    root = ET.fromstring(Path(path).read_bytes())
    meta = {x.tag: x.text for x in root if x.tag != 'ComponentList'}
    rows = []
    for x in root.findall('./ComponentList/Component'):
        r = {a.tag: a.text for a in x}
        rows.append(dict(code6=r['InstrumentID'].zfill(6), name=r.get('InstrumentName'), quantity=float(r['Quantity']), flag=str(r['SubstitutionFlag'])))
    df = pd.DataFrame(rows); df['code'] = df.code6.map(to_dotcode)
    return df, dict(trading_day=iso(meta['TradingDay']), valuation_day=iso(meta['PreTradingDay']), nav_per_cu=float(meta['NAVperCU']),
                    nav=float(meta['NAV']), cu=float(meta['CreationRedemptionUnit']), est_cash=float(meta['EstimatedCashComponent']))


def load_members(path):
    p = Path(path)
    if p.suffix == '.parquet':
        m = pd.read_parquet(p); allowed = set(m.symbol.map(to_dotcode)); c500 = set(m.loc[m['index'].eq('csi500'), 'symbol'].map(to_dotcode))
        as_of = sorted(m.snapshot_date.astype(str).unique())
    elif p.suffix == '.json':
        j = json.loads(p.read_text(encoding='utf-8-sig')); allowed = {to_dotcode(c) for c in j['allowed']}; c500 = {to_dotcode(c) for c in j['csi500']}; as_of = [j.get('as_of')]
    else:
        m = pd.read_csv(p, dtype=str); allowed = set(m.code.map(to_dotcode)); c500 = set(m.loc[m['index'].str.lower().eq('csi500'), 'code'].map(to_dotcode)); as_of = sorted(m.get('as_of', pd.Series(['?'])).astype(str).unique())
    return allowed, c500, as_of


def load_table(path):
    p = Path(path); return pd.read_parquet(p) if p.suffix == '.parquet' else pd.read_csv(p, dtype={'code': str, 'date': str})


def signal_day(table, date, required, label):
    """Validate before counting coverage; duplicates never increase coverage."""
    failures=[]
    missing=(set(required)|{'code','date'})-set(table.columns)
    if missing:return pd.DataFrame(),[label+' file lacks columns '+','.join(sorted(missing))]
    day=table.copy();day['code']=day.code.map(to_dotcode);day['date']=day.date.astype(str).str[:10];day=day[day.date==date].copy()
    if day.empty:failures.append(f'{label} file has no rows dated {date}')
    if day.code.duplicated().any():failures.append(label+' has duplicate canonical codes on the signal date')
    return day,failures


def guard_deadband(target, held, keep, ref, member, nav, upper, floor_stock, floor_member):
    """Apply the no-trade band, then hand names back to their full target (largest gap first) until the kept book is
    within the cap and above the stock / CSI500 floors. With nothing kept the book is the original lot target, so it ends feasible."""
    keep = keep.copy(); released = []
    book = lambda: target.where(~keep, held)
    def value(q, m=None): v = q * ref; return float((v[m] if m is not None else v).sum())
    for _ in range(int(keep.sum())):
        q = book(); over = value(q) - upper; short = nav * floor_stock - value(q); short_m = nav * floor_member - value(q, member)
        if over > 0: cand = keep & (held > target); gap = (held - target) * ref
        elif short > 0: cand = keep & (held < target); gap = (target - held) * ref
        elif short_m > 0: cand = keep & (held < target) & member; gap = (target - held) * ref
        else: break
        if not cand.any(): break
        i = gap.where(cand).idxmax(); keep[i] = False; released.append(str(i))
    q = book(); kept_gap = float((np.abs(target - held) * ref)[keep].sum())
    return q, keep, dict(deadband_released_for_limits=released, deadband_kept_gap_cny=round(kept_gap, 2),
                         deadband_book_stock_fraction=round(value(q) / nav, 6), deadband_book_member_fraction=round(value(q, member) / nav, 6))


def build(a, pcf, meta, px, allowed, c500, nav, cap_fraction, mode):
    """Return (weights Series indexed by dot code, info dict, layer_failures list)."""
    info = {}; layer_fail = []
    w = pcf.set_index('code').w.copy()
    outside = w[~w.index.isin(allowed)]; info['dropped_outside_union_names'] = int(len(outside)); info['dropped_outside_union_weight'] = float(outside.sum())
    w = w[w.index.isin(allowed)]
    if w.sum() > 0: w = w / w.sum() * cap_fraction   # replicable, in-universe names carry the whole stock sleeve (console PCF path does the same)
    if mode in ('V5', 'V5_tilt'):
        if not a.c5: layer_fail.append('V5 needs --c5')
        else:
            day,errors=signal_day(load_table(a.c5),a.c5_date or a.signal_date,{'state','raw_score'},'c5');layer_fail+=errors   # c5_date: intraday shadow only (yesterday's c5)
            if not errors:
                raw=pd.to_numeric(day.raw_score,errors='coerce')
                if np.isinf(raw.to_numpy()).any():layer_fail.append('c5 raw_score contains infinity')
                malformed=day.raw_score.notna() & day.raw_score.astype(str).str.strip().ne('') & raw.isna()
                if malformed.any():layer_fail.append('c5 raw_score contains nonnumeric values')
                if not day.state.fillna('').isin(['','core','overweight','excluded','vetoed']).all():layer_fail.append('c5 contains an unknown state')
                usable=pd.Series(np.isfinite(raw.to_numpy()),index=day.index)|day.state.eq('vetoed')
                info['core_overweight_without_raw_score'] = int((day.state.isin(['core','overweight']) & ~usable).sum())   # held anyway; teammate's chain carries the smoothed score forward (~0.7% of rows)
                if (day.state.isin(['core','overweight']) & ~usable & day.code.isin(w.index)).any():layer_fail.append('active c5 state in the PCF universe lacks a finite raw_score')
                cov_names = w.index[w.index.isin(c500)]; cov = float(day.loc[day.code.isin(cov_names)&usable,'code'].nunique() / max(len(cov_names), 1))
                info['c5_rows_signal_date'] = int(len(day)); info['c5_coverage_of_pcf_csi500'] = cov
                if cov < a.c5_min_coverage: layer_fail.append(f'c5 covers {cov:.0%} of PCF CSI500 names < {a.c5_min_coverage:.0%}')
                finite=pd.Series(np.isfinite(raw.to_numpy()),index=day.index)
                bad = set(day.code[(day.state == 'vetoed') | ((day.state == 'excluded') & finite)])
                stale = set(day.code[(day.state == 'excluded') & ~finite])
                removed = w[w.index.isin(bad)]; tot = w.sum(); keep = w[~w.index.isin(bad)].copy()
                if keep.sum() <= 0: layer_fail.append('c5 veto removed every name'); w = keep
                elif a.refill == 'industry':
                    ind = json.loads(Path(a.industry_map).read_text(encoding='utf-8-sig')) if a.industry_map else {}
                    if not ind: layer_fail.append('--refill industry needs --industry-map')
                    pooled = 0.0; same_ind = 0.0
                    for code, wt in removed.items():
                        g = ind.get(code); same = [c for c in keep.index if g is not None and ind.get(c) == g]
                        if same and keep[same].sum() > 0: keep[same] += wt * keep[same] / keep[same].sum(); same_ind += wt
                        else: pooled += wt
                    if pooled > 0: keep += pooled * keep / keep.sum()
                    w = keep / keep.sum() * tot
                    info.update(refill='industry', refill_same_industry_weight=float(same_ind), refill_pooled_weight=float(pooled), industry_map_coverage=float(np.mean([c in ind for c in w.index])) if len(w) else 0.0)
                else: w = keep / keep.sum() * tot; info['refill'] = 'prorata'
                info.update(removed_names=int(len(removed)), removed_weight=float(removed.sum()), excluded_without_raw_score_kept=int(len(stale & set(w.index))))
    if mode in ('tilt', 'V5_tilt'):
        if not a.signal: layer_fail.append('tilt needs --signal')
        else:
            table,errors=signal_day(load_table(a.signal),a.signal_date,{'z'},'signal');layer_fail+=errors
            sig=pd.to_numeric(table.set_index('code').z,errors='coerce') if not errors else pd.Series(dtype=float)
            if len(sig) and not np.isfinite(sig.to_numpy()).all():layer_fail.append('signal z contains missing or non-finite values')
            cov = float(sig.reindex(w.index).notna().mean()) if len(w) else 0.0; info['signal_rows_signal_date'] = int(len(sig)); info['signal_coverage'] = cov
            if cov < a.signal_min_coverage: layer_fail.append(f'signal covers {cov:.0%} of target names < {a.signal_min_coverage:.0%}')
            z = sig.reindex(w.index).fillna(0.0).clip(-2, 2); tot = w.sum()
            if a.tilt_form == 'sqrtb':   # 2026-10-19 T1: additive, proportional to sqrt(w0), long-only (pcf_overlay_20261012/skeleton_k1.py)
                w = skeleton_k1.sqrtb_tilt(w, z, a.tilt_lambda); info['tilt_lambda'] = a.tilt_lambda
            else: w = w * (1 + a.k * z); w = w / w.sum() * tot
            info['tilt_form'] = a.tilt_form; info['tilt_k'] = a.k; info['z_abs_mean'] = float(z.abs().mean())
    w = w[w >= a.min_weight]
    if w.sum() > 0: w = w / w.sum() * cap_fraction
    return w, info, layer_fail


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--pcf', required=True); ap.add_argument('--prices', required=True, help='CSV code,close : closes of the PCF valuation day'); ap.add_argument('--prices-date', required=True)
    ap.add_argument('--calendar', required=True, help='JSON list of trading days (teammate daily_public_pcf/calendar.json)')
    ap.add_argument('--console-config', required=True, help='console settings JSON: target_fraction, capital_limit, max_security_value')
    ap.add_argument('--members', required=True, help='index membership: teammate parquet (symbol,index,snapshot_date) | CSV code,index | JSON {csi500,allowed}')
    ap.add_argument('--account', help='JSON {as_of, nav, positions{SH600000: shares}, prices_now{SH600000: price}}; required unless --shadow')
    ap.add_argument('--account-capture-preexecution', action='store_true', help='use a real timezone-aware preexecution GET observation; preserve its actual capture time')
    ap.add_argument('--shadow', action='store_true', help='no account snapshot: NAV assumed 1e7, turnover not checked, output marked shadow')
    ap.add_argument('--c5'); ap.add_argument('--signal'); ap.add_argument('--mode', default='P0', choices=['P0', 'V5', 'V5_tilt', 'tilt'])
    ap.add_argument('--c5-date', help='intraday SHADOW only (2026-10-16): use c5 rows dated this earlier session instead of the signal date (an intraday rebalance only knows yesterday c5)')
    ap.add_argument('--same-day', action='store_true', help='intraday SHADOW only (2026-10-15): the signal is an intraday slot of the execution day itself; skips the next-session calendar check and marks the file shadow_intraday')
    ap.add_argument('--refill', default='prorata', choices=['prorata', 'industry'], help='how removed (c5) weight is given back: pro-rata over all remaining names, or within the same 申万一级 industry (2026-10-15 industry_refill_v1)')
    ap.add_argument('--industry-map', help='JSON {dotcode: industry label}; required with --refill industry')
    ap.add_argument('--tilt-form', default='mult', choices=['mult', 'sqrtb'], help="mult: w*(1+k z) (original); sqrtb: w + lambda*sqrt(w/mean w)*(z - mean z), clipped at 0 (2026-10-19 T1)")
    ap.add_argument('--tilt-lambda', type=float, help='sqrtb tilt strength (fixed, calibrated in outputs/skeleton_v5_replay_20261019)')
    ap.add_argument('--backfill-holdings', help='dir of 563030 report holdings (实盘日更/config/efund_holdings): fill PCF zero-quantity rows from the latest available report (2026-10-19 K1)')
    ap.add_argument('--k', type=float, default=.5); ap.add_argument('--fallback', action='store_true', help='on layer-specific input failure, generate one layer down')
    ap.add_argument('--deadband-lots', type=int, default=1, help='lots mode: keep the held quantity when |target-held| <= this many lots (avoids one-lot churn)')
    ap.add_argument('--deadband-cny', type=float, default=0.0, help='lots mode: also keep the held quantity when |target-held| x price < this many CNY (2026-10-13 study: 10000); exits and new names always trade; released name by name when the kept book would break the stock/CSI500 floors or the cap')
    ap.add_argument('--floor-stock', type=float, default=0.905, help='deadband guard: kept book must stay >= this stock fraction of NAV (executor floor 0.90)')
    ap.add_argument('--floor-member', type=float, default=0.805, help='deadband guard: kept book must stay >= this CSI500 fraction of NAV (executor floor 0.80)')
    ap.add_argument('--deadband-max-drift', type=float, default=0.005, help='deadband guard: kept book stock / CSI500 fractions stay within this of the full lot target (no systematic cash drift)')
    ap.add_argument('--lots', action='store_true', help="write mode='shares' with largest-remainder lot rounding (needs --account); default mode='weights' leaves rounding to the console")
    ap.add_argument('--overlay', help='CSV code,delta: weight deltas added to the final layer weights before lot rounding (2026-10-19 beta tilt, timing_research_20261017/beta_tilt_live.py); every code must be in the target, deltas must sum to ~0')
    ap.add_argument('--exec-date', required=True); ap.add_argument('--signal-date', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--strategy-id', default='pcf_overlay_v4'); ap.add_argument('--min-weight', type=float, default=0.0005)
    ap.add_argument('--min-member-share', type=float, default=0.70); ap.add_argument('--min-names', type=int, default=80); ap.add_argument('--max-single', type=float, default=0.05)
    ap.add_argument('--max-turnover', type=float, default=0.40, help='two-way turnover gate; pass 0.70 on a declared layer-switch day'); ap.add_argument('--c5-min-coverage', type=float, default=0.80); ap.add_argument('--signal-min-coverage', type=float, default=0.80)
    a = ap.parse_args(argv)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); fail = []
    # ---- dates and calendar
    cal = json.loads(Path(a.calendar).read_text(encoding='utf-8-sig')); cal = sorted(iso(d) for d in cal)
    if a.signal_date not in cal: fail.append(f'signal date {a.signal_date} not in trading calendar')
    if a.exec_date not in cal: fail.append(f'execution date {a.exec_date} not in trading calendar')
    if a.same_day:
        if a.exec_date != a.signal_date: fail.append('--same-day requires exec date == signal date')
        if not a.shadow: fail.append('--same-day is shadow-only (pass --shadow)')
    if a.c5_date:
        if not (a.shadow and a.same_day): fail.append('--c5-date is intraday-shadow-only (pass --shadow --same-day)')
        elif a.c5_date not in cal or a.c5_date >= a.signal_date: fail.append(f'--c5-date {a.c5_date} must be a trading day before the signal date')
    elif a.signal_date in cal and a.exec_date in cal and cal.index(a.exec_date) != cal.index(a.signal_date) + 1: fail.append('execution date is not the next session after the signal date')
    pcf, meta = parse_pcf(a.pcf); backfill_info = {}
    if a.tilt_form == 'sqrtb' and not (a.tilt_lambda and a.tilt_lambda > 0): fail.append('--tilt-form sqrtb needs a positive --tilt-lambda')
    if a.backfill_holdings:
        hold, hmeta = skeleton_k1.latest_report(a.backfill_holdings, meta['trading_day'])
        if hold is None: fail.append('--backfill-holdings: no report available on the PCF day')
        else: pcf, backfill_info = skeleton_k1.backfill(pcf, meta['cu'], hold, hmeta)
    if 'backfilled' not in pcf: pcf['backfilled'] = False
    if meta['trading_day'] != a.signal_date: fail.append(f"PCF trading day {meta['trading_day']} != signal date {a.signal_date}")
    price_date_mismatch = a.prices_date != meta['valuation_day']
    if price_date_mismatch and not a.shadow: fail.append(f"prices date {a.prices_date} != PCF valuation day {meta['valuation_day']}")
    # ---- console config and account
    cfg = json.loads(Path(a.console_config).read_text(encoding='utf-8-sig')); tf = float(cfg['target_fraction']); cap_cny = float(cfg['capital_limit']); single_cny = float(cfg['max_security_value'])
    acct = None; nav = 1e7; prices_now = {}
    if a.account:
        acct = json.loads(Path(a.account).read_text(encoding='utf-8-sig')); nav = float(acct['nav']); prices_now = {k: float(v) for k, v in acct.get('prices_now', {}).items()}
        if a.lots and any(type(n) is not int or n < 0 for n in acct.get('positions', {}).values()): fail.append('account holdings must use nonnegative integer shares')
        if a.account_capture_preexecution:
            try:
                captured=dt.datetime.fromisoformat(acct['as_of']);day=captured.astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
                if captured.tzinfo is None or not a.signal_date<=day<a.exec_date or acct.get('provenance')!='current_preexecution_GET_observation':fail.append('account capture is not a dated preexecution GET observation')
            except (KeyError,ValueError,TypeError):fail.append('account capture timestamp is invalid')
        elif str(acct.get('as_of', ''))[:10] != a.signal_date: fail.append(f"account snapshot as_of {acct.get('as_of')} != signal date")
        if nav <= 0: fail.append('account nav must be positive')
    elif not a.shadow: fail.append('account snapshot missing (use --account, or --shadow for a non-release run)')
    cap_fraction = min(tf, cap_cny / nav) - 0.001
    # ---- valuation prices
    px = pd.read_csv(a.prices, dtype={'code': str}); px['code'] = px.code.map(to_dotcode); px = px.drop_duplicates('code').set_index('code').close.astype(float)
    allowed, c500, member_as_of = load_members(a.members)
    pcf = pcf[(pcf.flag.isin(['1', '3']) | pcf.backfilled) & pcf.code.isin(allowed)].copy(); pcf['close'] = pcf.code.map(px)   # out-of-union names never need prices
    nopx = pcf.close.isna() | ~np.isfinite(pcf.close) | (pcf.close <= 0)
    if pcf.backfilled.any():   # a backfilled row without a valuation price is simply not backfilled (never a release failure)
        drop = pcf.backfilled & nopx; backfill_info.update(backfill_in_union=int(pcf.backfilled.sum()), backfill_dropped_no_price=sorted(pcf.code[drop]))
        pcf = pcf[~drop].copy(); nopx = nopx[~drop]
    missing = pcf[nopx]
    if len(missing): fail.append('missing valuation prices for ' + ','.join(missing.code))
    if pcf.code.duplicated().any(): fail.append('duplicate codes in PCF')
    pcf['w'] = pcf.quantity * pcf.close / meta['nav_per_cu']
    if a.backfill_holdings and 'backfill_report' in backfill_info:
        backfill_info.update(backfill_names=int(pcf.backfilled.sum()), backfill_weight_of_basket=float(pcf.w[pcf.backfilled].sum() / max(pcf.w.sum(), 1e-12)))
    if fail:
        rej = dict(execution_date=a.exec_date, signal_date=a.signal_date, mode=a.mode, released=False, failures=fail)
        (out / f'precheck_{a.exec_date}.json').write_text(json.dumps(rej, ensure_ascii=False, indent=2), encoding='utf-8')
        (out / f'rejected_{a.exec_date}.json').write_text(json.dumps(rej, ensure_ascii=False, indent=2), encoding='utf-8'); print(json.dumps(rej, ensure_ascii=False)); return 2
    # ---- build (with optional fallback down the layers)
    mode = a.mode; fallback_from = []
    while True:
        w, info, layer_fail = build(a, pcf, meta, px, allowed, c500, nav, cap_fraction, mode)
        if not layer_fail or not a.fallback or LOWER[mode] is None: break
        fallback_from.append(dict(mode=mode, reasons=layer_fail)); mode = LOWER[mode]
    fail += layer_fail
    if a.overlay and not fail:   # 2026-10-19: beta tilt (reports/P3 ... 实盘补充); applied on the same weights the deltas were computed from
        d = pd.read_csv(a.overlay, dtype={'code': str}); d['code'] = d.code.map(to_dotcode); d = d.groupby('code').delta.sum().astype(float)
        stray = d[~d.index.isin(w.index) & (d.abs() > 1e-9)]
        if len(stray): fail.append('overlay names not in the target: ' + ','.join(stray.index[:5]))
        elif not np.isfinite(d.to_numpy()).all() or abs(float(d.sum())) > 1e-6: fail.append('overlay deltas must be finite and sum to zero')
        else:
            tot = w.sum(); w2 = (w + d.reindex(w.index).fillna(0.0)).clip(lower=0.0); w2 = w2[w2 >= a.min_weight]; w2 = w2 / w2.sum() * tot
            info.update(overlay=str(a.overlay), overlay_sha256=sha(a.overlay), overlay_l1_half=float((w2.reindex(w.index).fillna(0.0) - w).abs().sum() / 2))
            w = w2
    # ---- console-equivalent lot rounding and caps
    ref = pd.Series({c: prices_now.get(console_code(c), np.nan) for c in w.index}).fillna(px.reindex(w.index))
    qty = np.floor(nav * w / ref / 100) * 100; qty[(w.index.str.startswith('688')) & (qty < 200)] = 0
    if a.lots:
        if acct is None: fail.append('--lots needs a dated --account snapshot (NAV must be known)')
        else:  # largest-remainder: add lots where the relative deficit is largest while total stays under the console caps
            budget = min(cap_cny, nav * tf) - nav * 0.004; lot = np.where(w.index.str.startswith('688') & (qty == 0), 200, 100) * ref
            deficit = nav * w - qty * ref
            for _ in range(len(w) * 3):
                room = budget - float((qty * ref).sum()); cand = (deficit > 0) & (lot <= room) & ((qty * ref + lot) <= single_cny)
                if not cand.any(): break
                i = (deficit / lot).where(cand).idxmax(); add = 200 if (i.startswith('688') and qty[i] == 0) else 100
                qty[i] += add; deficit[i] -= add * ref[i]
    deadband_info = {}
    if a.lots and acct is not None and (a.deadband_lots > 0 or a.deadband_cny > 0):
        held = pd.Series({c: float(acct.get('positions', {}).get(console_code(c), 0)) for c in w.index}); band = np.where(w.index.str.startswith('688'), 200, 100) * a.deadband_lots
        keep = (held > 0) & (held * ref <= single_cny) & ((np.abs(qty - held) <= band) | ((qty > 0) & (np.abs(qty - held) * ref < a.deadband_cny)))
        full = float((qty * ref).sum()) / nav; full_m = float((qty * ref)[w.index.isin(c500)].sum()) / nav
        qty, keep, deadband_info = guard_deadband(qty, held, keep, ref, w.index.isin(c500), nav,
                                                  upper=min(min(cap_cny, nav * tf) - nav * 0.004, nav * (full + a.deadband_max_drift)),
                                                  floor_stock=max(a.floor_stock, full - a.deadband_max_drift), floor_member=max(a.floor_member, full_m - a.deadband_max_drift))
        deadband_info.update(full_target_stock_fraction=round(full, 6), full_target_member_fraction=round(full_m, 6))
        pre_deadband_names = int(keep.sum())
    else: pre_deadband_names = 0
    odd_adjustments = []
    holdings = {c: int(n) for c, n in acct.get('positions', {}).items()} if a.lots and acct else {}
    for dot in qty.index:
        code = console_code(dot); held_n = holdings.get(code, 0); before = int(qty[dot])
        after = compatible_target(code, before, held_n)
        if held_n % 100 and after % 100:
            odd_adjustments.append(dict(code=code, held_shares=held_n, requested_shares=before, target_shares=after))
        qty[dot] = after
    value = qty * ref; post_lot = float(value.sum() / nav)
    pre = dict(execution_date=a.exec_date, signal_date=a.signal_date, mode=mode, requested_mode=a.mode, fallback_from=fallback_from, shadow=bool(a.shadow and not a.account),
               nav_used=nav, console=dict(target_fraction=tf, capital_limit=cap_cny, max_security_value=single_cny), cap_fraction=cap_fraction,
               names=int(len(w)), stock_weight=float(w.sum()), max_single=float(w.max()) if len(w) else 0.0, max_single_code=str(w.idxmax()) if len(w) else None,
               post_lot_stock_fraction=post_lot, post_lot_value_cny=float(value.sum()), max_single_value_cny=float(value.max()) if len(w) else 0.0,
               csi500_share_of_target=float(w[w.index.isin(c500)].sum() / w.sum()) if len(w) else 0.0, csi500_share_of_nav=float(w[w.index.isin(c500)].sum()),
               membership_as_of=member_as_of, pcf_trading_day=meta['trading_day'], pcf_valuation_day=meta['valuation_day'], prices_date=a.prices_date, price_date_mismatch=bool(price_date_mismatch), **backfill_info, deadband_kept_names=pre_deadband_names, deadband_cny=a.deadband_cny, **deadband_info, odd_lot_compatibility=odd_adjustments, **info)
    if len(w) < a.min_names: fail.append(f'{len(w)} names < {a.min_names}')
    if not np.isfinite(w.to_numpy()).all() or (w <= 0).any(): fail.append('non-finite or non-positive weights')
    if w.sum() > tf + 1e-9: fail.append('stock weight exceeds console target_fraction')
    if w.sum() < cap_fraction - 0.02: fail.append(f'stock weight {w.sum():.3f} below cap_fraction-2% ({cap_fraction - 0.02:.3f})')
    if len(w) and w.max() > a.max_single: fail.append(f'max single weight {w.max():.3%} > {a.max_single:.0%}')
    if len(w) and value.max() > single_cny + .01: fail.append(f'max single value {value.max():.0f} > console max_security_value {single_cny:.0f}')
    if value.sum() > min(cap_cny, nav * tf) + .01: fail.append('post-lot value exceeds console capital/fraction cap')
    if post_lot < cap_fraction - 0.07: fail.append(f'post-lot stock fraction {post_lot:.3f} < cap_fraction-7%')  # weights mode: high-priced names lose up to one lot each; console floor top-up buys the remainder in the same universe
    if pre['csi500_share_of_target'] < a.min_member_share: fail.append(f"CSI500 share {pre['csi500_share_of_target']:.1%} < {a.min_member_share:.0%}")
    if acct is not None:
        pos = {k: float(v) for k, v in acct.get('positions', {}).items()}
        nopx = [c for c, s in pos.items() if s > 0 and not (c in prices_now and prices_now[c] > 0)]
        if nopx: fail.append('held names without prices_now: ' + ','.join(nopx))
        else:
            cur = {c: s * prices_now[c] / nav for c, s in pos.items() if s > 0}
            tgt = {console_code(c): float(qty[c] * ref[c] / nav) for c in w.index} if a.lots else {console_code(c): float(v) for c, v in w.items()}
            turn = sum(abs(tgt.get(c, 0) - cur.get(c, 0)) for c in set(cur) | set(tgt))
            pre.update(current_stock_fraction=float(sum(cur.values())), planned_two_way_turnover=float(turn), names_to_sell_out=int(sum(1 for c in cur if c not in tgt)))
            if turn > a.max_turnover and sum(cur.values()) > 0.5: fail.append(f'planned turnover {turn:.1%} > {a.max_turnover:.0%}')
    pre['released'] = not fail; pre['failures'] = fail
    (out / f'precheck_{a.exec_date}.json').write_text(json.dumps(pre, ensure_ascii=False, indent=2), encoding='utf-8')
    if fail:
        (out / f'rejected_{a.exec_date}.json').write_text(json.dumps(pre, ensure_ascii=False, indent=2), encoding='utf-8'); print(json.dumps(pre, ensure_ascii=False)); return 2
    if a.lots:
        targets = [dict(code=console_code(c), shares=int(qty[c]), weight=round(float(v), 6)) for c, v in w.sort_values(ascending=False).items() if qty[c] > 0]
        assert all(target_allowed(t['code'], t['shares'], holdings.get(t['code'], 0)) for t in targets)
    else:
        targets = [dict(code=console_code(c), weight=round(float(v), 6)) for c, v in w.sort_values(ascending=False).items()]
    assert sum(t['weight'] for t in targets) <= tf + 1e-8 and len({t['code'] for t in targets}) == len(targets)
    payload = dict(strategy_id=a.strategy_id, execution_date=a.exec_date, signal_date=a.signal_date, mode='shares' if a.lots else 'weights', targets=targets,
                   generated_from=dict(pcf=str(a.pcf), pcf_sha256=sha(a.pcf), pcf_trading_day=meta['trading_day'], pcf_valuation_day=meta['valuation_day'],
                                       prices=str(a.prices), prices_sha256=sha(a.prices), c5=a.c5 and str(a.c5), c5_sha256=a.c5 and sha(a.c5),
                                       signal=a.signal and str(a.signal), signal_sha256=a.signal and sha(a.signal), members=str(a.members), members_sha256=sha(a.members),
                                       console_config_sha256=sha(a.console_config), account_as_of=acct and acct.get('as_of'), mode=mode, requested_mode=a.mode, k=a.k, tilt_form=a.tilt_form, tilt_lambda=a.tilt_lambda, backfill_report=backfill_info.get('backfill_report'), cap_fraction=cap_fraction, shadow=pre['shadow'], lots=bool(a.lots)))
    if a.overlay: payload['generated_from'].update(overlay=str(a.overlay), overlay_sha256=sha(a.overlay))   # unchanged file layout when no overlay
    if a.lots and acct:
        payload['odd_lot_reference'] = odd_reference(holdings, a.signal_date, sha(a.account))
    (out / f'target_{a.exec_date}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    pd.DataFrame(targets).to_csv(out / f'target_{a.exec_date}.csv', index=False)
    print(json.dumps(pre, ensure_ascii=False)); return 0


if __name__ == '__main__':
    sys.exit(main())
