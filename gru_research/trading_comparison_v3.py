"""Independently sealed nine-model daily-TWAP comparison command line.

``verify`` reads only market/source receipts. ``prepare`` binds an actual later
freeze and existing CSV bytes. ``run`` invokes the unchanged V2 research replay.
V3 composes primary reference facts and one independent missing cash component.
No inference, label read, model fit, test-winner selection or prefix alignment.
"""
from __future__ import annotations
import argparse
import csv
from datetime import date, datetime, time, timezone
from decimal import Decimal
import hashlib
from itertools import groupby
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT/'gru_research'
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE/'.deps'))
sys.path.insert(0, str(ROOT/'research_v3/src'))
import pandas as pd
from trading_comparison_metrics_v3 import compare_run, reference_exposures, reference_resolution_audit, WINDOWS
from trading_provider import CN, cash_symbol, FrozenMinuteTWAPSource
from trading_inputs import bind_frozen_scores, historical_members
from trading_inputs_v3 import load_base_provider
from primary_action_supplement_v3 import PrimaryActionSupplementV3, RECEIPT_SHA as PRIMARY_RECEIPT_SHA, OVERLAY_SHA as PRIMARY_OVERLAY_SHA
from extended_costs import research_costs
from trading_replay import replay_research_pretax
from tax_reserve_replay import replay_research_tax_reserve

MODELS = ('fixed_label_1','fixed_label_2','fixed_label_3','fixed_label_4',
          'blo_selected','selected_full','pv','pv_factors','pv_fund')
SCORE_COLUMNS = ['symbol','signal_time','earliest_execution_time','score','percentile','model_version','data_quality_flag']
PROTOCOL = HERE/'artifacts/trading_comparison_protocol_v3.json'
SEAL = HERE/'artifacts/trading_comparison_source_seal_v3.json'
RECEIPT = HERE/'artifacts/trading_comparison_driver_receipt_v3.json'
V2_SHA = 'ab8bff4833e2be7292f0f540061e9cfb42099eba1c4d245483b531fb9d4b974a'
V2_READINESS_SHA = 'cfccb8ee10f088af6ad25a06812533f3c5d32d942fdc8f4bd412661d423911cf'
CALENDAR_SOURCE = 'HISTORICAL_IFIND_SESSIONS_WITH_SSE_OFFICIAL_2025_2026_CLOSED_CROSS_CHECK_AND_VERIFIED_SEP24_TAIL'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(8*1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def plain(value):
    if isinstance(value, dict): return {str(k):plain(v) for k,v in value.items()}
    if isinstance(value, (list,tuple,set,frozenset)): return [plain(v) for v in value]
    if isinstance(value, (date,datetime,Decimal,Path)): return str(value)
    if isinstance(value, float) and not math.isfinite(value): return None
    if hasattr(value,'item'): return plain(value.item())
    return value


def write_new(path, payload):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(plain(payload), stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def verify_files(mapping):
    for filename, expected in mapping.items():
        path = Path(filename)
        actual = sha(path) if path.is_file() else None
        if actual != expected:
            raise ValueError('sealed bytes changed or missing: '+filename)


class SealedMinuteSource(FrozenMinuteTWAPSource):
    """Observe actual required windows without changing the frozen quote logic."""
    def __init__(self, mapping):
        super().__init__();self.sealed_files=mapping;self.accesses=[]

    def window(self, signal_time, symbols):
        symbols=tuple(symbols)
        window=super().window(signal_time,symbols)
        source=str(Path(window.source).resolve())
        if source not in self.sealed_files:
            raise ValueError('actual minute source was not sealed before score evaluation')
        verify_files({source:self.sealed_files[source]})
        self.accesses.append(dict(signal_time_utc_ns=window.signal_time,
            execution_day=str(pd.Timestamp(window.execution_start,tz='UTC').tz_convert(CN).date()),
            window_start_utc_ns=window.execution_start,window_end_utc_ns=window.execution_end,
            source_path=source,source_sha256=self.sealed_files[source],
            requested_quotes={s:dict(complete=q.complete,reason=q.reason,
                positive_observed_volume=q.observed_positive_volume) for s,q in window.quotes.items()}))
        return window


def requested_minute_coverage(accesses, members):
    unique={}
    for access in accesses:
        for symbol,quote in access['requested_quotes'].items():
            unique[(access['execution_day'],symbol)]=quote
    monthly={}
    for (d,s),quote in unique.items():
        record=monthly.setdefault(d[:7],dict(actual_requested_stock_days=0,complete_positive_volume=0,
            no_trade_or_unusable_window=0,outside_current_member_stock_days=0))
        record['actual_requested_stock_days']+=1
        record['complete_positive_volume']+=int(quote['complete'])
        record['no_trade_or_unusable_window']+=int(not quote['complete'])
        record['outside_current_member_stock_days']+=int(s not in members[date.fromisoformat(d)])
    return monthly


def verify_source_seal(protocol_path=PROTOCOL, seal_path=SEAL, receipt_path=RECEIPT):
    protocol, seal, receipt = map(read_json, (protocol_path,seal_path,receipt_path))
    if (receipt['comparison_protocol_sha256'] != sha(protocol_path)
            or receipt['source_seal_sha256'] != sha(seal_path)
            or protocol['bridge_protocol_sha256'] != V2_SHA
            or protocol['bridge_readiness_sha256'] != V2_READINESS_SHA
            or protocol['models'] != list(MODELS)
            or protocol['primary_action_receipt_sha256'] != PRIMARY_RECEIPT_SHA
            or protocol['primary_action_overlay_sha256'] != PRIMARY_OVERLAY_SHA):
        raise ValueError('fixed comparison/V2 identity changed')
    verify_files(receipt['driver_source_hashes'])
    verify_files(seal['immutable_files'])
    verify_files(seal['minute_raw_day_files'])
    PrimaryActionSupplementV3()
    return protocol, seal, receipt


def checkpoint_path(run, identity):
    candidate = Path(identity)
    if candidate.is_file(): return candidate
    for group in ('fixed','blo_final','ablations'):
        marker = '/'+group+'/'
        if marker in identity:
            suffix = Path(identity.split(marker,1)[1])
            if '..' in suffix.parts: raise ValueError('unsafe checkpoint relocation')
            return Path(run)/group/suffix
    raise ValueError('missing original frozen checkpoint identity: '+identity)


def verify_freeze(run, expected_sha=None):
    run = Path(run).resolve(); path = run/'freeze.json'
    if expected_sha is not None and sha(path)!=expected_sha:
        raise ValueError('actual model freeze bytes changed')
    frozen = read_json(path)
    identity = hashlib.sha256(json.dumps({'data':frozen['dataset_fingerprint'],
        'config':frozen['config'],'selection':frozen['selection'],
        'hashes':frozen['checkpoint_hashes']},sort_keys=True).encode()).hexdigest()[:20]
    if (identity!=frozen['freeze_id'] or not frozen['no_test_driven_tuning']
            or set(frozen['evaluation_models'])!=set(MODELS)
            or frozen['config']['seeds']!=[11,22,33,44,55]
            or frozen['config']!=read_json(HERE/'config.json')
            or frozen['selection']['validation_year']!=2024):
        raise ValueError('nine frozen models and 2024 selection evidence required')
    selection = frozen['selection']
    alternatives = selection['alternatives']
    if set(alternatives)!= {'fixed_1','fixed_2','fixed_3','fixed_4','blo'}:
        raise ValueError('fixed validation alternatives required')
    if any(not math.isfinite(float(v['mean_ic'])) for v in alternatives.values()):
        raise ValueError('finite 2024 mean IC evidence required')
    best = max(alternatives,key=lambda k: alternatives[k]['mean_ic'])
    gain = alternatives[best]['mean_ic']-alternatives['fixed_4']['mean_ic']
    method = 'fixed_4' if gain<0.001 else best
    label = selection['blo_label'] if method=='blo' else int(method[-1])
    if (selection['selected_method']!=method or selection['selected_label']!=label
            or selection['minimum_gain']!=0.001
            or not 1<=int(selection['blo_label'])<=4):
        raise ValueError('selected model must follow original 2024 rule')
    expected_selected = frozen['evaluation_models']['blo_selected' if method=='blo' else 'fixed_label_'+str(label)]
    if frozen['evaluation_models']['selected_full']!=expected_selected:
        raise ValueError('selected_full is not the frozen validation-selected model')
    hashes = {}
    used = set()
    for model in MODELS:
        paths = frozen['evaluation_models'][model]
        if len(paths)!=5 or len(set(paths))!=5: raise ValueError('five actual frozen seed checkpoints required')
        for seed, original in zip(frozen['config']['seeds'],paths):
            if '/seed_'+str(seed)+'/best.pt' not in original:
                raise ValueError('checkpoint seed identity mismatch')
            local = checkpoint_path(run, original)
            expected = frozen['checkpoint_hashes'][original]
            if sha(local)!=expected: raise ValueError('actual frozen checkpoint bytes changed')
            hashes[str(local.resolve())] = expected; used.add(original)
    if used!=set(frozen['checkpoint_hashes']): raise ValueError('extra/unbound checkpoint hashes')
    return frozen, hashes


def prepare_manifest(run, output=None):
    protocol, seal, _ = verify_source_seal()
    run = Path(run).resolve()
    frozen, checkpoints = verify_freeze(run)
    files, exports = {str(run/'freeze.json'):sha(run/'freeze.json'), **checkpoints}, {}
    # Corrected files may extend explicit exclusions only when exact raw lineage
    # proves every original score/percentile/version preserved, without inference.
    audit_path = run/'corrected_score_exports/manifest.json'
    audit = read_json(audit_path) if audit_path.is_file() else None
    if audit:
        if (audit['freeze_id']!=frozen['freeze_id'] or audit['freeze_sha256']!=sha(run/'freeze.json')
                or audit['export_code_sha256']!=sha(HERE/'score_export_audit.py')
                or set(audit['reports'])!={s+'/'+m for s in ('test_2025','recent_2026') for m in MODELS}):
            raise ValueError('corrected export audit differs from immutable raw freeze')
        files[str(audit_path)] = sha(audit_path)
    for model in MODELS:
        exports[model] = []
        for split in ('test_2025','recent_2026'):
            raw = run/'frozen_evaluation'/split/(model+'_scores.csv')
            raw_sha = sha(raw); effective = raw; unresolved = None
            if audit:
                report = audit['reports'][split+'/'+model]
                if (report['raw_sha256']!=raw_sha or not report['raw_preserved']
                        or report['scores_percentiles_versions_changed'] is not False
                        or report['inference_performed'] is not False or report['labels_or_IC_metrics_read'] is not False):
                    raise ValueError('original raw score preservation is unproved')
                effective = run/'corrected_score_exports'/split/(model+'_scores.csv')
                if sha(effective)!=report['corrected_sha256']:
                    raise ValueError('corrected score bytes do not match raw-preservation receipt')
                unresolved = report['unresolved_original_score_sections']
            files[str(raw)] = raw_sha; files[str(effective)] = sha(effective)
            exports[model].append(dict(split=split,raw_path=str(raw),raw_sha256=raw_sha,
                csv_path=str(effective),csv_sha256=sha(effective),
                unresolved_original_score_sections=unresolved))
    manifest = dict(schema_version=1, status='ACTUAL_FROZEN_INPUT_BYTES_BOUND_NOT_EVALUATED',
        prepared_at_utc=datetime.now(timezone.utc).isoformat(),run_directory=str(run),
        comparison_protocol_sha256=sha(PROTOCOL),source_seal_sha256=sha(SEAL),
        driver_receipt_sha256=sha(RECEIPT),freeze_sha256=sha(run/'freeze.json'),
        freeze_id=frozen['freeze_id'],models=list(MODELS),
        model_versions={m:frozen['freeze_id']+':'+m for m in MODELS},
        frozen_files=files,score_exports=exports,actual_score_rows_read=False,
        test_winner_selection=False)
    if output is not None: write_new(output,manifest)
    return manifest


def load_daily_scores(entries, model_version, calendar, member_by_day):
    records, sections, slot_counts, missing_sections = [], {}, {}, []
    all_days = [d for d in calendar if date(2025,1,2)<=d<=date(2026,9,23)]
    for entry in entries:
        path = Path(entry['csv_path'])
        if sha(path)!=entry['csv_sha256']: raise ValueError('CSV bytes changed before parsing')
        split_year = 2025 if entry['split']=='test_2025' else 2026
        with path.open(newline='') as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames!=SCORE_COLUMNS: raise ValueError('original seven-column score schema required')
            last_time = -1
            for stamp, group in groupby(reader, key=lambda r:int(r['signal_time'])):
                local = pd.Timestamp(stamp,tz='UTC').tz_convert(CN)
                d, clock = local.date(),local.time()
                if (stamp<=last_time or d.year!=split_year or d not in all_days
                        or clock not in (time(10,30),time(11,30),time(14),time(15))
                        or local.nanosecond):
                    raise ValueError('nonchronological/nonstandard/out-of-split score timestamp')
                last_time = stamp
                i = calendar.index(d)
                start_day,start_clock = (calendar[i+1],time(9,31)) if clock==time(15) else (d,
                    {time(10,30):time(10,31),time(11,30):time(13,1),time(14):time(14,1)}[clock])
                earliest = pd.Timestamp(datetime.combine(start_day,start_clock,CN)).value
                rows, scored, flags = {}, 0, {}
                for row in group:
                    symbol = cash_symbol(row['symbol'])
                    if symbol in rows or row['model_version']!=model_version or int(row['earliest_execution_time'])!=earliest:
                        raise ValueError('duplicate/version/execution time mismatch in frozen score')
                    text = row['score'].strip()
                    # IEEE NaN remains missing. Infinite values are rejected;
                    # feature_missing/label_unmatured never remove finite scores.
                    value = float(text) if text else float('nan')
                    if math.isinf(value): raise ValueError('infinite model score')
                    pct_text = row['percentile'].strip()
                    pct = float(pct_text) if pct_text else float('nan')
                    if math.isinf(pct) or (math.isfinite(pct) and not 0<=pct<=1):
                        raise ValueError('invalid score percentile metadata')
                    if math.isfinite(value)!=math.isfinite(pct):
                        raise ValueError('score and percentile missingness differs')
                    scored += int(math.isfinite(value))
                    rows[symbol] = value
                    flags[row['data_quality_flag']] = flags.get(row['data_quality_flag'],0)+1
                    if clock==time(15): records.append(dict(symbol=symbol,signal_time=stamp,
                        score=value,model_version=model_version))
                if set(rows)!=set(member_by_day[d]):
                    raise ValueError('CSV section must retain actual historical500member keys')
                sections[str(stamp)] = dict(date=str(d),slot=clock.isoformat(),members=len(rows),
                    finite_scores=scored,missing_scores=len(rows)-scored,quality_flag_counts=flags)
                slot_counts[clock.isoformat()] = slot_counts.get(clock.isoformat(),0)+1
        if sha(path)!=entry['csv_sha256']: raise ValueError('score CSV changed during parsing')
    for d in all_days:
        for clock in (time(10,30),time(11,30),time(14),time(15)):
            stamp = str(pd.Timestamp(datetime.combine(d,clock,CN)).value)
            if stamp not in sections: missing_sections.append(stamp)
    unresolved = [v for e in entries for v in (e.get('unresolved_original_score_sections') or [])]
    complete = not missing_sections and not unresolved
    unresolved_keys={str(v) for v in unresolved}
    trading_coverage={}
    for window,(_,end) in WINDOWS.items():
        # Every earlier daily decision remains causal state for2026; no reset.
        required=[str(pd.Timestamp(datetime.combine(d,time(15),CN)).value) for d in all_days if d<date.fromisoformat(end)]
        trading_coverage[window]=all(s in sections and s not in unresolved_keys for s in required)
    frame = pd.DataFrame(records,columns=['symbol','signal_time','score','model_version'])
    if frame.empty: raise ValueError('no actual daily-close score section')
    return frame, dict(source_complete=complete,four_hour_source_complete=complete,
        continuous_account_trading_score_coverage_by_window=trading_coverage,
        missing_entire_hour_sections=missing_sections,
        unresolved_original_score_sections=unresolved,four_hour_input_section_counts=slot_counts,
        sections=sections,trading_signal_slot='15:00:00',blank_or_NaN_never_zero=True,
        finite_scores_not_filtered_by_feature_or_label_quality_flags=True,
        prediction_IC_or_labels_read=False)


def benchmark_prices(path):
    prices = {}
    with Path(path).open(newline='') as stream:
        for row in csv.DictReader(stream):
            d = date.fromisoformat(row['date']); close = Decimal(row['close'])
            if d in prices or close<=0 or row['index_code']!='000905' or 'PRICE' not in row['benchmark_variant']:
                raise ValueError('actual official CSI500 PRICE quote schema required')
            prices[d] = close
    if len(prices)!=421 or min(prices)!=date(2024,12,31) or max(prices)!=date(2026,9,23):
        raise ValueError('sealed official421rows required')
    return prices


def slippage_from_sealed_windows(result, source):
    costs = {}
    for o in result['outcomes']:
        if not o['filled']: continue
        d = o['date'] if type(o['date']) is date else date.fromisoformat(str(o['date'])[:10])
        previous = source.calendar[source.calendar.index(d)-1]
        quote = source.window(datetime.combine(previous,time(15),CN),[o['symbol']]).quotes[o['symbol']]
        if not quote.complete or quote.raw_price is None:
            raise ValueError('a reported fill lacks its sealed real raw TWAP')
        signed = (Decimal(str(o['price']))-quote.raw_price)*(1 if o['side']=='BUY' else -1)*o['filled']
        if signed<0: raise ValueError('unexpected favourable slippage under fixed adverse scenario')
        costs[o['order_id']] = signed
    return costs


def pair_reports(results, protocol, frozen):
    pairs = dict(protocol['contrasts'])
    best = max(range(1,5),key=lambda k:frozen['selection']['alternatives']['fixed_'+str(k)]['mean_ic'])
    reports = {}
    for identity, spec in pairs.items():
        a = spec['model_a']; b = spec.get('model_b','fixed_label_'+str(best))
        rows = []
        for scenario in ('primary','stress'):
            for basis in ('pretax','tax_upper_reserve'):
                for window in WINDOWS:
                    x=results[a][scenario][basis]['windows'][window]
                    y=results[b][scenario][basis]['windows'][window]
                    full = x['full_mark_and_execution_coverage'] and y['full_mark_and_execution_coverage']
                    eligible = x['economic_comparison_eligible'] and y['economic_comparison_eligible']
                    delta = None
                    if full:
                        delta={k:Decimal(str(x['scenario_values'][k]))-Decimal(str(y['scenario_values'][k])) for k in (
                            'account_scenario_return','arithmetic_excess_percentage_points','wealth_relative_excess_return')}
                    net = None
                    if eligible:
                        p,q=x['same_path_net_tax_bounds'],y['same_path_net_tax_bounds']
                        net=dict(arithmetic_net_excess_difference_percentage_points_lower=
                            p['arithmetic_net_excess_percentage_points_lower']-q['arithmetic_net_excess_percentage_points_upper'],
                            arithmetic_net_excess_difference_percentage_points_upper=
                            p['arithmetic_net_excess_percentage_points_upper']-q['arithmetic_net_excess_percentage_points_lower'])
                    rows.append(dict(cost_scenario=scenario,account_basis=basis,window=window,
                        both_full_fixed_window=full,scenario_difference=delta,
                        same_path_net_difference_bounds=net,risk_and_net_bounds_eligible=eligible,
                        point_net_improvement_accepted=False,partial_prefix_or_subset_winner=False))
        reports[identity]=dict(model_a=a,model_b=b,resolver_used_2024_only=True,rows=rows)
    return reports


def run_manifest(manifest_path, output):
    protocol, seal, receipt = verify_source_seal()
    manifest = read_json(manifest_path)
    if (manifest['comparison_protocol_sha256']!=sha(PROTOCOL) or manifest['source_seal_sha256']!=sha(SEAL)
            or manifest['driver_receipt_sha256']!=sha(RECEIPT) or manifest['models']!=list(MODELS)):
        raise ValueError('manifest must bind this fixed comparison driver')
    verify_files(manifest['frozen_files'])
    frozen,_=verify_freeze(manifest['run_directory'],manifest['freeze_sha256'])
    if manifest['freeze_id']!=frozen['freeze_id']: raise ValueError('manifest freeze identity mismatch')
    derived=prepare_manifest(manifest['run_directory'])
    for key in ('freeze_sha256','freeze_id','models','model_versions','frozen_files','score_exports'):
        if manifest[key]!=derived[key]: raise ValueError('manifest does not preserve actual freeze/CSV bindings: '+key)
    output=Path(output).resolve(); output.mkdir(parents=True,exist_ok=False)
    write_new(output/'input_manifest.json',manifest)
    write_new(output/'comparison_protocol.json',protocol)
    minute = FrozenMinuteTWAPSource()
    calendar = [d for d in minute.calendar if d>=date(2024,12,1)]
    base=load_base_provider(protocol['trading_source_directory'],official_calendar=calendar,
        calendar_source=CALENDAR_SOURCE,protocol_path=HERE/'artifacts/trading_research_protocol_v2.json')
    days = [d for d in calendar if date(2025,1,2)<=d<=date(2026,9,23)]
    prices=benchmark_prices(protocol['official_benchmark_csv'])
    if days!=[d for d in prices if date(2025,1,2)<=d<=date(2026,9,23)]:
        raise ValueError('portfolio and benchmark valuation calendars differ')
    members=historical_members(calendar)
    member_map={d.date():frozenset(g.symbol) for d,g in members.groupby('date')}
    checks=pd.read_parquet(Path(protocol['trading_source_directory'])/'normalized/ex_reference_checks.parquet').to_dict('records')
    summaries={}; records={}
    for model in MODELS:
        verify_files(manifest['frozen_files'])
        frame,coverage=load_daily_scores(manifest['score_exports'][model],manifest['model_versions'][model],calendar,member_map)
        write_new(output/(model+'_score_coverage.json'),coverage)
        summaries[model]={}; records[model]={}
        for scenario in ('primary','stress'):
            summaries[model][scenario]={}; records[model][scenario]={}
            for basis, replay in (('pretax',replay_research_pretax),('tax_upper_reserve',replay_research_tax_reserve)):
                verify_files(seal['minute_raw_day_files'])
                verify_files(receipt['driver_source_hashes'])
                provider=bind_frozen_scores(base,frame,model_version=manifest['model_versions'][model],
                    score_source='SEALED_RAW_PRESERVED_CSV_MANIFEST_SHA256:'+sha(manifest_path))
                guarded_minute=SealedMinuteSource(seal['minute_raw_day_files'])
                provider.minute=guarded_minute
                run_id=frozen['freeze_id']+':'+model+':'+scenario+':'+basis
                print('BEGIN '+run_id,flush=True)
                result=replay(provider,model=manifest['model_versions'][model],valuation_days=days,
                    costs=research_costs(scenario=='stress'),capital=Decimal(protocol['initial_capital']),run_id=run_id)
                slippage=slippage_from_sealed_windows(result,guarded_minute)
                result['sealed_minute_source_accesses']=guarded_minute.accesses
                result['actual_required_minute_monthly_coverage']=requested_minute_coverage(guarded_minute.accesses,member_map)
                # Save the journal first, retaining a genuine attempted run even
                # if a subsequent byte guard rejects its comparison eligibility.
                destination=output/model/scenario/basis
                write_new(destination/'replay.json',result)
                try:
                    verify_files(seal['minute_raw_day_files'])
                    verify_files(seal['immutable_files'])
                    verify_files(manifest['frozen_files'])
                    verify_files(receipt['driver_source_hashes'])
                except ValueError as exc:
                    write_new(destination/'source_guard_rejection.json',dict(status='REJECTED_CHANGED_SOURCE_BYTES',
                        reason=str(exc),journal_retained=True,comparison_values_accepted=False))
                    raise
                exposed=reference_exposures(result,checks,catalog=base.catalog)
                summary=compare_run(result,prices,account_basis=basis,special_reference_exposures=exposed,
                    score_coverage_complete=coverage.get('continuous_account_trading_score_coverage_by_window',coverage['source_complete']),
                    slippage_by_order=slippage)
                summary['source_and_minute_hashes_verified_before_and_after_run']=True
                summary['ex_reference_exposures']=exposed
                summary['primary_reference_resolution_audit']=reference_resolution_audit(result,checks,catalog=base.catalog)
                write_new(destination/'summary.json',summary)
                summaries[model][scenario][basis]=summary
                records[model][scenario][basis]=dict(replay_path=str(destination/'replay.json'),
                    replay_sha256=sha(destination/'replay.json'),summary_sha256=sha(destination/'summary.json'),status=result['status'])
                print('END '+run_id+' '+result['status'],flush=True)
    verify_files(seal['minute_raw_day_files']);verify_files(seal['immutable_files'])
    verify_files(manifest['frozen_files']);verify_files(receipt['driver_source_hashes'])
    comparison=dict(status='ALL_NINE_FIXED_MODELS_REPLAYED_RESEARCH_ONLY',freeze_id=frozen['freeze_id'],
        model_versions=manifest['model_versions'],models=list(MODELS),
        fixed_2024_selected_model='selected_full',test_winner_selected=False,
        comparison_protocol_sha256=sha(PROTOCOL),source_seal_sha256=sha(SEAL),
        driver_receipt_sha256=sha(RECEIPT),input_manifest_sha256=sha(manifest_path),
        results=summaries,contrasts=pair_reports(summaries,protocol,frozen),artifacts=records,
        economic_target_accepted=False,all_PLAN_acceptance_complete=False,
        prospective60session_acceptance_complete=False)
    write_new(output/'comparison.json',comparison)
    return comparison


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    subs.add_parser('verify')
    prepare=subs.add_parser('prepare');prepare.add_argument('--run',type=Path,required=True);prepare.add_argument('--output',type=Path,required=True)
    execute=subs.add_parser('run');execute.add_argument('--manifest',type=Path,required=True);execute.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='verify':
        _,seal,_=verify_source_seal()
        print(json.dumps(dict(status='SEALED_MARKET_AND_DRIVER_BYTES_VERIFIED',minute_days=len(seal['minute_raw_day_files']),actual_scores_read=False)))
    elif args.command=='prepare':
        manifest=prepare_manifest(args.run,args.output)
        print(json.dumps(dict(status=manifest['status'],manifest=str(args.output),models=len(MODELS))))
    else:
        result=run_manifest(args.manifest,args.output)
        print(json.dumps(dict(status=result['status'],output=str(args.output),economic_target_accepted=False)))


if __name__=='__main__':main()
