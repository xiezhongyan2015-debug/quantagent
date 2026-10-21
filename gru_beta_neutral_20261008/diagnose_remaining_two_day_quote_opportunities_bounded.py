"""Select remaining two-day source opportunities without admitting any hour.

Only small accepted minute leaves and public response files are read. Native
archives are not reopened. A real empty-minute quote remains a requirement for
later staging; this diagnostic never manufactures a minute or an arrival clock.
"""
from collections import Counter, defaultdict
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stream_tail_minutes import GRID

RAW = p.BASE/'backfilled_panel_candidate_native_expanded_v16_raw'
DEST = p.OUT/'remaining_two_day_quote_opportunities_v16'
DAYS = ['2026-09-22', '2026-09-23']


def main():
    lock = p.locked('diagnose_remaining_two_day_quote_opportunities')
    DEST.mkdir(exist_ok=True);output = DEST/'receipt.json';assert not output.exists()
    audit_path = RAW/'raw_full_history_audit.json';audit = p.json.loads(audit_path.read_text())
    assert audit['passed'] and audit['stocks_checked'] == 1178
    assert audit['audited_raw_meta_sha256'] == p.sha(RAW/'dataset/meta.json')
    mask_identity = next(a['verified_identity'] for a in audit['arrays'] if a['name'] == 'hour_complete.npy')
    assert p.identity(RAW/'dataset/hour_complete.npy') == mask_identity
    mask = p.np.load(RAW/'dataset/hour_complete.npy', mmap_mode='r')
    calendar = p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    offsets = {d:calendar['dates'].index(d)*4 for d in DAYS}
    indices = {s:i for i,s in enumerate(calendar['union_symbols'])}
    queries = {};response_pins = [];native_pins = {}
    for name in ['sina_tail_minute_expanded', 'sina_tail_nonmonotonic_references', 'sina_remaining376_tail_references']:
        path = p.OUT/name/'receipt.json';proof_path = path.parent/'independent_verification.json'
        receipt = p.json.loads(path.read_text());proof = p.json.loads(proof_path.read_text())
        assert proof['passed'] and proof['audited_receipt_sha256'] == p.sha(path)
        response_pins.append(dict(receipt_path=str(path), receipt_sha256=p.sha(path), proof_path=str(proof_path), proof_sha256=p.sha(proof_path)))
        for r in receipt['records']:
            assert r['symbol'] not in queries;assert r['status'] == 'compared_without_admission'
            queries[r['symbol']] = r
        for pin in receipt.get('native_source_leaf_pins', []):native_pins[pin['path']] = pin
    assert len(queries) == 624
    # The completed remaining376 pass covered every relevant prefix/day leaf.
    progress_path = p.OUT/'minute_observation_progress.json';progress = p.json.loads(progress_path.read_text())
    minute_leaves = {(g['date'], g['prefix']):next(v for v in g['minute_files'] if v['model'] == 'floor_left') for g in progress['groups'] if g['date'] in DAYS}
    for leaf in minute_leaves.values():
        assert leaf['path'] in native_pins and leaf['sha256'] == native_pins[leaf['path']]['sha256']
    coarse_path = p.OUT/'tencent_minute_references/all_tail_hour_comparison.json';coarse = p.json.loads(coarse_path.read_text())
    coarse_proof_path = coarse_path.parent/'independent_hour_comparison_verification.json';coarse_proof = p.json.loads(coarse_proof_path.read_text())
    assert coarse_proof['passed'] and coarse_proof['audited_receipt_sha256'] == p.sha(coarse_path)
    requested = defaultdict(lambda:defaultdict(list));counts = Counter()
    for g in coarse['groups']:
        if g['date'] not in DAYS:continue
        data = p.Path(g['comparison_path']).read_bytes();assert p.hashlib.sha256(data).hexdigest() == g['comparison_sha256']
        for h in p.json.loads(data)['comparisons']:
            if h['model'] != 'floor_left' or h['symbol'] not in queries:continue
            if mask[offsets[h['date']]+h['slot'], indices[h['symbol']]]:counts['already_admitted_two_day_hours'] += 1;continue
            if not h.get('native_hour') or not h.get('differences'):counts['native_hour_absent'] += 1;continue
            requested[h['symbol'][:2]][h['symbol']].append(h)
    records = [];disqualified = [];scanned = []
    for prefix, stocks in sorted(requested.items()):
        frames = {}
        for date in DAYS:
            leaf = minute_leaves[date, prefix];pin = native_pins[leaf['path']];path = p.Path(leaf['path'])
            assert p.identity(path) == pin['verified_identity']
            pf = p.pq.ParquetFile(path);assert pf.metadata.num_rows == pin['rows']
            frames[date] = pf.read(columns=['ticker', 'left_minute', 'open', 'high', 'low', 'close', 'volume']).to_pandas()
            scanned.append(dict(date=date, prefix=prefix, path=str(path), accepted_sha256=pin['sha256'], verified_identity=pin['verified_identity'], rows=pin['rows']))
        for symbol, hours in sorted(stocks.items()):
            query = queries[symbol];path = p.Path(query['path']);assert p.identity(path) == query['verified_identity']
            data = path.read_bytes();assert p.hashlib.sha256(data).hexdigest() == query['sha256']
            text = data.decode();qrows, _ = p.json.JSONDecoder().raw_decode(text[text.index('['):])
            qrefs = {(q['day'][:10], int(q['day'][11:13])*60+int(q['day'][14:16])-1):q for q in qrows}
            bars = {date:frames[date].loc[frames[date].ticker.eq(symbol[:6])].set_index('left_minute') for date in DAYS}
            for h in hours:
                date, slot = h['date'], h['slot'];minutes = GRID[slot*60:(slot+1)*60];native = h['native_hour']
                ref = {f:native[f]-h['differences'][f] for f in ['open', 'high', 'low', 'close', 'volume']}
                frame = bars[date];assert not frame.index.duplicated().any()
                previous = max((int(m) for m in frame.index if m<minutes[0]), default=None)
                carry = int(frame.loc[previous, 'close']) if previous is not None else native['open']
                prior_q = qrefs.get((date, previous)) if previous is not None else None
                prior_exact = prior_q is not None and D(prior_q['close'])*10000 == carry
                formula = dict(open=carry, high=max(carry, native['high']), low=min(carry, native['low']), close=native['close'], volume=native['volume'])
                kind = 'direct_coarse_ohlcv_exact' if all(h['differences'][f] == 0 for f in ref) else 'fixed_previous_close_coarse_reconciled' if prior_exact and formula == ref else None
                absent = [];missing_reference = [];conflicts = []
                for m in minutes:
                    if m not in frame.index:absent.append(m);continue
                    row = frame.loc[m];q = qrefs.get((date, m))
                    if q is None:missing_reference.append(m);continue
                    if any(D(int(row[f])) != D(q[f])*10000 for f in ['open', 'high', 'low', 'close']) or D(int(row['volume'])) != D(q['volume']):conflicts.append(m)
                reason = 'coarse_conflict' if kind is None else 'independent_actual_minute_conflict' if conflicts else 'independent_actual_minute_reference_missing' if missing_reference else 'all60_already_actual_but_other_qualification_pending' if not absent else None
                record = dict(date=date, symbol=symbol, slot=slot, coarse_reference_kind=kind, native_hour=native, coarse_reference=ref,
                    previous_observed_minute=previous, previous_observed_close=carry, previous_close_independent_exact=prior_exact,
                    native_absent_minutes=absent, missing_independent_actual_minutes=missing_reference, conflicting_independent_actual_minutes=conflicts,
                    sina_reference_path=str(path), sina_reference_sha256=query['sha256'])
                if reason:counts[reason] += 1;record['reason'] = reason;disqualified.append(record)
                else:counts['hours_needing_real_zero_quote_witness'] += 1;records.append(record)
            assert p.identity(path) == query['verified_identity']
        for date in DAYS:
            leaf = minute_leaves[date, prefix];assert p.identity(p.Path(leaf['path'])) == native_pins[leaf['path']]['verified_identity']
        del frames, bars
    assert len(records)+len(disqualified) == sum(len(v) for ss in requested.values() for v in ss.values())
    assert p.identity(RAW/'dataset/hour_complete.npy') == mask_identity
    p.atomic_json(output, dict(status='complete_diagnostic_only_no_admission', checked_utc=p.utc(), source_code_sha256=p.sha(__file__),
        parent_raw_candidate=RAW.name, parent_raw_audit_sha256=p.sha(audit_path), public_reference_pins=response_pins,
        source_minute_progress_sha256=p.sha(progress_path), coarse_comparison_receipt_sha256=p.sha(coarse_path), coarse_proof_sha256=p.sha(coarse_proof_path),
        source_leaves_scanned_once=scanned, counts=dict(counts), records=records, rejected_or_pending=disqualified,
        candidate_hours_admitted=0, source_arrival_or_quote_witness_certified=False, missing_minutes_imputed=False,
        raw_candidate_modified=False, production_ready=False, peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
    lock.close()


if __name__ == '__main__':main()
