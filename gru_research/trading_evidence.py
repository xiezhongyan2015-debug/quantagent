"""Normalize actual public responses and audit coverage without inventing rows.

The source collector may still be running. Unrequested queries remain explicit
in coverage, and raw source nulls/precision are preserved in separate tables.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
sys.path.insert(0, str(ROOT / 'research_v3/src'))
import numpy as np
import pandas as pd
from scenario_action_catalog import ACTION_FIELDS, distribution_terms, vendor_action
from research_action_catalog import ResearchActionCatalog, verified_compound_rows
from distribution_prefix import parse_distribution_prefix
from acquire_trading_evidence import key, atomic_json, OUTPUT


def normalize(directory):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(OUTPUT.resolve()):
        raise ValueError('research evidence directory required')
    specs = json.loads((directory / 'planned_queries.json').read_text())
    raw_rows = []; basic_rows = []; query_coverage = []; action_coverage = []
    action_sources = defaultdict(list); query_bytes = {}; calendar_rows = []
    for spec in specs:
        path = directory / (key(spec) + '.json')
        data = path.read_bytes() if path.exists() else None
        q = json.loads(data) if data else None
        sha = hashlib.sha256(data).hexdigest() if data else None
        success = q is not None and q.get('error_code') == '0'
        rows = q.get('rows', []) if q else []
        fields = q.get('fields', []) if q else []
        status = ('provider_success_nonempty' if rows else 'provider_success_empty') if success else ('provider_failed' if q else 'not_requested_yet')
        receipt = {'api': spec['api'], 'params_json': json.dumps(spec['params'], sort_keys=True),
            'query_status': status, 'provider_rows': len(rows) if q else None,
            'source_sha256': sha, 'source_path': str(path) if q else None,
            'error_code': q.get('error_code') if q else None, 'error_msg': q.get('error_msg') if q else None}
        query_coverage.append(receipt)
        if data: query_bytes[sha] = data
        if spec['api'] == 'query_dividend_data':
            symbol = spec['params']['code'].replace('.', '')
            cov = {**receipt, 'symbol': symbol, 'operating_year': int(spec['params']['year']),
                'year_type': spec['params']['yearType'], 'nonempty_identity_matches_query':
                success and bool(rows) and all(v[fields.index('code')] == spec['params']['code'] for v in rows)}
            action_coverage.append(cov)
            if success and tuple(sorted(fields)) != tuple(sorted(ACTION_FIELDS)):
                raise ValueError('complete vendor action schema changed; no silent field dropping')
        if not success: continue
        if any(len(row) != len(fields) for row in rows):
            raise ValueError('malformed source row width')
        for index, values in enumerate(rows):
            original = dict(zip(fields, values))
            provenance = {'source_sha256': sha, 'source_path': str(path), 'source_row': index,
                'api': spec['api'], 'params': spec['params'], 'fetched_end_utc': q['fetched_end_utc']}
            if spec['api'] == 'query_history_k_data_plus':
                if original['code'] != spec['params']['code'] or original['adjustflag'] != '3':
                    raise ValueError('raw identity/adjustment mismatch')
                raw_rows.append({**original, 'symbol': original['code'].replace('.', ''),
                    'source_sha256': sha, 'source_row': index, 'source_path': str(path)})
            elif spec['api'] == 'query_stock_basic':
                basic_rows.append({**original, 'symbol': original['code'].replace('.', ''), 'source_sha256': sha,
                    'source_path': str(path), 'ipo_date': original.get('ipoDate'),
                    'entity_delisting_date_candidate': original.get('outDate')})
            elif spec['api'] == 'query_trade_dates':
                calendar_rows.append({**original, 'source_sha256': sha})
            elif spec['api'] == 'query_dividend_data':
                action_sources[(original['code'], original['dividOperateDate'])].append((original, provenance))
    normalized = directory / 'normalized'; normalized.mkdir(exist_ok=True)
    def save(name, rows):
        pd.DataFrame(rows).to_parquet(normalized / (name + '.parquet'), index=False)
    save('query_coverage', query_coverage); save('corporate_action_query_coverage', action_coverage)
    save('raw_daily_source_strings', raw_rows); save('lifecycle', basic_rows); save('calendar_source', calendar_rows)
    raw = pd.DataFrame(raw_rows)
    if len(raw):
        if raw.duplicated(['date', 'symbol']).any(): raise ValueError('raw duplicate key requires reconciliation')
        raw['date'] = pd.to_datetime(raw.date)
        for column in ['open', 'high', 'low', 'close', 'preclose', 'volume', 'amount', 'tradestatus', 'isST', 'turn', 'pctChg']:
            raw[column] = pd.to_numeric(raw[column], errors='coerce')
        raw['cross_source_price_conflict'] = False
        raw['cross_source_check_status'] = 'NOT_CHECKED_AGAINST_INDEPENDENT_SOURCE'
        raw.to_parquet(normalized / 'raw_daily.parquet', index=False)
    actions = []; conflicts = []; action_checks = []
    for identity, originals in sorted(action_sources.items()):
        merged = {}; bad = []
        for field in ACTION_FIELDS:
            nonempty = sorted({row.get(field, '') for row, _ in originals if str(row.get(field, '')).strip()})
            if len(nonempty) > 1: bad.append(field)
            merged[field] = nonempty[0] if nonempty else ''
        provenance = [p for _, p in originals]
        merged['source_provenance_json'] = json.dumps(provenance, sort_keys=True)
        if bad:
            if verified_compound_rows(identity, [r for r, _ in originals]):
                distinct = defaultdict(list)
                for original, src in originals:
                    distinct[tuple(original.get(f, '') for f in ACTION_FIELDS)].append((original, src))
                for components in distinct.values():
                    actions.append({**components[0][0],
                        'source_provenance_json': json.dumps([p for _, p in components], sort_keys=True)})
            else:
                conflicts.append({'code': identity[0], 'ex_date': identity[1], 'conflicting_fields': bad,
                    'all_original_rows': [r for r, _ in originals], 'sources': provenance})
            continue
        actions.append(merged)
    for merged in actions:
        check = {'code': merged['code'], 'ex_date': merged['dividOperateDate'], 'source_fields_unchanged': True,
            'cash_unit_check': 'UNRESOLVED', 'scenario_action_ready': False, 'missing_fields': []}
        try:
            cash, shares = distribution_terms(merged)
            parsed = parse_distribution_prefix(merged['dividCashStock'])
            check.update({'cash_unit_check': 'PER_SHARE_NUMERIC_EQUALS_COMPLETE_PER_TEN_TEXT_DIVIDED_BY_10',
                'gross_cash_per_share': str(cash), 'share_distribution_ratio': str(shares),
                'bonus_per_share': str(parsed['bonus']), 'capital_conversion_per_share': str(parsed['reserve']),
                'cash_tax_after_field_final_net': False})
            needed = ['dividPlanDate', 'dividRegistDate', 'dividOperateDate']
            if cash: needed.append('dividPayDate')
            if shares: needed.append('dividStockMarketDate')
            check['missing_fields'] = [f for f in needed if not str(merged[f]).strip()]
            vendor_action(merged)
            check['scenario_action_ready'] = True
        except (ValueError, ArithmeticError) as exc:
            check['scenario_error'] = str(exc)
        action_checks.append(check)
    save('corporate_actions', actions); save('corporate_action_checks', action_checks)
    components = []
    for row,check in zip(actions,action_checks):
        if check.get('share_distribution_ratio') not in (None,'0'):
            bonus = check.get('bonus_per_share'); reserve = check.get('capital_conversion_per_share')
            components.append({**row,'symbol':row['code'].replace('.',''),
                'action_type': 'MIXED_BONUS_AND_CAPITAL_CONVERSION' if bonus != '0' and reserve != '0'
                    else ('PROFIT_BONUS_SHARES' if bonus != '0' else 'CAPITAL_CONVERSION_SHARES'),
                'gross_cash_per_share':check.get('gross_cash_per_share'),
                'bonus_per_share':bonus,'capital_conversion_per_share':reserve,
                'cash_unit_check':check['cash_unit_check'],
                'actual_held_exposure_not_evaluated':True,'tax_lot_transform':'PENDING_IF_ACTUALLY_HELD'})
    save('noncash_action_components',components)
    atomic_json(normalized / 'corporate_action_conflicts.json', conflicts)
    identity = hashlib.sha256(json.dumps({'query_hashes': sorted(query_bytes), 'action_rows': actions,
        'coverage': action_coverage}, sort_keys=True).encode()).hexdigest()
    catalog_error = None
    try:
        ResearchActionCatalog(actions, action_coverage, source_identity=identity, query_bytes_by_sha256=query_bytes)
    except (ValueError, ArithmeticError) as exc:
        catalog_error = str(exc)
    states = Counter(r['query_status'] for r in query_coverage)
    report = {'source_class': 'ANONYMOUS_TCP_RETROSPECTIVE_VENDOR_NOT_EXCHANGE_CERTIFIED',
        'planned_queries': len(specs), 'query_status': dict(states), 'raw_daily_rows': len(raw_rows),
        'raw_daily_symbols': int(raw.symbol.nunique()) if len(raw) else 0,
        'raw_daily_first': str(raw.date.min()) if len(raw) else None, 'raw_daily_last': str(raw.date.max()) if len(raw) else None,
        'historical_ST_observations': int(raw.isST.eq(1).sum()) if len(raw) else 0,
        'historical_suspended_observations': int(raw.tradestatus.eq(0).sum()) if len(raw) else 0,
        'raw_price_missing_counts': {c: int(raw[c].isna().sum()) for c in ['open','close','preclose','volume','tradestatus','isST']} if len(raw) else {},
        'action_rows': len(actions), 'action_conflicts': len(conflicts),
        'action_scenario_ready': sum(c['scenario_action_ready'] for c in action_checks),
        'action_cash_only': sum(c.get('share_distribution_ratio') == '0' for c in action_checks),
        'action_missing_fields': dict(Counter(f for c in action_checks for f in c['missing_fields'])),
        'catalog_structural_reconciliation_ready': catalog_error is None, 'catalog_error': catalog_error,
        'catalog_source_identity': identity, 'all_source_strings_retained': True,
        'price_adjustment_flag': '3_RAW_UNADJUSTED', 'cross_source_validation_completed': False,
        'verified_historical_arrival': False, 'actual_dividend_tax_debit_or_rounding_known': False,
        'net_excess_return_acceptance': False,
        'remaining_requirements': ['all declared queries must finish successfully or remain explicit missing',
            'cross-source raw daily price and volume check', 'minute raw TWAP after pool exits if held',
            'bonus fractional allocation/tax-lot transformation where actual held action occurs',
            'official cash-tax rounding grouping; next-session deduction mechanics sourced from ChinaClear FAQ Q23',
            'live 80-hour update requires 2026-09-24,09-28,09-29,09-30 minutes before 2026-10-08'],
    }
    atomic_json(normalized / 'coverage_audit.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def audit_cross_source(directory):
    directory = Path(directory).resolve(); normalized = directory/'normalized'
    raw = pd.read_parquet(normalized/'raw_daily.parquet')
    minute_directory = ROOT/'gru_research/artifacts/minute_hourly_raw'
    audit = []
    cols = ['symbol','slot','hour_complete','raw_open','raw_high','raw_low','raw_close','volume']
    for day, group in raw.groupby('date'):
        path = minute_directory/(day.strftime('%Y-%m-%d')+'.parquet')
        if not path.exists(): continue
        minutes = pd.read_parquet(path, columns=cols).sort_values(['symbol','slot'])
        groups = minutes.groupby('symbol')
        daily = groups.agg(raw_open=('raw_open','first'),raw_high=('raw_high','max'),
            raw_low=('raw_low','min'),raw_close=('raw_close','last'),minute_volume=('volume','sum'),
            complete=('hour_complete','all'),slots=('slot','nunique'))
        daily = daily[daily.complete & daily.slots.eq(4)]
        daily.index = daily.index.map(lambda s:s[-2:].lower()+s[:6])
        joined = group.set_index('symbol').join(daily, how='inner')
        for symbol,row in joined.iterrows():
            differences = {c:float(row[c]-row['raw_'+c]) for c in ['open','high','low','close']}
            conflict = any(abs(v)>.01001 for v in differences.values())
            ratio = float(row.volume/row.minute_volume) if row.minute_volume > 0 and np.isfinite(row.volume) else None
            audit.append({'date':day,'symbol':symbol,'price_differences':json.dumps(differences,sort_keys=True),
                'price_conflict':conflict,'daily_volume':row.volume,'minute_volume':row.minute_volume,
                'volume_ratio_daily_to_minute':ratio,'volume_within_one_percent':ratio is not None and abs(ratio-1)<=.01,
                'source_sha256':row.source_sha256,'minute_source_path':str(path)})
    checks = pd.DataFrame(audit)
    checks.to_parquet(normalized/'raw_minute_cross_source.parquet',index=False)
    if len(checks):
        raw = raw.drop(columns=['cross_source_price_conflict','cross_source_check_status']).merge(
            checks[['date','symbol','price_conflict']],on=['date','symbol'],how='left',validate='one_to_one')
        raw['cross_source_check_status']=np.where(raw.price_conflict.isna(),'NO_COMPLETE_INDEPENDENT_MINUTE_DAY',
            np.where(raw.price_conflict,'OBSERVED_OHLC_CONFLICT_QUARANTINED','MINUTE_DAILY_OHLC_WITHIN_ONE_CENT'))
        raw['cross_source_price_conflict']=raw.price_conflict.eq(True)
        raw=raw.drop(columns='price_conflict')
        raw.to_parquet(normalized/'raw_daily.parquet',index=False)
    metadata=json.loads((ROOT/'gru_research/artifacts/minute_feature_metadata.json').read_text())
    times=np.load(ROOT/'gru_research/artifacts/minute_times.npy',mmap_mode='r')
    member=np.load(ROOT/'gru_research/artifacts/minute_members.npy',mmap_mode='r')
    symbols=metadata['union_symbols']; lookup={s[-2:].lower()+s[:6]:i for i,s in enumerate(symbols)}
    local_dates=pd.to_datetime(times+8*3600*10**9).normalize()
    tidx={d:i for i,d in enumerate(local_dates) if i%4==0}
    observed_member=observed_outside=0; expected=0
    for day,group in raw[raw.date.ge('2025-01-01')].groupby('date'):
        if day not in tidx:continue
        idx=tidx[day]; pool=member[idx];expected+=int(pool.sum())
        for symbol in group.symbol:
            u=lookup.get(symbol)
            if u is not None and pool[u]:observed_member+=1
            else:observed_outside+=1
    # Full expected denominator independent of which query symbols finished.
    full_expected=int(member[(local_dates>='2025-01-01') & (np.arange(len(times))%4==0)].sum())
    report={'raw_daily_rows':len(raw),'raw_daily_sha256':hashlib.sha256((normalized/'raw_daily.parquet').read_bytes()).hexdigest(),
        'cross_checks_sha256':hashlib.sha256((normalized/'raw_minute_cross_source.parquet').read_bytes()).hexdigest(),
        'cross_checked_complete_stock_days':len(checks),
        'observed_OHLC_conflicts':int(checks.price_conflict.sum()) if len(checks) else 0,
        'observed_volume_within_one_percent':int(checks.volume_within_one_percent.sum()) if len(checks) else 0,
        'volume_ratio_median':float(checks.volume_ratio_daily_to_minute.median()) if len(checks) else None,
        'true_historical_member_stock_days_2025_2026_expected':full_expected,
        'raw_observed_member_stock_days':observed_member,'raw_observed_outside_pool_stock_days':observed_outside,
        'historical_member_stock_days_without_raw_quote':full_expected-observed_member,
        'OHLC_conflicts_quarantined_for_execution':True,'raw_source_strings_never_repaired':True,
        'prices_unadjusted':True,'outside_pool_daily_OPEN_is_not_TWAP':True,
        'known_missing_live_minute_dates':['2026-09-24','2026-09-28','2026-09-29','2026-09-30']}
    atomic_json(normalized/'cross_source_audit.json',report)
    finalize_cross_status(directory)
    print(json.dumps(report,ensure_ascii=False,indent=2));return report


def finalize_cross_status(directory):
    n=Path(directory).resolve()/'normalized'
    cross=json.loads((n/'cross_source_audit.json').read_text())
    if hashlib.sha256((n/'raw_daily.parquet').read_bytes()).hexdigest()!=cross['raw_daily_sha256']:
        raise ValueError('cross audit is not bound to final raw bytes')
    if hashlib.sha256((n/'raw_minute_cross_source.parquet').read_bytes()).hexdigest()!=cross['cross_checks_sha256']:
        raise ValueError('cross audit check bytes changed')
    coverage=json.loads((n/'coverage_audit.json').read_text())
    coverage['cross_source_validation_completed']=True
    coverage['cross_source_audit_sha256']=hashlib.sha256((n/'cross_source_audit.json').read_bytes()).hexdigest()
    coverage['remaining_requirements']=[s for s in coverage['remaining_requirements']
        if s!='cross-source raw daily price and volume check'
        and not(s.startswith('all declared queries') and not coverage['query_status'].get('not_requested_yet',0)
            and not coverage['query_status'].get('provider_failed',0))]
    atomic_json(n/'coverage_audit.json',coverage)


def audit_ex_references(directory):
    """Observe ordinary formula agreement; never overwrite vendor preclose.

    Exchange rules allow issuer-specific/differential adjustments. Mismatches
    are pending primary evidence and do not get silently repaired by a formula.
    """
    n=Path(directory).resolve()/'normalized'
    raw=pd.read_parquet(n/'raw_daily.parquet').set_index(['date','symbol'])
    actions=pd.read_parquet(n/'corporate_actions.parquet')
    cal=pd.read_parquet(n/'calendar_source.parquet')
    days=list(pd.to_datetime(cal.loc[cal.is_trading_day.eq('1'),'calendar_date']))
    lookup={day:i for i,day in enumerate(days)}; rows=[]
    for (code,ex), group in actions.groupby(['code','dividOperateDate']):
        day=pd.Timestamp(ex);symbol=code.replace('.','')
        out={'symbol':symbol,'ex_date':day,'distinct_source_components':len(group),
            'raw_preclose_never_changed':True,'official_quote_reference_certified':False}
        i=lookup.get(day)
        if i is None or i==0 or (day,symbol) not in raw.index or (days[i-1],symbol) not in raw.index:
            out['status']='NO_RAW_PREVIOUS_AND_EX_DAY_PAIR';rows.append(out);continue
        cash=Decimal(0);shares=Decimal(0)
        try:
            for row in group.to_dict('records'):
                c,s=distribution_terms(row);cash+=c;shares+=s
            previous=Decimal(str(raw.loc[(days[i-1],symbol),'close']))
            observed=Decimal(str(raw.loc[(day,symbol),'preclose']))
            expected=((previous-cash)/(1+shares)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            difference=observed-expected
            out.update(previous_raw_close=str(previous),gross_cash_per_share=str(cash),
                ordinary_share_ratio=str(shares),vendor_ex_preclose=str(observed),
                ordinary_formula_reference=str(expected),difference=str(difference),
                status='MATCHES_ORDINARY_NO_RIGHTS_FORMULA' if not difference else
                    'DIFFERENTIAL_DIVIDEND_OR_SPECIAL_FORMULA_REQUIRES_PRIMARY')
        except (ValueError,ArithmeticError) as exc:
            out.update(status='TERMS_UNRESOLVED',reason=str(exc))
        rows.append(out)
    pd.DataFrame(rows).to_parquet(n/'ex_reference_checks.parquet',index=False)
    report={'source_class':'RETROSPECTIVE_VENDOR_REFERENCE_SCENARIO',
        'events':len(rows),'states':dict(Counter(r['status'] for r in rows)),
        'original_preclose_never_overwritten':True,'ordinary_formula_is_not_a_special_event_repair':True,
        'official_formula_source':'https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml#4.3.2',
        'held_exposures_need_primary_reference_if_mismatched':True}
    atomic_json(n/'ex_reference_audit.json',report);return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('directory', type=Path);p.add_argument('--cross-check',action='store_true'); a = p.parse_args()
    normalize(a.directory)
    if a.cross_check:
        audit_cross_source(a.directory)
        print(json.dumps(audit_ex_references(a.directory),ensure_ascii=False,indent=2))
