"""Real cash-dividend FIFO/EOD tax integration with explicit unresolved bounds.

Reuses the original audited cash_dividend_tax ledger unchanged. This bridge
retains payable tax even when cash is insufficient. Unknown cent rounding
and noncash transformations prevent a point-net claim;
no zero-tax assumption or fictional broker receipt is substituted.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'research_v3/src'))
from causal_cash_book import D
from cash_dividend_tax import CashDividend, CashDividendTaxLedger

OFFICIAL_TAX_SOURCES = {
    '2015_101_current_rates': 'https://www.chinatax.gov.cn/chinatax/n810341/n810765/n1465977/n1466017/c1967339/content.html',
    '2012_85_fifo_eod_calendar': 'https://shanghai.chinatax.gov.cn/zcfw/zcfgk/grsds/201211/t400938.html',
    'official_csdcc_mechanics_republication': 'https://www.csrc.gov.cn/shenzhen/c105614/c1575308/content.shtml',
    'next_session_tax_detail_delivery': 'https://investor.szse.cn/institute/video/classroom/right/t20130416_538089.html',
    '2024_csdcc_fifo_case': 'https://www.csrc.gov.cn/shenzhen/c105614/c7521964/content.shtml',
}


def share_unit_cent_bounds(total, quantity):
    """Cent envelope independent of grouping of integer entitlement shares.

    No official per-share/lot/action/account rounding convention is asserted.
    Integer entitled shares are the finest allowed cash-right unit here.
    Fractional noncash rights remain blocked by a separate pending gate.
    """
    if type(quantity) is not int or quantity <= 0 or D(total) < 0:
        raise ValueError('positive integer share quantity and nonnegative cash tax required')
    unit = D(total) / quantity
    return (unit.quantize(D('.01'), rounding=ROUND_FLOOR) * quantity,
        unit.quantize(D('.01'), rounding=ROUND_CEILING) * quantity)


class CashTaxAssessmentBridge:
    def __init__(self, *, calendar, calendar_source, protocol_source,
                 month_end_policy='CLAMP_MONTH_END_SCENARIO'):
        self.ledger = CashDividendTaxLedger(account_kind='PERSONAL_ORDINARY_PUBLIC_FLOAT',
            calendar=list(calendar), calendar_source=calendar_source,
            tax_source='2015_101_RATES_WITH_2012_85_UNCHANGED_OPERATIONS:'+'|'.join(OFFICIAL_TAX_SOURCES.values()),
            month_end_policy=month_end_policy,
            anniversary_source=protocol_source+'|OFFICIAL_CHINACLEAR_FAQ_Q10_NATURAL_MONTH_YEAR_JANUARY_MONTH_END_EXAMPLES')
        self.protocol_source = protocol_source
        self.events = []; self.failure = None; self.pending_noncash = {}
        self.cash_debits = []; self.pending_cash_obligations = {}

    def _retain_uncertain_cash_action(self, action, quantity):
        ident = action.action_id
        # A failed EOD can have registered an action without capturing its
        # rights. Count preserved rights plus prior sale assessments, not just
        # presence of the action ID, before deciding what remains untracked.
        covered = sum(lot.quantity for lots in self.ledger.lots.values() for lot in lots if ident in lot.rights)
        covered += sum(a['quantity'] for a in self.ledger.assessments if a['action_id'] == ident)
        missing = max(0, quantity-covered)
        cash_base = D(action.cash_gross_per_share)*missing
        if not cash_base:
            return
        self.pending_cash_obligations[ident] = {'symbol':action.symbol,'original_record_quantity':quantity,
            'record_quantity':missing,'record_on':action.record_on,'ex_on':action.ex_on,
            'cash_gross_per_share':D(action.cash_gross_per_share),'known_cash_tax_base':cash_base,
            'known_cash_tax_lower':D(0),'known_cash_unrounded_tax_max':cash_base*D('.20'),
            'known_cash_tax_upper':share_unit_cent_bounds(cash_base*D('.20'),missing)[1],
            'source':action.event_source,'status':'PENDING_CASH_RIGHT_WITH_UNRESOLVED_FIFO_TRANSFORMATION'}

    def settle_book_eod(self, book, *, noncash_coverage_source):
        if book.day is None or not book.closed:
            raise ValueError('actual completed book EOD required before tax settlement')
        for ident, action in book.actions.items():
            quantity = book.entitlements.get(ident, 0)
            cash_base = D(action.cash_gross_per_share) * quantity
            if D(action.new_share_ratio) and quantity:
                self.pending_noncash[ident] = {'symbol':action.symbol,'record_quantity':book.entitlements[ident],
                    'record_on':action.record_on,'ex_on':action.ex_on,'ratio':str(action.new_share_ratio),
                    'cash_gross_per_share':D(action.cash_gross_per_share),'known_cash_tax_base':cash_base,
                    'known_cash_unrounded_tax_max':cash_base*D('.20'),
                    'known_cash_tax_upper':share_unit_cent_bounds(cash_base*D('.20'),quantity)[1],
                    'cash_rights_source':action.event_source,
                    'tax_base':None,'tax_lot_transformation':None,'status':'PENDING_PRIMARY_NONCASH_TAX_LOT_EVIDENCE'}
            if cash_base and (D(action.new_share_ratio) or self.failure is not None):
                self._retain_uncertain_cash_action(action,quantity)
            if self.failure is not None:
                continue
            if not D(action.new_share_ratio) and D(action.cash_gross_per_share) and ident not in self.ledger.actions:
                cash = CashDividend(ident, action.symbol, action.announced_on, action.record_on, action.ex_on,
                    D(action.cash_gross_per_share), action.event_source, D(0))
                self.ledger.announce(cash, available_on=book.day)
        if self.failure is not None:
            result = {'date':book.day,'status':'PENDING_PRIOR_UNRESOLVED_TAX',
                'failure':deepcopy(self.failure),'cash_action_book_rights_retained':True}
            self.events.append(result)
            return result
        quantities = {s:book.quantity(s) for s in book.lots if book.quantity(s)}
        noncash = [ident for ident,x in self.pending_noncash.items() if x['ex_on'] == book.day]
        try:
            result = self.ledger.settle_eod(book.day, quantities,
                quantity_source='ACTUAL_FULL_RESEARCH_ACCOUNT_EOD_LOTS:'+book.history[-1]['hash'],
                noncash_action_ids=noncash, noncash_coverage_source=noncash_coverage_source)
            self.events.append(deepcopy(result))
            return result
        except (ValueError, ArithmeticError) as exc:
            self.failure = {'day':book.day,'type':type(exc).__name__,'reason':str(exc),
                'tax_obligation_discarded':False,'point_net_acceptance':False}
            for ident,action in book.actions.items():
                if book.entitlements.get(ident,0) and D(action.cash_gross_per_share):
                    self._retain_uncertain_cash_action(action,book.entitlements[ident])
            self.events.append({'date':book.day,'status':'PENDING_UNRESOLVED_TAX','failure':deepcopy(self.failure)})
            return self.events[-1]

    def summary(self, *, actual_cash, pretax_nav):
        # Envelopes start from EACH ENTITLED SHARE and include per-share as
        # well as coarser lot/action/account cent rounding. No assumption of
        # one rounding per assessment is made.
        assessments = self.ledger.assessments
        exact = sum((a['unrounded_tax'] for a in assessments), D(0))
        lower = sum((share_unit_cent_bounds(a['unrounded_tax'],a['quantity'])[0] for a in assessments), D(0))
        upper = sum((share_unit_cent_bounds(a['unrounded_tax'],a['quantity'])[1] for a in assessments), D(0))
        pending = []; contingent_max = D(0)
        for symbol,lots in self.ledger.lots.items():
            for lot in lots:
                for ident,gross in lot.rights.items():
                    amount = D(gross)*lot.quantity
                    # Eventual exemption is possible; maximum statutory 20%
                    # is retained, including unresolved anniversary conventions.
                    bound = amount*D('.20')
                    monetary_upper = share_unit_cent_bounds(bound,lot.quantity)[1]
                    contingent_max += monetary_upper
                    pending.append({'symbol':symbol,'lot_id':lot.lot_id,'acquired_on':lot.acquired_on,
                        'action_id':ident,'quantity':lot.quantity,'gross_tax_base':amount,
                        'future_tax_min':D(0),'future_tax_unrounded_max':bound,
                        'future_tax_max':monetary_upper,'status':'CONTINGENT_UNTRANSFERRED_RIGHT'})
        unresolved_cash_upper = sum((p['known_cash_tax_upper'].quantize(D('.01'), rounding=ROUND_CEILING)
            for p in self.pending_cash_obligations.values()), D(0))
        contingent_max += unresolved_cash_upper
        deducted = sum((d['amount'] for d in self.cash_debits), D(0))
        due_low = max(D(0), lower-deducted)
        due_high = max(D(0), upper-deducted)+contingent_max
        complete = self.failure is None and not self.pending_noncash
        # If a noncash transformation broke FIFO, finite upper bounds on all
        # missing future tax cannot be asserted. Keep None, never coerce to 0.
        return {'scope':'FIFO_CASH_DIVIDEND_TAX_WITH_UNRESOLVED_NET_BOUNDS',
            'official_sources':OFFICIAL_TAX_SOURCES,'assessed_unrounded_tax':exact,
            'assessed_cent_lower':lower,'assessed_cent_upper':upper,'already_simulated_debits':deducted,
            'known_assessed_payable_lower':max(D(0),lower-deducted),
            'known_assessed_payable_upper':max(D(0),upper-deducted),
            'contingent_cash_rights_tax_upper':contingent_max,'pending_cash_rights':pending,
            'pending_cash_obligations':deepcopy(self.pending_cash_obligations),
            'pending_cash_component_tax_upper_retained':unresolved_cash_upper,
            'pending_noncash_events':deepcopy(self.pending_noncash),'tax_ledger_complete':complete,
            'tax_failure':deepcopy(self.failure), 'actual_broker_settlement_claim':False,
            'month_end_policy':self.ledger.month_end_policy,'official_cent_rounding_verified':False,
            'rounding_bounds_basis':'FLOOR_CEIL_CENTS_OF_EACH_INTEGER_ENTITLEMENT_SHARE_NOT_PER_ASSESSMENT',
            'month_end_official_examples_source':OFFICIAL_TAX_SOURCES['official_csdcc_mechanics_republication']+'#Q10',
            'simulated_debit_policy':None,
            'net_nav_upper_bound_same_pretax_trading_path':D(pretax_nav)-due_low if complete else None,
            'net_nav_lower_bound_same_pretax_trading_path':D(pretax_nav)-due_high if complete else None,
            'conservative_cash_after_full_upper_tax_reserve':D(actual_cash)-due_high if complete else None,
            'cash_shortfall_against_known_upper_payable':max(D(0),max(D(0),upper-deducted)-D(actual_cash)),
            'tax_liability_dropped_due_to_negative_cash':False,
            'bounds_change_cash_available_to_new_orders':False,
            'cash_feedback_not_integrated_requires_separate_reserve_driver':True,
            'net_excess_return_point_acceptance':False,
            'remaining':['official tax rounding/aggregation convention',
                'reserve/debit timing protocol with cash feedback',
                'primary bonus/conversion tax basis and FIFO transform where held']}

    def report(self, *, actual_cash, pretax_nav, cash_feedback_integrated=False):
        summary=self.summary(actual_cash=actual_cash,pretax_nav=pretax_nav)
        if cash_feedback_integrated:
            summary['bounds_change_cash_available_to_new_orders']=True
            summary['cash_feedback_not_integrated_requires_separate_reserve_driver']=False
            summary['simulated_reserve_policy']=self.protocol_source
            summary['remaining']=[x for x in summary['remaining'] if x!='reserve/debit timing protocol with cash feedback']
        return {'events':deepcopy(self.events),'assessments':deepcopy(self.ledger.assessments),'summary':summary}
