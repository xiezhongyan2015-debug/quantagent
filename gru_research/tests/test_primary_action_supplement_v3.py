"""Readonly genuine fact checks and isolated accounting contract fixtures.

Fixture cash, fills and marks never enter training/market data or strategy
returns. No actual frozen score/holdings CSV or real-model replay is opened.
"""
import ast
from copy import deepcopy,copy
from dataclasses import replace
from datetime import date
from decimal import Decimal as D, ROUND_CEILING
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE));sys.path.insert(0,str(HERE/'.deps'))
import pandas as pd
import primary_action_supplement_v3 as primary
from research_action_catalog import ResearchActionCatalog
from scenario_action_catalog import IncompleteActionEvidence,vendor_action
from tax_reserve_account import TaxReserveResearchAccount
from pretax_replay_account import PretaxReplayAccount
from tax_bridge import share_unit_cent_bounds
from trading_comparison_metrics_v3 import reference_resolution_audit,compare_run
from trading_comparison_metrics import compare_run as v2_compare_run


class PrimaryV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proof=primary.PrimaryActionSupplementV3()
        cls.actions=pd.read_parquet(primary.NORMALIZED/'corporate_actions.parquet').to_dict('records')
        cls.coverage=pd.read_parquet(primary.NORMALIZED/'corporate_action_query_coverage.parquet').to_dict('records')
        cls.queries={r['source_sha256']:Path(r['source_path']).read_bytes() for r in cls.coverage}
        cls.catalog_source=json.loads((primary.NORMALIZED/'coverage_audit.json').read_text())['catalog_source_identity']
        cls.original=ResearchActionCatalog(cls.actions,cls.coverage,source_identity=cls.catalog_source,
            query_bytes_by_sha256=cls.queries)
        cls.catalog=primary.PrimarySupplementCatalogV3(cls.original,cls.proof)

    def test_nine_real_facts_all_exact_cent_and_never_change_entitled_to_virtual(self):
        self.assertEqual(set(self.proof.cases),primary.EXPECTED_KEYS)
        self.assertEqual(self.proof.verified_files,79)
        for key,c in self.proof.cases.items():
            with self.subTest(key=key):
                calc=c['as_published_expression_using_observed_previous_raw_close']
                self.assertEqual(D(calc['reference_cent']),D(c['original_frozen_ex_reference']['vendor_ex_preclose']))
                self.assertFalse(c['official_exchange_reference_quote_certified'])
                if key!=primary.GAP_KEY:
                    for row in self.proof.base_action_rows[key]:
                        action=vendor_action(row)
                        self.assertEqual(action.cash_gross_per_share,D(c['actual_cash_per_entitled_share']))
                        self.assertEqual(action.new_share_ratio,D(c['actual_capital_conversion_per_entitled_share']))

    def test_two_published_precision_differences_keep_exact_sharebase_diagnostic_only(self):
        for key,published,alternative in [(('sh688498','2026-05-18'),'1075.73','1075.76'),
                (('sh688608','2025-07-02'),'241.20','241.19')]:
            c=self.proof.cases[key]
            self.assertEqual(c['as_published_expression_using_observed_previous_raw_close']['reference_cent'],published)
            self.assertEqual(c['sharebase_alternatives_diagnostic_only']['exact_participating_basis_cent'],alternative)
            self.assertFalse(c['sharebase_alternatives_diagnostic_only']['active'])

    def test_official_missing_component_is_separate_from_untouched_original_cash_id(self):
        original=vendor_action(self.proof.base_action_rows[primary.GAP_KEY][0])
        extra=self.catalog.official_components[0]
        self.assertNotEqual(original.action_id,extra.action_id)
        self.assertEqual(original.action_id,'3b46114af50b9241818e365d6748511259a97376d75a96de28e8229d0607b316')
        self.assertEqual((original.cash_gross_per_share,extra.cash_gross_per_share),(D('.070'),D('.014')))
        self.assertEqual(extra.announced_on,date(2025,7,3))
        self.assertEqual((extra.record_on,extra.ex_on,extra.pay_on),(date(2025,7,9),date(2025,7,10),date(2025,7,10)))
        event=json.loads(extra.event_source)
        self.assertFalse(event['actual_historical_source_arrival_verified'])
        self.assertEqual(event['source_kind'],'OFFICIAL_PRIMARY_NOT_VENDOR_ROW_OR_QUERY_INDEX')
        self.assertEqual(len(self.catalog.query_reconciliation),len(self.original.query_reconciliation))
        self.assertIs(self.catalog.records,self.original.records)

    def fixture_book(self,kind):
        # Explicit small accounting fixture; prices and fills are not real observations.
        days=[date(2025,7,d) for d in (8,9,10,11,14)]
        book=kind(10000,'SYNTHETIC_ACCOUNT_CONTRACT_CAPITAL',sessions=days,
            calendar_source='SYNTHETIC_ACCOUNT_CONTRACT_SESSION_SUBSET',fee_source='SYNTHETIC_ZERO_FEE_CONTRACT')
        for i,day in enumerate(days[:-1]):
            book.begin(day)
            if i==0:book.fill('SYNTHETIC_BUY','sh601966','BUY',100,D(10),D(0),days[i+1])
            if day==date(2025,7,9):self.catalog.announce_held_record_actions(book,day)
            if day==date(2025,7,11):book.fill('SYNTHETIC_SELL','sh601966','SELL',100,D(10),D(0),days[i+1])
            book.close()
            point=book.snapshot({'sh601966':D(10)},{'sh601966'},valuation_on=day,
                valuation_source='SYNTHETIC_MARK',membership_on=day,membership_source='SYNTHETIC_MEMBERS')
            self.assertEqual(point['accounting_residual'],D(0))
        return book,point

    def test_two_components_actual_cash_and_record_rights_conserve_in_pretax_fixture(self):
        book,point=self.fixture_book(PretaxReplayAccount)
        self.assertEqual(book.cash,D('10008.40'))
        self.assertEqual(book.distribution_income,D('8.40'))
        self.assertEqual(sorted(book.entitlements.values()),[100,100])
        self.assertEqual(len(book.actions),2)
        amounts=[e['payload']['amount'] for e in book.history if e['kind']=='GROSS_DIVIDEND_PAID_SCENARIO']
        self.assertEqual(sorted(amounts),[D('1.40'),D('7.00')])
        self.assertTrue(book.verify_history())

    def test_two_components_tax_reserve_cash_nav_and_fifo_rights_conserve(self):
        book,point=self.fixture_book(TaxReserveResearchAccount)
        self.assertEqual(book.gross_settled_cash,D('10008.40'))
        self.assertEqual(book.tax_reserve,D('3.00'))
        self.assertEqual(book.cash,D('10005.40'))
        self.assertEqual(len(book.tax_bridge.ledger.assessments),2)
        self.assertEqual(sorted(a['unrounded_tax'] for a in book.tax_bridge.ledger.assessments),[D('.2800'),D('1.4000')])
        detail=book.tax_bridge.summary(actual_cash=book.gross_settled_cash,pretax_nav=book.gross_settled_cash)
        self.assertEqual((detail['assessed_cent_lower'],detail['assessed_cent_upper']),(D('1.00'),D('3.00')))
        self.assertEqual((point['net_nav_lower_bound'],point['net_nav_upper_bound']),(D('10005.40'),D('10007.40')))
        self.assertFalse(point['net_excess_return_point_acceptance'])

    def test_rounding_counterexample_missing_two_components_must_not_merge(self):
        upper=sum((share_unit_cent_bounds(c*D('.20'),1)[1] for c in (D('.014'),D('.070'))),D(0))
        merged=share_unit_cent_bounds(D('.084')*D('.20'),1)[1]
        self.assertEqual((upper,merged),(D('.03'),D('.02')))

    def test_existing301308_two_original_components_keep_distinct_ids_and_tenpercent_upper(self):
        rows=[r for r in self.original.records['sz301308'] if r['dividOperateDate']=='2026-06-02']
        self.assertEqual(len(rows),2)
        actions=[vendor_action(r) for r in rows]
        self.assertEqual(len({a.action_id for a in actions}),2)
        self.assertEqual(sorted(a.cash_gross_per_share for a in actions),[D('.3467605'),D('.6439837')])
        upper=sum((share_unit_cent_bounds(a.cash_gross_per_share*D('.10'),1)[1] for a in actions),D(0))
        merged=share_unit_cent_bounds(sum((a.cash_gross_per_share for a in actions),D(0))*D('.10'),1)[1]
        self.assertEqual((upper,merged),(D('.11'),D('.10')))

    def relocated_corruption(self,path):
        with tempfile.TemporaryDirectory() as directory:
            new=Path(directory)/Path(path).name
            new.write_bytes(Path(path).read_bytes()+b'\nISOLATED_NEGATIVE_TEST_CHANGED_BYTES')
            with self.assertRaises(ValueError):primary.PrimaryActionSupplementV3(path_relocations={path:new})

    def test_changed_primary_pdf_fails_closed_in_isolated_copy(self):
        self.relocated_corruption(self.proof.cases[primary.GAP_KEY]['official_primary_source']['path'])

    def test_changed_overlay_fails_closed_in_isolated_copy(self):
        self.relocated_corruption(primary.OVERLAY)

    def test_changed_original_vendor_normalized_file_fails_closed_in_isolated_copy(self):
        self.relocated_corruption(primary.NORMALIZED/'corporate_actions.parquet')

    def test_changed_reference_identity_fails_without_relabeling_original_as_ordinary(self):
        row=deepcopy(self.proof.cases[primary.GAP_KEY]['original_frozen_ex_reference'])
        row['vendor_ex_preclose']='14.86'
        with self.assertRaises(ValueError):self.catalog.reference_resolution(row,{'account_event_ledger':[]})
        row['vendor_ex_preclose']='14.85';row['status']='MATCHES_ORDINARY_NO_RIGHTS_FORMULA'
        with self.assertRaises(ValueError):self.catalog.reference_resolution(row,{'account_event_ledger':[]})

    def test_duplicate_official_or_original_source_component_rejected(self):
        extra=self.catalog.official_components[0]
        original=vendor_action(self.proof.base_action_rows[primary.GAP_KEY][0])
        with self.assertRaises(ValueError):primary.validate_component_uniqueness((extra,extra),(original,))
        with self.assertRaises(ValueError):primary.validate_component_uniqueness((replace(extra,cash_gross_per_share=D('.070')),),(original,))
        with self.assertRaises(ValueError):ResearchActionCatalog(self.actions+[self.actions[0]],self.coverage,
            source_identity=self.catalog_source,query_bytes_by_sha256=self.queries)

    def test_record_callback_duplicate_extra_rejected_before_any_new_announcement(self):
        ds=[date(2025,7,d) for d in (8,9,10,11)]
        book=PretaxReplayAccount(10000,'SYNTHETIC_CAPITAL',sessions=ds,calendar_source='SYNTHETIC_CAL',fee_source='SYNTHETIC_FEES')
        book.begin(ds[0]);book.fill('SYNTHETIC_BUY','sh601966','BUY',100,D(10),D(0),ds[1]);book.close();book.begin(ds[1])
        self.catalog.announce_held_record_actions(book,ds[1]);before=len(book.history)
        with self.assertRaises(ValueError):self.catalog.announce_held_record_actions(book,ds[1])
        self.assertEqual(len(book.history),before);self.assertEqual(len(book.actions),2)

    def test_reference_gate_requires_official_component_and_both_held_record_actions(self):
        row=deepcopy(self.proof.cases[primary.GAP_KEY]['original_frozen_ex_reference'])
        original=self.catalog.original_gap_action_ids[0]
        result={'account_event_ledger':[dict(kind='PRETAX_ACTION_SCENARIO_ANNOUNCED',payload={'action_id':original})]}
        with self.assertRaises(ValueError):self.catalog.reference_resolution(row,result)
        result['account_event_ledger'].append(dict(kind='PRETAX_ACTION_SCENARIO_ANNOUNCED',payload={'action_id':self.catalog.official_components[0].action_id}))
        resolved=self.catalog.reference_resolution(row,result)
        self.assertEqual(resolved['original_status'],'DIFFERENTIAL_DIVIDEND_OR_SPECIAL_FORMULA_REQUIRES_PRIMARY')
        self.assertTrue(resolved['cash_component_catalog_installed'])
        unconnected=copy(self.catalog);unconnected.installed_supplement_component_ids=frozenset()
        with self.assertRaises(ValueError):unconnected.reference_resolution(row,result)

    def test_unknown_reference_still_blocks_and_future39_are_not_reclassified_historical(self):
        overlay=json.loads(primary.OVERLAY.read_text());unknown=overlay['unknown_historical_cases'][0]
        self.assertIsNone(self.catalog.reference_resolution(unknown,{}))
        exposed={'account_event_ledger':[], 'outcomes':[{'date':unknown['ex_date'],'symbol':unknown['symbol']}],
            'provider_execution_events':[]}
        result=reference_resolution_audit(exposed,[unknown],catalog=self.catalog)
        self.assertEqual(len(result['unresolved_actual_exposures']),1)
        self.assertEqual(len(result['resolved_by_primary_evidence']),0)
        # Deliberately small metric fixture: it is not an actual CSI/model NAV path.
        points=['2025-01-02','2025-12-31','2026-09-23']
        book_result=dict(status='SYNTHETIC_METRIC_CONTRACT',outcomes=[],provider_execution_events=[],
            completed_marks=[dict(date=d,nav=D(100),net_nav_lower_bound=D(99),net_nav_upper_bound=D(100),violations=[]) for d in points])
        summary=compare_run(book_result,{date.fromisoformat(d):D(100) for d in points},
            account_basis='tax_upper_reserve',special_reference_exposures=[unknown])
        self.assertFalse(summary['windows']['whole_observed']['economic_comparison_eligible'])
        self.assertIsNone(summary['windows']['whole_observed']['same_path_net_tax_bounds'])
        future=json.loads((primary.SUPPLEMENT/'future39_outside_frozen_history.json').read_text())
        self.assertTrue(all(r['ex_date']>'2026-09-23' for r in future['events']))
        with self.assertRaises(IncompleteActionEvidence):self.catalog.require_coverage('sh999999',[2025])

    def test_v2_arithmetic_policies_and_unmodified_driver_functions_reused(self):
        self.assertIs(compare_run,v2_compare_run)
        a=ast.parse((HERE/'trading_comparison.py').read_text());b=ast.parse((HERE/'trading_comparison_v3.py').read_text())
        old={n.name:ast.dump(n,include_attributes=False) for n in a.body if isinstance(n,ast.FunctionDef)}
        new={n.name:ast.dump(n,include_attributes=False) for n in b.body if isinstance(n,ast.FunctionDef)}
        self.assertEqual(set(old),set(new))
        self.assertEqual({k for k in old if old[k]!=new[k]},{'verify_source_seal','run_manifest'})
        before=json.loads((primary.SUPPLEMENT/'frozen_input_guard_before.json').read_text())
        for path,h in before.items():self.assertEqual(primary.sha(path),h)


if __name__=='__main__':unittest.main()
