"""Independent exact-decimal enumeration of adjusted archive uncertainty."""
import csv
import hashlib
import json
import sys
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq

OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/huggingface/joinquant_targeted_concordance_20261009'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def candidates(value, factor, volume=False):
    value, factor = Decimal(value), Decimal(factor)
    assert Decimal('.4') < factor < 1
    center = int((value * factor if volume else value / factor * 100).to_integral_value(rounding=ROUND_HALF_UP))
    found = []
    for integer in range(center - 3, center + 4):
        raw = Decimal(integer) if volume else Decimal(integer) / 100
        adjusted = raw / factor if volume else raw * factor
        if adjusted.quantize(Decimal('1') if volume else Decimal('.01'), rounding=ROUND_HALF_UP) == value:
            found.append(raw)
    return found


def main():
    audit_path = OUT / 'targeted_concordance_receipt.json'
    recovery_path = OUT / 'singleton_partial_recovery/isolated_recovery_receipt.json'
    audit, recovery = json.loads(audit_path.read_text()), json.loads(recovery_path.read_text())
    assert recovery['source_audit_sha256'] == sha(audit_path)
    assert audit['source_code_sha256'] == sha(ROOT / 'gru_beta_neutral_20261008/audit_targeted_adjusted_minute_archive.py')
    assert recovery['source_code_sha256'] == sha(ROOT / 'gru_beta_neutral_20261008/recover_singleton_adjusted_minutes.py')
    assert recovery['status'] == 'unique_inverse_hypothesis_did_not_pass_amount_admission'
    assert not recovery['candidate_changed'] and recovery['minutes_admitted'] == 0
    groups = {}
    for source in audit['sources']:
        assert sha(source['full_path']) == source['full_sha256']
        assert sha(source['selected_path']) == source['selected_sha256']
        with Path(source['selected_path']).open(newline='') as stream:
            for row in csv.DictReader(stream):
                groups.setdefault((source['symbol'], row['Unnamed: 0'][:10]), []).append(row)
    ambiguity_total, amount_conflicts = 0, []
    for sample in audit['samples']:
        rows = groups[(sample['symbol'], sample['date'])]
        assert len(rows) == 240
        ambiguities = 0
        for i, row in enumerate(rows):
            prices = {field: candidates(row[field], row['factor']) for field in ['open', 'high', 'low', 'close']}
            ambiguities += sum(len(values) > 1 for values in prices.values())
            if i == 0:
                assert [int(value * 100) for value in prices['open']] == sample['source_first_open_possible_raw_cents']
            if sample['symbol'] == '000099.SZ' and all(len(values) == 1 for values in prices.values()):
                volumes = candidates(row['volume'], row['factor'], volume=True)
                assert len(volumes) == 1
                raw_volume, amount = volumes[0], Decimal(row['money'])
                lower, upper = prices['low'][0] * raw_volume, prices['high'][0] * raw_volume
                if amount < lower - 1 or amount > upper + 1:
                    amount_conflicts.append(row['Unnamed: 0'])
        assert ambiguities == sample['rounded_adjusted_price_cells_with_multiple_possible_raw_ticks']
        ambiguity_total += ambiguities
    assert ambiguity_total == audit['ambiguous_adjusted_price_cells']
    assert amount_conflicts == [row['timestamp'] for row in recovery['additional_amount_conflicts']]
    control = next(row for row in audit['samples'] if row['date'] == '2022-06-06')
    assert sha(control['original_raw_minute_path']) == control['original_raw_minute_sha256']
    original = pq.read_table(control['original_raw_minute_path']).to_pydict()
    order = sorted(range(len(original['datetime_nano'])), key=lambda i: original['datetime_nano'][i])
    for i, row in enumerate(groups[('002831.SZ', '2022-06-06')]):
        volume = candidates(row['volume'], row['factor'], volume=True)
        assert volume == [Decimal(str(original['volume'][order[i]]))]
        close = Decimal(str(original['close'][order[i]]))
        assert (close * Decimal(row['factor'])).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) == Decimal(row['close'])
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
        'source_code_sha256': sha(Path(__file__)), 'audited_receipt_sha256': sha(audit_path),
        'recovery_hypothesis_receipt_sha256': sha(recovery_path),
        'independent_decimal_enumerated_ambiguous_price_cells': ambiguity_total,
        'independent_amount_conflicts': amount_conflicts, 'control_close_and_volume_checks': 240,
        'candidate_hours_admitted': 0, 'candidate_changed': False}
    (OUT / 'independent_findings_verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
