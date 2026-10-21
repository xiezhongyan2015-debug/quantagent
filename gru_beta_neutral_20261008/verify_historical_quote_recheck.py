"""Independently reproduce selected source strings and public coarse diagnostics.

No network requests, repeated year-file scans, or source precision inventions.
Keep exact cash/share differences and test the cumulative reporting hypothesis
as a diagnostic rather than recovering missing shares from it.
"""
import csv
from collections import Counter
from datetime import datetime
from decimal import Decimal as D, ROUND_HALF_UP
from . import bounded_tail_pipeline as p


def main():
    lock = p.locked('verify_historical_quote_recheck')
    directory = p.OUT / 'historical_quote_candidate_recheck'
    audit_path = directory / 'receipt.json'
    audit = p.json.loads(audit_path.read_text())
    assert audit['status'] == 'complete'
    code = p.ROOT / 'gru_beta_neutral_20261008/recheck_historical_quote_candidates.py'
    assert p.sha(code) == audit['active_source_code_sha256']
    assert p.sha(directory / 'initial_recheck_without_alias_routing.py.txt') == audit['source_code_sha256']
    scope_path = p.BASE / 'public_source_probes/additional_public_archives_20261009/wind17/all_minimum_gap_consolidation/every_actual_missing_hour_diagnostics.csv'
    assert p.sha(scope_path) == audit['immutable_scope_sha256']
    with scope_path.open() as stream:
        scope = [r for r in csv.DictReader(stream) if r['daily_and_positive_hour_pass'] == 'True']
    expected = {(r['symbol'], r['date'], int(r['slot'])) for r in scope}
    assert len(expected) == 1620
    grid = list(range(571, 691)) + list(range(781, 901))
    fields = {'open': '开盘价', 'high': '最高价', 'low': '最低价', 'close': '收盘价'}
    leaves = {(r['symbol'], r['date']): r for f in audit['native_files'] for r in f['days']}
    assert len(leaves) == 405
    for record in audit['native_files']:
        assert p.identity(record['path']) == record['source_identity']
    daily_cache, results, observed, strict_hours, exact_hours = {}, [], set(), set(), set()
    for result in audit['results']:
        symbol, date = result['symbol'], result['date']
        key = symbol, date
        assert key not in observed
        observed.add(key)
        leaf = leaves[key]
        path = p.Path(leaf['selected_path'])
        assert p.sha(path) == leaf['selected_sha256'] == result['native_selected_sha256']
        selected = p.json.loads(path.read_text())
        rows = selected['raw_selected_rows']
        bars, auction = {}, None
        for raw in rows:
            time = datetime.fromisoformat(raw['k线结束时间'])
            assert time.strftime('%Y-%m-%d') == date and time.second == 0
            minute = time.hour * 60 + time.minute
            bar = {f: D(raw[k]) for f, k in fields.items()}
            bar.update(volume=D(raw['成交量']) * 100, amount=D(raw['成交额']))
            assert all(v.is_finite() for v in bar.values())
            assert all(bar[f] > 0 and bar[f] * 100 == (bar[f] * 100).to_integral_value() for f in fields)
            assert bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']
            assert bar['volume'] >= 0 and bar['volume'] == bar['volume'].to_integral_value() and bar['amount'] >= 0
            if minute in grid:
                assert minute not in bars
                bars[minute] = bar
            elif minute == 570 and bar['volume'] > 0:
                assert auction is None
                auction = bar
        assert sorted(bars) == grid
        if auction:
            first = bars[571]
            first['open'] = auction['open']
            first['high'] = max(first['high'], auction['high'])
            first['low'] = min(first['low'], auction['low'])
            first['volume'] += auction['volume']
            first['amount'] += auction['amount']
        native = [bars[m] for m in grid]
        assert [{f:str(v) for f,v in b.items()} for b in native] == selected['normalized_bars']
        if symbol not in daily_cache:
            daily_path = p.BASE / 'public_source_probes/baostock/union_daily_factors/normalized' / (symbol + '.json')
            primary = p.json.loads(daily_path.read_text())
            daily_cache[symbol] = {r[0]: dict(zip(primary['fields'], r)) for r in primary['rows']}
        day = daily_cache[symbol][date]
        assert day['tradestatus'] == '1' and D(day['volume']) > 0
        assert native[0]['open'] == D(day['open']) and native[-1]['close'] == D(day['close'])
        assert max(b['high'] for b in native) <= D(day['high']) and min(b['low'] for b in native) >= D(day['low'])
        volume_delta = sum(b['volume'] for b in native) - D(day['volume'])
        assert abs(volume_delta) <= 100
        assert all(sum(b['volume'] for b in native[s*60:(s+1)*60]) > 0 for s in result['slots'])
        record = dict(symbol=symbol, date=date, slots=result['slots'], primary_daily_volume_difference=str(volume_delta),
                      primary_daily_amount_difference=str(sum(b['amount'] for b in native) - D(day['amount'])),
                      native_reported_lots_all_integral=all(D(r['成交量']) == D(r['成交量']).to_integral_value() for r in rows),
                      native_reported_money_all_integral=all(D(r['成交额']) == D(r['成交额']).to_integral_value() for r in rows),
                      complete_coarse_reference=result['reference_rows'] == 48)
        for slot in result['slots']:
            assert (symbol, date, slot) in expected
        if result['reference_rows'] == 48:
            wire_path = p.Path(result['reference_path'])
            assert p.sha(wire_path) == result['reference_sha256']
            q = p.json.loads(wire_path.with_suffix('.json').read_text())
            code = 'sz.300114' if symbol == '302132.SZ' and date < '2025-02-17' else symbol[-2:].lower()+'.'+symbol[:6]
            assert q['code'] == code and q['frequency'] == '5' and q['adjustflag'] == '3' and q['start'] == q['end'] == date
            payload = q['data']
            payload = payload['record'] if isinstance(payload, dict) else payload
            reference = [dict(zip(q['fields'], r)) for r in payload]
            assert len(reference) == 48
            gates, exacts, native_cum, reference_cum, rounded = [], [], D(0), D(0), []
            money_deltas, qty_deltas = [], []
            for i, ref in enumerate(reference):
                minute = int(ref['time'][8:10])*60+int(ref['time'][10:12])
                assert minute == grid[i*5+4]
                block = native[i*5:(i+1)*5]
                agg = dict(open=block[0]['open'], close=block[-1]['close'], high=max(b['high'] for b in block),
                           low=min(b['low'] for b in block), volume=sum(b['volume'] for b in block), amount=sum(b['amount'] for b in block))
                deltas = {f:agg[f]-D(ref[f]) for f in agg}
                reported = result['comparisons'][i]
                assert {f:str(v) for f,v in deltas.items()} == reported['differences']
                gate = all(deltas[f] == 0 for f in fields) and abs(deltas['volume']) <= 100
                gates.append(gate)
                exacts.append(all(v == 0 for v in deltas.values()))
                assert gate == reported['strict_gate_pass']
                native_cum += agg['volume']
                reference_cum += D(ref['volume'])
                rounded.append(native_cum == (reference_cum / 100).quantize(D(1), rounding=ROUND_HALF_UP) * 100)
                qty_deltas.append(deltas['volume'])
                money_deltas.append(deltas['amount'])
            strict = [s for s in result['slots'] if all(gates[s*12:(s+1)*12])]
            exact = [s for s in result['slots'] if all(exacts[s*12:(s+1)*12])]
            assert strict == result['strict_coarse_pass_slots'] and exact == result['all_fields_exact_slots']
            strict_hours.update((symbol,date,s) for s in strict)
            exact_hours.update((symbol,date,s) for s in exact)
            record.update(strict_coarse_slots=strict, all_fields_exact_slots=exact,
                          cumulative_volume_matches_nearest_100_share_hypothesis_at_5m_ends=sum(rounded),
                          maximum_absolute_coarse_volume_difference=str(max(map(abs,qty_deltas))),
                          maximum_absolute_coarse_money_difference=str(max(map(abs,money_deltas))))
        results.append(record)
    assert observed == set(leaves)
    assert sum(len(r['slots']) for r in results) == len(expected)
    assert len(strict_hours) == audit['strict_coarse_diagnostic_hours'] and len(exact_hours) == audit['all_fields_exact_diagnostic_hours']
    proof = dict(checked_utc=p.utc(), passed=True, source_code_sha256=p.sha(__file__), audited_receipt_sha256=p.sha(audit_path),
                 selected_native_stockdays_checked=len(results), existing_primary_daily_and_native_grid_gate_reproduced=True,
                 complete_coarse_reference_stockdays=sum(r['complete_coarse_reference'] for r in results),
                 strict_coarse_diagnostic_hours=len(strict_hours), all_fields_exact_diagnostic_hours=len(exact_hours),
                 native_reporting_quantization_is_a_diagnostic_not_true_share_recovery=True,
                 source_year_files_not_rescanned_or_rehashed=True,
                 source_file_identities_still_unchanged=True, candidate_hours_admitted=0,
                 results=results, peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(directory / 'independent_verification.json', proof)
    lock.close()


if __name__ == '__main__':
    main()
