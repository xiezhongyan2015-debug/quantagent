"""Conservative cash-tax reserve research account with genuine cash feedback.

``cash`` is spendable simulated cash. Reserved cash remains in the separate
gross settled balance; it is offset once by its explicit tax-liability reserve.
This is a statutory upper-bound scenario, not a point-final-net certification.
Unknown noncash tax transforms/rounding remain pending and invalidate net bounds.
"""
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'research_v3/src'))
from causal_cash_book import D, atomic
from pretax_replay_account import PretaxReplayAccount
from tax_bridge import CashTaxAssessmentBridge

RESERVE_PROTOCOL = 'PERSONAL_PUBLIC_FLOAT_FIFO_EOD_CASH_TAX_MAX20_PER_ENTITLED_SHARE_CENT_UPPER_RESERVE_V2'


class TaxReserveResearchAccount(PretaxReplayAccount):
    basis = 'AFTER_TRADING_COSTS_WITH_CONSERVATIVE_CASH_DIVIDEND_TAX_LIABILITY_RESERVE_RESEARCH'

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.tax_reserve = D(0)
        self.tax_bridge = None
        self.tax_reserve_complete = True
        self.tax_reserve_history = []

    @property
    def gross_settled_cash(self):
        return self.cash + self.tax_reserve

    @atomic
    def begin(self,day):
        if self.tax_bridge is None:
            index=self.sessions.index(day)
            self.tax_bridge=CashTaxAssessmentBridge(calendar=self.sessions[index:],
                calendar_source=self.calendar_source,protocol_source=RESERVE_PROTOCOL)
        return super().begin(day)

    @atomic
    def close(self):
        super().close()
        self.tax_bridge.settle_book_eod(self,noncash_coverage_source=self.calendar_source+'|FULL_DECLARED_ACTION_CATALOG_GATE')
        detail=self.tax_bridge.summary(actual_cash=self.gross_settled_cash,pretax_nav=D(1))
        self.tax_reserve_complete=detail['tax_ledger_complete']
        # Reserve definitely assessed cash tax and a maximum 20% of each
        # untransferred entitled cash right. Per-share ceil cents is an upper envelope,
        # explicitly not a claim about the official unknown rounding method.
        target=(detail['known_assessed_payable_upper']+detail['contingent_cash_rights_tax_upper']).quantize(
            D('.01'),rounding=ROUND_CEILING)
        previous=self.tax_reserve
        self.cash-=target-previous
        self.tax_reserve=target
        event={'day':self.day,'previous_reserve':previous,'tax_liability_upper_reserve':target,
            'reserve_delta':target-previous,'spendable_cash':self.cash,
            'gross_settled_cash':self.gross_settled_cash,'cash_deficit':max(D(0),-self.cash),
            'tax_ledger_complete':self.tax_reserve_complete,'protocol':RESERVE_PROTOCOL,
            'reserve_is_simulated_liability_not_actual_broker_debit':True,
            'unknown_noncash_tax_not_covered_by_finite_reserve':not self.tax_reserve_complete}
        self.tax_reserve_history.append(event)
        self.record('RESEARCH_CASH_DIVIDEND_TAX_UPPER_LIABILITY_RESERVED',event)

    def snapshot(self,*args,**kwargs):
        result=super().snapshot(*args,**kwargs)
        # Original gross income/realized-cost reconciliation stays intact. The
        # reserve reduces spendable cash and NAV once; report it separately.
        result['accounting_residual']+=self.tax_reserve
        gross_nav=result['nav']+self.tax_reserve
        detail=self.tax_bridge.summary(actual_cash=self.gross_settled_cash,pretax_nav=gross_nav) if self.tax_bridge else None
        result.update(gross_settled_cash=self.gross_settled_cash,spendable_cash=self.cash,
            tax_liability_upper_reserve=self.tax_reserve,nav_basis=self.basis,
            distribution_tax_liability_included=True,
            cash_tax_net_bounds_complete=self.tax_reserve_complete,
            cash_tax_rounding_officially_verified=False,
            simulated_reserve_protocol=RESERVE_PROTOCOL,
            net_NAV_contract_compatible=False,net_excess_return_point_acceptance=False)
        if detail:
            result['net_nav_lower_bound']=detail['net_nav_lower_bound_same_pretax_trading_path']
            result['net_nav_upper_bound']=detail['net_nav_upper_bound_same_pretax_trading_path']
            result['unresolved_tax_events']=detail['pending_noncash_events']
        return result
