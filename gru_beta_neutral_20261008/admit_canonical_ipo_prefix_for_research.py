"""Admit a two-day-bounded calculated prefix to an isolated research candidate.

The directly observed next-session reference equals the IPO close. This adds
the missing IPO effective date in the already-used retrospective canonical
basis; it does not replace minute quotes or widen future factor events.
"""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/gru_beta_neutral_20261008/public_source_probes/ipo_factor_rounding_method_20261009'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    output = OUT/'isolated_research_prefix_admission.json'
    assert not output.exists()
    vp = OUT/'independent_canonical_basis_prefix_verification.json'
    v = json.loads(vp.read_text())
    assert v['passed'] and v['qualified_unapplied_hours'] == 8
    assert sha(ROOT/'gru_beta_neutral_20261008/verify_ipo_factor_rounding_basis.py') == v['source_code_sha256']
    ap = Path(v['audited_receipt_path']); a = json.loads(ap.read_text())
    assert sha(ap) == v['audited_receipt_sha256']
    assert sha(v['official_table_observation_path']) == v['official_table_observation_sha256']
    assert sha(a['proposed_event_path']) == v['proposed_events_sha256']
    rows = []
    for r in a['records']:
        p = Path(r['primary_daily_factor_path'])
        assert sha(p) == r['primary_daily_factor_sha256']
        o = json.loads(p.read_text())
        daily = {d[0]:dict(zip(o['fields'],d)) for d in o['rows']}
        ipo, first = r['observed_IPO_seed_date'], r['first_canonical_factor_date']
        trading = sorted(d for d in daily if daily[d]['tradestatus']=='1' and Decimal(daily[d]['volume'])>0)
        assert trading[trading.index(ipo)+1] == first
        assert Decimal(daily[first]['preclose']) == Decimal(daily[ipo]['close'])
        assert Decimal(r['prefix_factor_in_existing_canonical_basis']) == Decimal(o['factors'][0][2])
        assert r['no_observed_action_between_IPO_and_first_canonical_event']
        assert all(s['ex_date']>first for s in r['action_steps'])
        assert all(r['minute_day_gate_checks'].values())
        assert sha(r['minute_source_path']) == r['minute_source_sha256']
        rows.append({'symbol':r['symbol'],'observed_ipo_event_date':ipo,
                     'first_canonical_factor_date':first,'factorless_trading_dates':[ipo],
                     'calculated_canonical_prefix_value':r['prefix_factor_in_existing_canonical_basis'],
                     'next_session_raw_preclose':daily[first]['preclose'],'IPO_raw_close':daily[ipo]['close'],
                     'next_session_reference_equals_IPO_close_exactly':True,
                     'source_record':r,'qualified_prefix_hours':4})
    receipt = {'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_code_sha256':sha(__file__),
               'independent_verification_path':str(vp),'independent_verification_sha256':sha(vp),
               'audit_receipt_path':str(ap),'audit_receipt_sha256':sha(ap),
               'proposed_event_path':a['proposed_event_path'],'proposed_events_sha256':v['proposed_events_sha256'],
               'records':rows,'research_prefix_symbols':[r['symbol'] for r in rows],'qualified_prefix_hours':8,
               'admission_basis':'For each actual IPO day only: independently pinned IPO seed date, next trading session raw preclose equals IPO raw close, no intervening observed action, all known canonical factors reproduced exactly at their declared precision, source cash divisor discrepancy independently explained and official rendered cash rows corroborated.',
               'canonical_factor_values_overwritten':False,'generic_pre_first_event_fill_used':False,
               'retrospective_canonical_normalization_not_historically_published_factor':True,
               'allowed_for_isolated_research_candidate_only':True,'candidate_already_changed':False,
               'complete_company_action_history_certified':False,'first_dividend_issuer_PDF_acquired':False,
               'production_ready':False}
    output.write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in receipt.items() if k!='records'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
