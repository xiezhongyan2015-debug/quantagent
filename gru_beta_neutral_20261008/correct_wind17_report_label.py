"""Correct one prose stock-name label against the retained member dictionary."""
import csv
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
OUT = BASE / 'public_source_probes/additional_public_archives_20261009/wind17'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    path = BASE / '数据补齐核验与实验进度_2026-10-09.html'
    output = OUT / 'report_label_correction_receipt.json'
    assert not output.exists()
    source_path = ROOT / 'acquired_data/current_prediction_20260920/current_members.csv'
    with source_path.open(newline='') as stream:
        source_rows = list(csv.reader(stream))
    assert any('688708' in r and '佳驰科技' in r for r in source_rows)
    text = path.read_text()
    old, new = '白云机场、正元地信两个 2026 文件', '白云机场、佳驰科技（688708）两个 2026 文件'
    assert text.count(old) == 1
    before = sha(path)
    backup = OUT / 'report_before_stock_label_correction.html'
    assert not backup.exists()
    shutil.copyfile(path, backup)
    path.write_text(text.replace(old, new))
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'corrected_report_path': str(path), 'before_sha256': before, 'after_sha256': sha(path),
               'before_report_snapshot_path': str(backup), 'old_label': old, 'new_label': new,
               'stock_dictionary_path': str(source_path), 'stock_dictionary_sha256': sha(source_path),
               'scope': 'Prose name only; numeric code, source receipts, gap counts, models and candidate unchanged',
               'candidate_changed': False, 'new_hours_admitted': 0}
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
