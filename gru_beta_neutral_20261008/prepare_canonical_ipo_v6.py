"""Prepare V6 separately; regenerate only the two independently qualified stocks."""
import ast
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT/'gru_beta_neutral_20261008'
BASE = ROOT/'outputs/gru_beta_neutral_20261008'
SOURCE = BASE/'public_source_probes/ipo_factor_rounding_method_20261009'
OLD = BASE/'backfilled_panel_candidate_ipo_prefix_v5'
OUT = BASE/'backfilled_panel_candidate_canonical_ipo_v6'
DEST = CODE/'canonical_ipo_v6'


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def function(path,name):
    return ast.dump(next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name==name))


def main():
    admission = SOURCE/'isolated_research_prefix_admission.json'
    a = json.loads(admission.read_text())
    assert a['passed'] and a['qualified_prefix_hours']==8 and a['allowed_for_isolated_research_candidate_only']
    assert sha(CODE/'admit_canonical_ipo_prefix_for_research.py') == a['source_code_sha256']
    old_audit = json.loads((OLD/'independent_completion_audit.json').read_text())
    assert old_audit['passed'] and sha(OLD/'dataset/meta.json')==old_audit['candidate_meta_sha256']
    assert not OUT.exists() and not DEST.exists()
    OUT.mkdir(); DEST.mkdir()
    code_inputs=[]
    for source in sorted((CODE/'ipo_prefix_v5').glob('*.py')):
        target = DEST/('previous_observed_ipo_prefix.py' if source.name=='validated_ipo_factor_prefix.py' else source.name)
        text = source.read_text().replace('backfilled_panel_candidate_ipo_prefix_v5','backfilled_panel_candidate_canonical_ipo_v6')
        text = text.replace("'source_observed_IPO_factor_prefix_v5'","'canonical_basis_calculated_IPO_prefix_v6'")
        if source.name=='backfilled_hour_panel.py':
            old = "incoming.loc[incoming.observed_IPO_seed_prefix,'hour_source']='corroborated_hf_observed_IPO_seed'"
            new = "incoming.loc[incoming.observed_IPO_seed_prefix,'hour_source']='corroborated_hf_observed_IPO_seed'\n        if symbol in {'688582.SH','688709.SH'}:\n            incoming.loc[incoming.observed_IPO_seed_prefix,'hour_source']='corroborated_hf_calculated_canonical_IPO_prefix'"
            assert old in text
            text=text.replace(old,new)
        if source.name=='dense_backfill_candidate.py':
            text=text.replace('three independently validated source-observed IPO seed events','three source-observed IPO seeds and two independently qualified calculated canonical-basis IPO events')
        target.write_text(text)
        code_inputs.append({'source':str(source),'source_sha256':sha(source),'destination':str(target),'destination_sha256':sha(target)})
    for name in ['donor_qualification','observed_arrays','aggregate_matrix']:
        assert function(DEST/'backfilled_hour_panel.py',name)==function(CODE/'ipo_prefix_v5/backfilled_hour_panel.py',name)
    changed=set(a['research_prefix_symbols'])
    symbols=json.loads((ROOT/'gru_research/artifacts/minute_calendar.json').read_text())['union_symbols']
    copies=[]
    for directory,suffix in [('symbol_hour_features','.parquet'),('donor_daily_audit','.parquet'),('symbol_receipts','.json')]:
        (OUT/directory).mkdir()
        for symbol in symbols:
            if symbol in changed:continue
            source=OLD/directory/(symbol+suffix); destination=OUT/directory/source.name
            shutil.copyfile(source,destination); digest=sha(source)
            assert sha(destination)==digest
            copies.append({'source':str(source),'destination':str(destination),'sha256':digest,'bytes':source.stat().st_size})
    result={'status':'isolated_calculated_canonical_IPO_candidate_prepared','source_code_sha256':sha(__file__),
            'regenerated_stocks':sorted(changed),'copied_unchanged_stocks':len(symbols)-len(changed),
            'copied_files':copies,'code_inputs':code_inputs,'admission_receipt_sha256':sha(admission),
            'previous_candidate_meta_sha256':sha(OLD/'dataset/meta.json'),
            'daily_slow_features_require_full_recalculation':True,'hour_price_grid_execution_source_gates_unchanged':True,
            'canonical_daily_sources_modified':False,'prior_candidates_modified':False}
    (OUT/'version_preparation_receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ['copied_files','code_inputs']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
