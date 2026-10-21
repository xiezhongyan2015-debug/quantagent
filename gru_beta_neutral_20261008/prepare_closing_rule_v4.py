"""Prepare an isolated fourth candidate and reuse unchanged daily calculations."""
import ast
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / 'gru_beta_neutral_20261008'
BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
V3 = BASE / 'backfilled_panel_candidate_code_bridge_v3'
OUT = BASE / 'backfilled_panel_candidate_closing_rule_v4'
DEST_CODE = CODE / 'historical_closing_rule_v4'
AUDIT = BASE / 'public_source_probes/sse_historical_close_rule_20261009'


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def function_body(path, name):
    parsed = ast.parse(path.read_text())
    node = next(n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.dump(node)


def main():
    proof = json.loads((AUDIT / 'audit_receipt_v2.json').read_text())
    assert proof['status'] == 'historical_SSE_closing_statistics_corroborated'
    assert sha(CODE / 'audit_historical_sse_closing_semantics_v2.py') == proof['source_code_sha256']
    assert not OUT.exists() and not DEST_CODE.exists()
    OUT.mkdir()
    DEST_CODE.mkdir()
    code_inputs = []
    for source in sorted((CODE / 'historical_code_bridge_v3').glob('*.py')):
        contents = source.read_text().replace('backfilled_panel_candidate_code_bridge_v3', 'backfilled_panel_candidate_closing_rule_v4')
        (DEST_CODE / source.name).write_text(contents)
        code_inputs.append({'source': str(source), 'source_sha256': sha(source)})
    assert function_body(DEST_CODE / 'backfilled_hour_panel.py', 'factor_state') == function_body(CODE / 'historical_code_bridge_v3/backfilled_hour_panel.py', 'factor_state')
    changed = set(proof['qualified_symbols'])
    symbols = json.loads((ROOT / 'gru_research/artifacts/minute_calendar.json').read_text())['union_symbols']
    assert changed <= set(symbols)
    records = []
    for directory, suffix in [('symbol_hour_features', '.parquet'), ('donor_daily_audit', '.parquet'), ('symbol_receipts', '.json')]:
        (OUT / directory).mkdir()
        for symbol in symbols:
            if symbol in changed:
                continue
            source = V3 / directory / (symbol + suffix)
            destination = OUT / directory / source.name
            shutil.copyfile(source, destination)
            expected = sha(source)
            assert expected == sha(destination)
            records.append({'source': str(source), 'destination': str(destination), 'sha256': expected, 'bytes': source.stat().st_size})
    shutil.copytree(V3 / 'slow_features', OUT / 'slow_features')
    slow_files = []
    for source in sorted((V3 / 'slow_features').rglob('*')):
        if source.is_file():
            destination = OUT / 'slow_features' / source.relative_to(V3 / 'slow_features')
            digest = sha(source)
            assert digest == sha(destination)
            slow_files.append({'source': str(source), 'destination': str(destination), 'sha256': digest})
    calculation = CODE / 'historical_code_bridge_v3/backfilled_slow_panel.py'
    slow_receipt = json.loads((OUT / 'slow_features/slow_panel_receipt.json').read_text())
    assert sha(calculation) == slow_receipt['source_contract']['code_sha256']
    shutil.copyfile(calculation, OUT / 'slow_features/calculation_source.py')
    for record in slow_receipt['primary_sources']:
        assert sha(BASE / 'public_source_probes/baostock/union_daily_factors/normalized' / (record['symbol'] + '.json')) == record['primary_sha256']
    result = {'status': 'isolated_closing_rule_candidate_prepared', 'regenerated_stocks': sorted(changed),
              'copied_unchanged_stocks': len(symbols) - len(changed), 'copied_files': records, 'slow_feature_files_reused': slow_files,
              'slow_calculation_source_sha256': sha(calculation), 'factor_state_function_ast_equal_v3': True,
              'all_daily_factor_inputs_unchanged': True, 'daily_slow_features_require_no_recalculation': True,
              'old_candidates_modified': False, 'code_inputs': code_inputs, 'source_code_sha256': sha(Path(__file__)),
              'historical_closing_audit_receipt_sha256': sha(AUDIT / 'audit_receipt_v2.json'),
              'source_version_metadata': {name: sha(BASE / name / 'hour_panel_receipt.json') for name in
                  ['backfilled_panel_candidate', 'backfilled_panel_candidate_factor_bridge_v2', 'backfilled_panel_candidate_code_bridge_v3']}}
    (OUT / 'version_preparation_receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k not in ['copied_files', 'code_inputs', 'slow_feature_files_reused', 'regenerated_stocks']}), flush=True)


if __name__ == '__main__':
    main()
