"""Original source guards and explicit contract fixtures, never model returns."""
import ast
from copy import copy,deepcopy
from datetime import date
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE));sys.path.insert(0,str(HERE/'.deps'))
import primary_reference_inputs_v4 as source
from primary_reference_inputs_v4 import PrimaryReferenceInputsV4,PrimaryInputCatalogV4,apply_signature_correction
from trading_comparison_metrics_v4 import (compare_run,instrument_qualification_exposures,
    reference_resolution_audit)
from trading_comparison_metrics import compare_run as parent_compare_run
import test_primary_action_supplement_v3 as frozen_v3_fixture


class PrimaryV4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs=PrimaryReferenceInputsV4()
        frozen_v3_fixture.PrimaryV3Tests.setUpClass()
        cls.catalog=PrimaryInputCatalogV4(frozen_v3_fixture.PrimaryV3Tests.catalog,cls.inputs)

    def test_exact_fixed100_original_indices_and_primary_quotes(self):
        self.assertEqual(len(self.inputs.candidates),100)
        self.assertEqual({c['source_only_order'] for c in self.inputs.candidates.values()},set(range(1,101)))
        for key,c in self.inputs.candidates.items():
            with self.subTest(key=key):
                self.assertEqual(D(c['reference_cent']),D(c['original_reference']['vendor_ex_preclose']))
                self.assertFalse(c['actual_historical_arrival_verified'])
                self.assertFalse(c['tax_fee_noncash_or_broker_rounding_certified'])
        for order,cash,ratio,cent in [(69,'.5582','.1993','47.05'),(81,'.9993','.3997','117.95')]:
            c=next(c for c in self.inputs.cases.values() if c['source_only_order']==order)
            self.assertEqual(D(c['printed_virtual_cash']),D(cash))
            self.assertEqual(D(c['printed_virtual_ratio']),D(ratio))
            self.assertEqual(D(c['reference_cent']),D(cent))

    def test_all100_real_reviews_pass_and_missing_pass_cannot_admit(self):
        self.assertEqual({c['source_only_order'] for c in self.inputs.cases.values()},set(range(1,101)))
        self.assertEqual(self.inputs.pending_orders,[])
        self.inputs.require_all100_admitted()
        missing=copy(self.inputs)
        missing.cases={k:c for k,c in self.inputs.cases.items() if c['source_only_order']<=60}
        missing.pending_orders=list(range(61,101))
        with self.assertRaisesRegex(ValueError,'independent PASS'):
            missing.require_all100_admitted()

    def test_incomplete_duplicate_or_wrong_original_review_case_cannot_admit(self):
        cases=[dict(source_only_order=n,original_input_index=self.inputs.fixed_order[n-1][0],
                    symbol=self.inputs.fixed_order[n-1][1]['symbol'],
                    ex_date=self.inputs.fixed_order[n-1][1]['ex_date']) for n in range(61,101)]
        self.assertEqual(len(source.require_review_case_roster(cases,self.inputs.fixed_order,61,100)),40)
        for bad in (cases[:-1],cases[:-1]+[cases[0]],list(reversed(cases))):
            with self.assertRaisesRegex(ValueError,'review roster'):
                source.require_review_case_roster(bad,self.inputs.fixed_order,61,100)
        for field,value in [('original_input_index',-1),('symbol','CONTRACT_WRONG_CODE'),('ex_date','1900-01-01')]:
            bad=deepcopy(cases);bad[0][field]=value
            with self.assertRaisesRegex(ValueError,'case/index identity'):
                source.require_review_case_roster(bad,self.inputs.fixed_order,61,100)
        original=json.loads(source.REVIEW100.read_text())
        for change in ('missing','wrong_case'):
            # Contract-only reviewer records exercise the complete admission
            # path, even if a caller trusted the modified receipt's new SHA.
            modified=deepcopy(original)
            if change=='missing':modified['independent_reviewed_cases'].pop()
            else:modified['independent_reviewed_cases'][0]['original_input_index']=-1
            with tempfile.TemporaryDirectory() as temporary:
                path=Path(temporary)/'CONTRACT_CHANGED_REVIEW.json';path.write_text(json.dumps(modified))
                h=hashlib.sha256(path.read_bytes()).hexdigest()
                with patch.object(source,'REVIEW100_SHA',h):
                    with self.assertRaisesRegex(ValueError,'review roster|case/index identity'):
                        PrimaryReferenceInputsV4(path_relocations={source.REVIEW100:path})

    def test_append_only_signature6_10_19_preserves_all_other_fields(self):
        expected={6:'2025-05-08',10:'2025-05-15',19:'2025-05-23'}
        for c in self.inputs.cases.values():
            n=c['source_only_order']
            if n in expected:
                self.assertEqual(c['corrected_signature_date'],expected[n])
                correction=c['append_only_signature_correction']
                raw=json.loads(Path(correction['original_fact']['path']).read_text())
                self.assertEqual(raw['publication']['pdf_signature_date'],correction['original_value'])
                self.assertTrue(correction['no_other_fields_overridden'])
        c=next(c for c in self.inputs.cases.values() if c['source_only_order']==19)
        self.assertTrue(c['signature_date_after_api_publication_date'])

    def test_append_only_correction_wrong_old_value_rejected(self):
        corrections=json.loads((source.FIRST/'corrections_001_020_signature_dates.json').read_text())
        fact=json.loads(Path(corrections['corrections'][0]['original_fact']['path']).read_text())
        corrections['corrections'][0]['original_value']='1900-01-01'
        with self.assertRaisesRegex(ValueError,'old field'):
            apply_signature_correction(fact,corrections,self.inputs.checked)

    def test_append_only_correction_wrong_override_or_duplicate_rejected(self):
        original=json.loads((source.FIRST/'corrections_001_020_signature_dates.json').read_text())
        fact=json.loads(Path(original['corrections'][0]['original_fact']['path']).read_text())
        duplicate=deepcopy(original);duplicate['corrections'].append(deepcopy(duplicate['corrections'][0]))
        with self.assertRaisesRegex(ValueError,'duplicate'):
            apply_signature_correction(fact,duplicate,self.inputs.checked)
        different=deepcopy(original);different['corrections'][0]['supersedes_field']='dates.record_date'
        with self.assertRaisesRegex(ValueError,'only append-only'):
            apply_signature_correction(fact,different,self.inputs.checked)

    def test_changed_independent_review_bytes_fail_closed(self):
        for real in (source.REVIEW20,source.REVIEW40,source.REVIEW100):
            with self.subTest(review=real.name),tempfile.TemporaryDirectory() as temporary:
                changed=Path(temporary)/'changed_review.json';changed.write_text('{}')
                with self.assertRaisesRegex(ValueError,'source/review missing or changed'):
                    PrimaryReferenceInputsV4(path_relocations={real:changed})

    def test_changed_overlay_or_original_vendor_bytes_fail_closed(self):
        for real in [source.OVERLAY40,source.NORMALIZED/'corporate_actions.parquet',source.INPUT,source.PREFIX_PARSER]:
            with self.subTest(source=real.name),tempfile.TemporaryDirectory() as temporary:
                changed=Path(temporary)/real.name;changed.write_bytes(b'CONTRACT_CHANGED_BYTES_NOT_REAL_SOURCE')
                with self.assertRaisesRegex(ValueError,'missing or changed'):
                    PrimaryReferenceInputsV4(path_relocations={real:changed})

    def test_changed_actual_pdf_not_admitted(self):
        c=self.inputs.candidates[('sh689009','2025-06-13')]
        overlay=json.loads(source.OVERLAY40.read_text())
        real=next(x for x in overlay['cases'] if x['symbol']=='sh689009')['official_primary']['download_receipt']['local_original_path']
        with tempfile.TemporaryDirectory() as temporary:
            changed=Path(temporary)/'changed.pdf';changed.write_bytes(b'CONTRACT_NO_OFFICIAL_PDF')
            with self.assertRaisesRegex(ValueError,'missing or changed'):
                PrimaryReferenceInputsV4(path_relocations={real:changed})

    def test_reference_resolution_bound_original_row_and_not_ordinary_status(self):
        case=self.inputs.cases[('sz002557','2025-01-17')]
        proof=self.catalog.reference_resolution(case['original_reference'],{})
        self.assertEqual(proof['supplemental_status'],'VERIFIED_AS_PUBLISHED_PRIMARY_REFERENCE_V4_NOT_ORDINARY_FORMULA')
        changed=deepcopy(case['original_reference']);changed['vendor_ex_preclose']='0'
        with self.assertRaisesRegex(ValueError,'original row differs'):
            self.catalog.reference_resolution(changed,{})

    def test_future_outside_cutoff_not_admitted_and_both_cdr_references_stay_unknown(self):
        future=next(row for (symbol,d),row in self.inputs.exrows.items() if d>'2026-09-23')
        self.assertIsNone(self.catalog.reference_resolution(future,{}))
        for d in ('2025-06-13','2026-08-07'):
            admitted=self.inputs.cases[('sh689009',d)]
            proof=self.catalog.reference_resolution(admitted['original_reference'],{})
            self.assertIsNotNone(proof)
            self.assertEqual(proof['instrument_qualification_status'],'UNKNOWN')

    def test_actual_cdr_unit2025_and2026_never_underlying_or_double_count(self):
        a=self.inputs.candidates[('sh689009','2025-06-13')]
        b=self.inputs.candidates[('sh689009','2026-08-07')]
        self.assertEqual(D(a['actual_cash_per_entitled_unit']),D('1.131'))
        self.assertEqual(D(b['actual_cash_per_entitled_unit']),D('1.23852'))
        self.assertEqual((a['actual_unit'],b['actual_unit']),('ONE_CDR','ONE_CDR'))
        self.assertEqual(len(b['original_action_ids']),1)
        q=self.inputs.instrument_qualifications['sh689009']
        self.assertEqual(q['status'],'UNKNOWN');self.assertEqual(q['tax_policy_applicability'],'UNKNOWN')
        self.assertEqual(self.inputs.instrument_scope['historical_cdr_symbols'],['sh689009'])
        self.assertTrue(self.inputs.instrument_scope['future_new_cdr_symbols_or_other_universe_not_certified'])
        self.assertEqual(len(self.inputs.instrument_scope['fixed_universe_source_sha256']),2)
        # The original numeric vendor fields are rounded; the complete original
        # action text already preserves the exact entitled terms in case075.
        precise=self.inputs.candidates[('sz002831','2026-06-04')]
        row=precise['original_action_rows'][0]
        self.assertEqual(D(row['dividCashPsBeforeTax']),D('.803813'))
        self.assertEqual(D(precise['actual_cash_per_entitled_unit']),D('.8038127'))
        self.assertEqual(source.distribution_terms(row),(D('.8038127'),D('.4019063')))

    def fixture(self,buy_date):
        # Fixed contract-only capital/NAV/zero-fees; never fed to market/training.
        days=[date(2025,1,2),date(2025,1,3),date(2025,12,31),date(2026,1,5),date(2026,9,23)]
        result=dict(status='CONTRACT_FIXTURE_NOT_A_MODEL_REPLAY',
            completed_marks=[dict(date=d,nav='10000',violations=[],net_nav_lower_bound='10000',net_nav_upper_bound='10000') for d in days],
            account_event_ledger=[dict(day=buy_date,kind='FILL',payload=dict(symbol='sh689009',side='BUY',quantity=100))],
            outcomes=[dict(date=buy_date,symbol='sh689009',side='BUY',order_id='CONTRACT_CDR_BUY',filled=100,price='10',
                           fees={k:'0' for k in ('commission','transfer','stamp','total')},risk_after={'violations':[]})],
            provider_execution_events=[])
        return result,{d:D('1000') for d in days}

    def test_cdr_buy_without_any_dividend_ex_date_blocks_net_and_economic(self):
        result,benchmark=self.fixture(date(2025,1,3))
        self.assertEqual(reference_resolution_audit(result,[],catalog=self.catalog)['unresolved_actual_exposures'],[])
        summary=compare_run(result,benchmark,catalog=self.catalog,account_basis='tax_upper_reserve',slippage_by_order={'CONTRACT_CDR_BUY':0})
        for window in summary['windows'].values():
            self.assertTrue(window['full_mark_and_execution_coverage'])
            self.assertIsNotNone(window['scenario_values'])
            self.assertTrue(window['scenario_values_diagnostic_only'])
            self.assertIsNone(window['same_path_net_tax_bounds'])
            self.assertFalse(window['economic_comparison_eligible'])
            self.assertFalse(window['trading_fees_cny_certify_actual_cdr_fee_schedule'])

    def test_cdr2025_exposure_continuous2026_prior_anchor_pending(self):
        result,benchmark=self.fixture(date(2025,1,3))
        window=compare_run(result,benchmark,catalog=self.catalog,account_basis='tax_upper_reserve',slippage_by_order={'CONTRACT_CDR_BUY':0})['windows']['calendar_2026_through_sep23']
        self.assertTrue(window['prior_instrument_qualification_exposures_affect_anchor'])
        self.assertIsNone(window['same_path_net_tax_bounds'])
        self.assertFalse(window['economic_comparison_eligible'])

    def test_only2026_cdr_exposure_does_not_deny_complete2025(self):
        result,benchmark=self.fixture(date(2026,1,5))
        summary=compare_run(result,benchmark,catalog=self.catalog,account_basis='tax_upper_reserve',slippage_by_order={'CONTRACT_CDR_BUY':0})
        year2025=summary['windows']['calendar_2025_observed']
        self.assertTrue(year2025['instrument_tax_fee_scope_complete'])
        self.assertTrue(year2025['economic_comparison_eligible'])
        self.assertIsNotNone(year2025['same_path_net_tax_bounds'])
        self.assertFalse(summary['windows']['calendar_2026_through_sep23']['economic_comparison_eligible'])

    def test_unfilled_cdr_request_is_explicit_unknown_and_not_deleted(self):
        result,benchmark=self.fixture(date(2026,1,5))
        result['account_event_ledger']=[];result['outcomes'][0]['filled']=0
        exposures=instrument_qualification_exposures(result,catalog=self.catalog)
        self.assertEqual(len(exposures),1)
        self.assertIn('ACTUAL_REQUESTED_OR_FILLED_ORDER',exposures[0]['exposure_reasons'])
        self.assertEqual(result['outcomes'][0]['symbol'],'sh689009')

    def test_without_cdr_numeric_definitions_are_parent_exact(self):
        result,benchmark=self.fixture(date(2025,1,3));result['account_event_ledger']=[];result['outcomes']=[]
        parent=parent_compare_run(result,benchmark,account_basis='tax_upper_reserve')
        derived=compare_run(result,benchmark,catalog=self.catalog,account_basis='tax_upper_reserve')
        for name,window in parent['windows'].items():
            for field,value in window.items():
                self.assertEqual(derived['windows'][name][field],value)

    def test_catalog_delegates_all_original_account_rules_and_rights(self):
        self.assertIs(self.catalog.records,self.catalog.original.records)
        self.assertIs(self.catalog.by_record,self.catalog.original.by_record)
        self.assertIs(self.catalog.query_reconciliation,self.catalog.original.query_reconciliation)
        self.assertEqual(len(self.catalog.original.official_components),1)
        self.assertEqual(self.catalog.original.official_components[0].cash_gross_per_share,D('.014'))

    def test_old_driver_ast_unchanged_except_exact_v4_input_and_qualification_calls(self):
        def functions(name):
            return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse((HERE/name).read_text()).body
                    if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))}
        parent,derived=functions('trading_comparison_v3.py'),functions('trading_comparison_v4.py')
        self.assertEqual(set(parent),set(derived))
        changed={n for n in parent if parent[n]!=derived[n]}
        self.assertEqual(changed,{'verify_source_seal','run_manifest'})


if __name__=='__main__':unittest.main()
