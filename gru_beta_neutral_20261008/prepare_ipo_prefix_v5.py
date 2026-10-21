"""Prepare an isolated fifth candidate for three source-observed IPO seeds."""
import ast
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / 'gru_beta_neutral_20261008'
BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
V4 = BASE / 'backfilled_panel_candidate_closing_rule_v4'
OUT = BASE / 'backfilled_panel_candidate_ipo_prefix_v5'
DEST = CODE / 'ipo_prefix_v5'
SOURCE = BASE / 'public_source_probes/sina_ipo_factor_prefix_20261009'


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def function(path, name):
    return ast.dump(next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name==name))


def main():
    receipt = json.loads((SOURCE/'prefix_validation_receipt.json').read_text())
    assert receipt['passed'] and receipt['qualified_prefix_hours']==24
    assert sha(CODE/'validate_sina_ipo_factor_prefix.py')==receipt['source_code_sha256']
    prior = json.loads((V4/'independent_completion_audit.json').read_text())
    assert prior['passed'] and sha(V4/'dataset/meta.json')==prior['candidate_meta_sha256']
    assert not OUT.exists() and not DEST.exists()
    OUT.mkdir();DEST.mkdir()
    code_inputs=[]
    for source in sorted((CODE/'historical_closing_rule_v4').glob('*.py')):
        text=source.read_text().replace('backfilled_panel_candidate_closing_rule_v4','backfilled_panel_candidate_ipo_prefix_v5')
        (DEST/source.name).write_text(text)
        code_inputs.append({'source':str(source),'source_sha256':sha(source)})
    for name in ['donor_qualification','observed_arrays','aggregate_matrix']:
        assert function(DEST/'backfilled_hour_panel.py',name)==function(CODE/'historical_closing_rule_v4/backfilled_hour_panel.py',name)
    changed=set(receipt['research_prefix_symbols'])
    symbols=json.loads((ROOT/'gru_research/artifacts/minute_calendar.json').read_text())['union_symbols']
    records=[]
    for directory,suffix in [('symbol_hour_features','.parquet'),('donor_daily_audit','.parquet'),('symbol_receipts','.json')]:
        (OUT/directory).mkdir()
        for symbol in symbols:
            if symbol in changed:continue
            source=V4/directory/(symbol+suffix);destination=OUT/directory/source.name
            shutil.copyfile(source,destination);digest=sha(source);assert sha(destination)==digest
            records.append({'source':str(source),'destination':str(destination),'sha256':digest,'bytes':source.stat().st_size})
    result={'status':'isolated_IPO_prefix_candidate_prepared','regenerated_stocks':sorted(changed),
            'copied_unchanged_stocks':len(symbols)-len(changed),'copied_files':records,'code_inputs':code_inputs,
            'daily_slow_features_require_full_recalculation':True,'canonical_daily_sources_modified':False,
            'hour_price_grid_execution_source_gates_unchanged':True,'previous_candidate_meta_sha256':sha(V4/'dataset/meta.json'),
            'prefix_validation_receipt_sha256':sha(SOURCE/'prefix_validation_receipt.json'),'prior_candidates_modified':False,
            'source_code_sha256':sha(Path(__file__))}
    (OUT/'version_preparation_receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ['copied_files','code_inputs']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
