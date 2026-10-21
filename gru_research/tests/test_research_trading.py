"""Dated-rule and cash-tax semantics; fixtures never enter market training."""
import unittest
from datetime import date, datetime, time
from decimal import Decimal as D
from dataclasses import replace
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / '.deps'))
import pandas as pd
from research_rule_scenarios import dated_limit_rate, execution_rule
from dev_rule_scenarios import CalendarEvidence, SecurityMetadata, RuleInputSource, SCENARIO, ORDINARY_ASSUMPTION
from next_session_execution import CN
from tax_bridge import CashTaxAssessmentBridge, share_unit_cent_bounds
from pretax_replay_account import PretaxReplayAccount, PretaxDistribution
from tax_reserve_account import TaxReserveResearchAccount


class ResearchTradingTests(unittest.TestCase):
    def test_official_st_effective_date_changes_only_main_board_band(self):
        before = date(2026,7,3); after = date(2026,7,6)
        for board in ('SSE_MAIN','SZSE_MAIN'):
            self.assertEqual(dated_limit_rate(board,before,'1'),D('.05'))
            self.assertEqual(dated_limit_rate(board,after,'1'),D('.10'))
            self.assertEqual(dated_limit_rate(board,before,'0'),D('.10'))
        for board in ('STAR','CHINEXT'):
            self.assertEqual(dated_limit_rate(board,after,'1'),D('.20'))
        with self.assertRaises(ValueError): dated_limit_rate('SSE_MAIN',after,1)
        with self.assertRaises(ValueError): dated_limit_rate('SSE_MAIN',date(2027,1,4),'1')

    def test_extended_rule_still_rejects_unknown_flags_future_metadata_and_strict_claim(self):
        ds = tuple(pd.bdate_range('2026-05-01','2026-07-08').date)
        calendar = CalendarEvidence(ds,'EXPLICIT_SYNTHETIC_TEST_CALENDAR',ds[0],ds[-1])
        target=date(2026,7,6); previous=date(2026,7,3)
        meta=SecurityMetadata('sh600004','SSE_MAIN',datetime.combine(previous,time(15),CN),
            'SYNTHETIC_METADATA',date(2003,4,28),'SYNTHETIC_IPO',calendar,True,'SYNTHETIC_MEMBER',
            known_special_regimes=(),special_status_coverage_complete=False,
            ordinary_regime_assumption=ORDINARY_ASSUMPTION,assumption_source='sha256:'+'a'*64)
        src=RuleInputSource('SYNTHETIC_SOURCE','sh600004',target,'SYNTHETIC_PREVCLOSE',
            'VENDOR_PREVCLOSE_REFERENCE_SCENARIO','SYNTHETIC_DAILY_STATUS','SYNTHETIC_DATED_MEMBERS',target)
        go=lambda m=meta,flag='1',mode=SCENARIO: execution_rule('sh600004',target,D(10),flag,'1',m,{'sh600004'},src,mode=mode)
        result=go();self.assertTrue(result.supported);self.assertEqual(result.rule.ceiling,D(11));self.assertFalse(result.verified_rules)
        self.assertFalse(go(flag=None).supported)
        self.assertFalse(go(m=replace(meta,known_at=datetime(2026,7,6,10,tzinfo=CN))).supported)
        self.assertFalse(go(mode='STRICT').supported)

    def test_fifo_cash_tax_keeps_record_rights_and_payable_when_cash_insufficient(self):
        ds=[date(2025,6,d) for d in (23,24,25,26,27,30)]+[date(2025,7,1),date(2025,7,2)]
        book=PretaxReplayAccount(10000,'SYNTHETIC_TAX_TEST_CAPITAL',sessions=ds,
            calendar_source='SYNTHETIC_CALENDAR',fee_source='SYNTHETIC_FEES')
        bridge=CashTaxAssessmentBridge(calendar=ds,calendar_source='SYNTHETIC_CALENDAR',protocol_source='SYNTHETIC_PROTOCOL')
        action=PretaxDistribution('cash','A',date(2025,6,24),date(2025,6,26),date(2025,6,27),
            date(2025,6,27),None,None,D('.157'),D(0),'LISTED_ONLY','SYNTHETIC_ENTITLEMENT',
            'SYNTHETIC_CASH_ACTION','PAY_AT_CLOSE_LIST_AT_OPEN_SELL_AT_EXPLICIT_DATE_RESEARCH_SCENARIO')
        for i,day in enumerate(ds[:-1]):
            book.begin(day)
            if i==0:book.fill('buy','A','BUY',100,D(10),D(0),ds[i+1])
            if day==action.record_on:book.announce(action)
            if day==date(2025,7,1):book.fill('sell','A','SELL',100,D(10),D(0),ds[i+1])
            book.close();bridge.settle_book_eod(book,noncash_coverage_source='SYNTHETIC_FULL_ACTION_COVERAGE')
        self.assertEqual(book.entitlements['cash'],100)
        self.assertEqual(book.cash,D('10015.70'))
        report=bridge.summary(actual_cash=D(1),pretax_nav=book.cash)
        self.assertEqual(report['assessed_unrounded_tax'],D('3.1400'))
        self.assertEqual(report['known_assessed_payable_upper'],D('4.00'))
        self.assertEqual(report['known_assessed_payable_lower'],D('3.00'))
        self.assertEqual(report['cash_shortfall_against_known_upper_payable'],D('3.00'))
        self.assertFalse(report['tax_liability_dropped_due_to_negative_cash'])
        self.assertFalse(report['net_excess_return_point_acceptance'])
        self.assertEqual(bridge.ledger.assessments[0]['acquired_on'],ds[0])

    def test_bonus_event_is_retained_pending_not_a_zero_tax_or_deleted_position(self):
        ds=[date(2025,1,d) for d in (2,3,6,7)]
        book=PretaxReplayAccount(10000,'SYNTHETIC',sessions=ds,calendar_source='SYNTHETIC',fee_source='SYNTHETIC')
        bridge=CashTaxAssessmentBridge(calendar=ds,calendar_source='SYNTHETIC',protocol_source='SYNTHETIC')
        action=PretaxDistribution('bonus','A',ds[0],ds[1],ds[2],ds[2],ds[2],ds[2],D('.157'),D('.1'),
            'LISTED_ONLY','SYNTHETIC_ENTITLEMENT','SYNTHETIC_BONUS','PAY_AT_CLOSE_LIST_AT_OPEN_SELL_AT_EXPLICIT_DATE_RESEARCH_SCENARIO')
        for i,day in enumerate(ds[:3]):
            book.begin(day)
            if i==0:book.fill('buy','A','BUY',100,D(10),D(0),ds[i+1])
            if i==1:book.announce(action)
            book.close();bridge.settle_book_eod(book,noncash_coverage_source='SYNTHETIC_FULL_ACTION_COVERAGE')
        self.assertEqual(book.quantity('A'),110)
        result=bridge.summary(actual_cash=book.cash,pretax_nav=10000)
        self.assertIn('bonus',result['pending_noncash_events'])
        self.assertIsNone(result['net_nav_lower_bound_same_pretax_trading_path'])
        self.assertFalse(result['tax_ledger_complete'])
        self.assertEqual(result['pending_cash_obligations']['bonus']['known_cash_tax_base'],D('15.700'))
        self.assertEqual(result['pending_cash_component_tax_upper_retained'],D('4.00'))

    def test_tax_reserve_changes_spendable_cash_once_and_preserves_gross_cash_reconciliation(self):
        ds=[date(2025,6,d) for d in (23,24,25,26,27,30)]+[date(2025,7,1),date(2025,7,2)]
        book=TaxReserveResearchAccount(10000,'SYNTHETIC_CAPITAL',sessions=ds,
            calendar_source='SYNTHETIC_CALENDAR',fee_source='SYNTHETIC_FEES')
        action=PretaxDistribution('cash','A',ds[1],ds[3],ds[4],ds[4],None,None,D('.157'),D(0),
            'LISTED_ONLY','SYNTHETIC_ENTITLEMENT','SYNTHETIC_CASH',
            'PAY_AT_CLOSE_LIST_AT_OPEN_SELL_AT_EXPLICIT_DATE_RESEARCH_SCENARIO')
        for i,day in enumerate(ds[:-1]):
            book.begin(day)
            if i==0:book.fill('buy','A','BUY',100,D(10),D(0),ds[i+1])
            if day==action.record_on:book.announce(action)
            if day==date(2025,7,1):book.fill('sell','A','SELL',100,D(10),D(0),ds[i+1])
            book.close()
            point=book.snapshot({'A':D(10)}, {'A'},valuation_on=day,valuation_source='SYNTHETIC_MARK',
                membership_on=day,membership_source='SYNTHETIC_MEMBERS')
            self.assertEqual(point['accounting_residual'],D(0))
        self.assertEqual(book.tax_reserve,D('4.00'))
        self.assertEqual(book.gross_settled_cash,D('10015.70'))
        self.assertEqual(book.cash,D('10011.70'))
        self.assertEqual(point['net_nav_lower_bound'],point['nav'])
        self.assertFalse(point['net_excess_return_point_acceptance'])

    def test_rounding_bounds_cover_per_share_and_coarser_aggregation(self):
        lower,upper=share_unit_cent_bounds(D('3.14'),100)
        self.assertEqual((lower,upper),(D('3.00'),D('4.00')))
        for observed in [D('3.00'),D('3.10'),D('3.14'),D('4.00')]:
            self.assertLessEqual(lower,observed);self.assertLessEqual(observed,upper)
        with self.assertRaises(ValueError):share_unit_cent_bounds(D('1'),D('1.5'))


if __name__=='__main__':unittest.main()
