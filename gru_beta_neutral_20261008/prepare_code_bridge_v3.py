"""Create a fresh candidate; preserve both preceding candidates byte-for-byte."""
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / 'gru_beta_neutral_20261008'
BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
OUT = BASE / 'backfilled_panel_candidate_code_bridge_v3'
DEST_CODE = CODE / 'historical_code_bridge_v3'


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def main():
    assert not OUT.exists() and not DEST_CODE.exists()
    OUT.mkdir()
    DEST_CODE.mkdir()
    code_inputs = []
    for source in sorted((CODE / 'factor_bridge_v2').glob('*.py')):
        text = source.read_text().replace('backfilled_panel_candidate_factor_bridge_v2', 'backfilled_panel_candidate_code_bridge_v3')
        text = text.replace('this isolated v2 only', 'this isolated v3 only')
        (DEST_CODE / source.name).write_text(text)
        code_inputs.append({'source': str(source), 'source_sha256': sha(source), 'destination': str(DEST_CODE / source.name)})
    symbols = json.loads((ROOT / 'gru_research/artifacts/minute_calendar.json').read_text())['union_symbols']
    records = []
    for directory, suffix in [('symbol_hour_features', '.parquet'), ('donor_daily_audit', '.parquet'), ('symbol_receipts', '.json')]:
        (OUT / directory).mkdir()
        for symbol in symbols:
            if symbol in ['001914.SZ', '302132.SZ']:
                continue
            source = BASE / 'backfilled_panel_candidate' / directory / (symbol + suffix)
            destination = OUT / directory / source.name
            shutil.copyfile(source, destination)
            digest = sha(source)
            assert digest == sha(destination)
            records.append({'source': str(source), 'destination': str(destination), 'sha256': digest, 'bytes': source.stat().st_size})
    receipt = {'status': 'new_isolated_code_bridge_candidate_prepared', 'copied_unchanged_stocks': 1176,
               'regenerated_stocks': ['001914.SZ', '302132.SZ'], 'original_v1_and_factor_v2_modified': False,
               'source_code_sha256': sha(Path(__file__)), 'code_inputs': code_inputs, 'copied_files': records,
               'source_version_metadata': {name: sha(BASE / name / 'hour_panel_receipt.json') for name in
                   ['backfilled_panel_candidate', 'backfilled_panel_candidate_factor_bridge_v2']}}
    (OUT / 'version_preparation_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k not in ['copied_files', 'code_inputs']}), flush=True)


if __name__ == '__main__':
    main()
