"""One append-only aggregation of saved inventory artifacts; never opens source CSVs."""
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]
BASE = PROJECT / 'experiment_traces/meta_development_raw_coverage_v13'
ROOTS = [BASE / 'attempts' / n for n in ('20260906T184719050829Z', '20260906T190344337689Z_remaining')]
PLAN_HASHES = ['b927d358b108ff3ee55ff0dc9f4d055762ed72d1edda7b63ee17f9a137240f79', '12877dedad07091e4e9dce8d67b221da2c85ba9d93ee9abb65909d124a23a6bc']
OUT = BASE / 'union_001'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def dump(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write('\n')

def main():
    OUT.mkdir(exist_ok=False)
    dump(OUT / 'intent.json', {'started_at': datetime.now(timezone.utc).isoformat(), 'script_sha256': sha(Path(__file__).read_bytes()), 'sources': [str(x.relative_to(PROJECT)) for x in ROOTS], 'source_market_reads_permitted': False, 'new_model_calls_permitted': False, 'new_backtests_permitted': False})
    inputs = []
    def read(path, expected=None):
        data = path.read_bytes()
        digest = sha(data)
        if expected is not None:
            assert digest == expected, str(path)
        inputs.append({'path': path.relative_to(PROJECT).as_posix(), 'sha256': digest, 'bytes': len(data)})
        return data
    plans = [json.loads(read(r / 'plan.json', h)) for r, h in zip(ROOTS, PLAN_HASHES)]
    receipts = [json.loads(read(r / 'receipt.json')) for r in ROOTS]
    assert receipts[0]['status'] == 'budget_admission_stop'
    assert receipts[1]['status'] == 'all_codes_processed'
    assert plans[1]['codes'] == receipts[0]['unprocessed_codes']
    assert not receipts[1]['unprocessed_codes']
    assert plans[0]['days'] == plans[1]['days'] and len(plans[0]['days']) == 1217
    codes = [x['code'] for r in receipts for x in r['code_receipts']]
    assert codes == plans[0]['codes'] and len(codes) == len(set(codes)) == 846
    assert plans[1]['budget']['max_raw_and_calendar_bytes'] == plans[0]['budget']['max_raw_and_calendar_bytes'] - receipts[0]['read_bytes_charged']
    assert plans[1]['budget']['max_compressed_artifact_bytes'] == plans[0]['budget']['max_compressed_artifact_bytes'] - receipts[0]['compressed_artifact_byte_charge']
    years = defaultdict(Counter)
    totals, grid_reasons, member_reasons = Counter(), Counter(), Counter()
    code_summaries, rejected_members = [], []
    for root, plan, receipt in zip(ROOTS, plans, receipts):
        assert receipt['plan_sha256'] == sha((root / 'plan.json').read_bytes())
        batch = Counter()
        batch_reasons = Counter()
        for entry in receipt['code_receipts']:
            code = entry['code']
            folder = root / 'codes' / code
            stored = json.loads(read(folder / 'receipt.json'))
            assert stored == entry and entry['status'] == 'field_check_complete'
            read(folder / 'intent.json')
            read(folder / 'source_manifest.json', entry['source_manifest_sha256'])
            raw = read(folder / 'rows.json.gz', entry['rows_sha256'])
            rows = json.loads(gzip.decompress(raw))
            assert [r['date'] for r in rows] == plan['days']
            counts = Counter()
            reasons = Counter()
            for row in rows:
                assert row['code'] == code and row['execution_valid'] is False
                member = any(a <= row['date'] <= b for a, b in plan['membership_intervals'][code])
                assert row['historical_membership'] is member
                assert type(row['accepted']) is bool
                assert bool(row['reason_codes']) is not row['accepted']
                counts['requested'] += 1
                counts['accepted' if row['accepted'] else 'rejected'] += 1
                reasons.update(row['reason_codes'])
                y = years[row['date'][:4]]
                y['grid_requested'] += 1
                y['grid_accepted' if row['accepted'] else 'grid_rejected'] += 1
                if '\ufffd' in (row.get('stock_name') or ''):
                    counts['name_contains_unicode_replacement'] += 1
                if member:
                    counts['membership_requested'] += 1
                    counts['membership_accepted' if row['accepted'] else 'membership_rejected'] += 1
                    y['membership_requested'] += 1
                    y['membership_accepted' if row['accepted'] else 'membership_rejected'] += 1
                    if not row['accepted']:
                        member_reasons.update(row['reason_codes'])
                        for reason in row['reason_codes']:
                            y['membership_reason_' + reason] += 1
                        rejected_members.append({'code': code, 'date': row['date'], 'reason_codes': row['reason_codes']})
            assert counts['accepted'] == entry['accepted_days']
            assert counts['rejected'] == entry['rejected_days']
            assert counts['membership_requested'] == entry['membership_days']
            assert counts['membership_accepted'] == entry['accepted_membership_days']
            assert dict(reasons) == entry['reason_counts']
            batch.update(counts)
            batch_reasons.update(reasons)
            code_summaries.append({'code': code, 'run': root.name, **counts, 'reason_counts': dict(reasons)})
        assert batch['accepted'] == receipt['accepted_full_grid_days']
        assert batch['rejected'] == receipt['checked_rejected_full_grid_days']
        assert batch['membership_accepted'] == receipt['accepted_membership_days']
        assert dict(batch_reasons) == receipt['reason_counts']
        totals.update(batch)
        grid_reasons.update(batch_reasons)
    assert totals['requested'] == 1029582
    assert totals['membership_requested'] == 559770
    assert totals['membership_accepted'] == 548062
    assert totals['membership_rejected'] == 11708 == len(rejected_members)
    # Detect any source-artifact mutation during the aggregate without rereading market CSVs.
    for item in inputs:
        assert sha((PROJECT / item['path']).read_bytes()) == item['sha256']
    dump(OUT / 'input_manifest.json', inputs)
    dump(OUT / 'code_summary.json', code_summaries)
    dump(OUT / 'membership_rejected_rows.json', rejected_members)
    report = {'status': 'saved_union_verified', 'created_at': datetime.now(timezone.utc).isoformat(), 'original_codes': 846, 'unique_processed_codes': len(codes), 'unprocessed_codes': [], 'sessions': 1217, 'totals': dict(totals), 'grid_reason_counts': dict(grid_reasons), 'membership_reason_counts': dict(member_reasons), 'by_year': dict(years), 'source_read_bytes_charged_across_two_runs': sum(r['read_bytes_charged'] for r in receipts), 'compressed_artifact_byte_charge_across_two_runs': sum(r['compressed_artifact_byte_charge'] for r in receipts), 'reader_elapsed_seconds_across_two_runs': sum(r['elapsed_seconds'] for r in receipts), 'original_parent_status_retained': receipts[0]['status'], 'continuation_status': receipts[1]['status'], 'new_market_source_reads': 0, 'new_model_calls': 0, 'new_backtests': 0, 'returns_calculated': False, 'execution_valid': False, 'formal_target_success': False, 'limitations': ['Field acceptance is not price-source authenticity, PIT, historical membership authenticity, trading-state, actions, fee or capacity certification.', 'Missing rows are retained and not silently filled or excluded from the universe.', 'No pre-2017 warmup or post-2021 settlement obligations certified.', 'These are already-exposed development artifacts, not held-out evidence.'], 'self_check': {'assessment': 'Both disjoint runs close the original denominator; every saved row and registered artifact hash checked.', 'next_step': 'Resolve execution-source gaps under frozen bounded provenance checks before any real execution validation.', 'falsifiers': 'Any changed input hash, duplicate/missing code/day, membership mismatch or budget-reset discrepancy invalidates this aggregate.'}}
    report['output_sha256'] = {p.name: sha(p.read_bytes()) for p in OUT.iterdir() if p.is_file()}
    dump(OUT / 'receipt.json', report)
    print(json.dumps({k: v for k, v in report.items() if k not in ('output_sha256', 'limitations', 'self_check')}, ensure_ascii=True))

if __name__ == '__main__':
    main()
