"""Unchanged V2 arithmetic with a separately verified primary reference gate."""
from trading_comparison_metrics import compare_run,WINDOWS
from trading_comparison_metrics import reference_exposures as v2_reference_exposures
from primary_action_supplement_v3 import PrimarySupplementCatalogV3


def reference_resolution_audit(result,checks,*,catalog):
    if type(catalog) is not PrimarySupplementCatalogV3:
        raise ValueError('V3 primary reference gate requires verified component catalog')
    catalog.supplement.verify()
    unresolved=[];resolved=[]
    for row in v2_reference_exposures(result,checks):
        proof=catalog.reference_resolution(row,result)
        (resolved if proof else unresolved).append(proof if proof else row)
    return dict(resolved_by_primary_evidence=resolved,unresolved_actual_exposures=unresolved,
        original_ex_reference_statuses_and_vendor_values_unmodified=True,
        reference_formula_evidence_not_noncash_FIFO_or_net_acceptance=True)


def reference_exposures(result,checks,*,catalog):
    return reference_resolution_audit(result,checks,catalog=catalog)['unresolved_actual_exposures']
