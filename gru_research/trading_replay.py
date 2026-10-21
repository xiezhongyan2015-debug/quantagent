# Research copy of frozen orchestration with independently bound TWAP executor,
# cash-tax assessment trace and incomplete-window gate. Original rules retained.
# Original research_v3/src/pretax_portfolio_replay.py SHA256: 1d52118902f06748b838d6b295f5ec6d1757275437ef97437597c422d03ac3b0
"""Compose the frozen planner, executor and explicitly pretax cash journal.

The trusted data provider must expose prior-close snapshots separately from
execution contexts. No daily high/low is used for a fill path. An incomplete
run remains incomplete; its valid prefix is never relabelled as a full window.
"""
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from copy import deepcopy

from causal_cash_book import D
from pretax_replay_account import PretaxReplayAccount
from prior_day_order_planner import PlannerState,plan_orders
from next_session_execution import FixedOrder
from extended_costs import ResearchTWAPExecutor
from tax_bridge import CashTaxAssessmentBridge
from pretax_portfolio_replay import MarketContext


def replay_research_pretax(provider,*,model,valuation_days,costs,capital=Decimal('1000000'),run_id):
    """Run all declared days or return INCOMPLETE with a traceable last mark.

    Provider contract:
      calendar/calendar_source: full official sessions including next T+1 day;
      planning_snapshot(previous,day,first,book,model,costs): Snapshot containing
        only prior-close observations, actual holdings/cash, no pending proceeds;
      market_context(day,symbols,phase): explicit OPEN/CLOSE marks, memberships,
        OPEN rules only, and sources;
      require_action_coverage(symbols,years): throw for unrequested/failed input;
      record_actions(book,day): register held record-day rights before close.
    All inputs must be frozen by the caller before invoking this function.
    """
    days=tuple(valuation_days);calendar=list(provider.calendar)
    if not run_id or not model or len(days)<4 or tuple(sorted(set(days)))!=days:
        raise ValueError('named full run with initial mark plus3build sessions required')
    if any(type(d) is not date for d in days) or days[0] not in calendar:
        raise ValueError('explicit official valuation dates required')
    start=calendar.index(days[0])
    if calendar[start:start+len(days)]!=list(days) or start+len(days)>=len(calendar):
        raise ValueError('contiguous official marks and terminal T+1 metadata required')
    # The initial mark is explicitly pure cash: no historical stock right can
    # exist before the first possible fill. A Dec31 cash origin must not force
    # an irrelevant prior-year action query for a Jan2 initial execution.
    years=sorted({d.year for d in days[1:]})
    book=PretaxReplayAccount(capital,'EXPLICIT_RESEARCH_CAPITAL_NOT_USER_BALANCE',sessions=calendar,
        calendar_source=provider.calendar_source,fee_source=costs.source)
    engine=ResearchTWAPExecutor(book,calendar,days[1],costs,provider.calendar_source)
    tax_bridge=CashTaxAssessmentBridge(calendar=calendar[start:],calendar_source=provider.calendar_source,
        protocol_source='RESEARCH_TWAP_SAME_PROTOCOL_PERSONAL_CASH_TAX_ASSESSMENT_BOUNDS_V1')
    state=PlannerState();plans=[];marks=[];outcomes=[];failure=None;last_close_context=None

    def context(day,symbols,phase):
        c=provider.market_context(day,set(symbols),phase)
        if type(c) is not MarketContext or c.day!=day or len(c.members)!=500 or not c.members<=c.eligible_symbols:
            raise ValueError('invalid explicit market context')
        if not set(symbols)<=set(c.prices):raise ValueError('unvalued position/order cannot disappear')
        return c

    def close_mark(c):
        nonlocal last_close_context
        provider.record_actions(book,book.day)
        book.close()
        tax_bridge.settle_book_eod(book,noncash_coverage_source=provider.calendar_source+'|REQUIRED_CATALOG_COVERAGE_RETAINED')
        point=engine.close_check(c.prices,c.members,valuation_source=c.valuation_source,
            membership_source=c.membership_source,eligible_symbols=c.eligible_symbols,
            unlisted_valuation_source=c.unlisted_valuation_source)
        if point['accounting_residual']!=0:raise ValueError('cash journal reconciliation failed')
        marks.append(deepcopy(point))
        last_close_context=c

    try:
        book.begin(days[0]);close_mark(context(days[0],set(),'CLOSE'))
        for previous,day in zip(days,days[1:]):
            # This call and plan construction complete BEFORE any execution
            # context for this day is requested from the provider.
            snapshot=provider.planning_snapshot(previous,day,days[1],book,model,costs)
            if snapshot.decision_at.date()!=previous or snapshot.execute_on!=day:
                raise ValueError('provider planning date mismatch')
            if snapshot.cash!=book.cash or snapshot.pending_order_count!=0:
                raise ValueError('planner cash must be the actual prior closed book')
            if (snapshot.calendar!=tuple(calendar) or snapshot.first_session!=days[1]
                    or snapshot.nav!=marks[-1]['nav']
                    or snapshot.receivable_value!=sum(book.receivables.values(),D(0))):
                raise ValueError('planner calendar/NAV/receivables must match actual closed state')
            if {s.symbol for s in snapshot.securities if s.member}!=set(last_close_context.members):
                raise ValueError('planner must use the previous marked membership')
            expected={s:book.quantity(s) for s in book.lots if book.quantity(s)}
            actual={h.symbol:h.quantity for h in snapshot.holdings if h.quantity}
            if actual!=expected:raise ValueError('planner cannot invent completed fills')
            for h in snapshot.holdings:
                listed=sum(lot.quantity for lot in book.lots.get(h.symbol,[]) if lot.listed_on<=previous)
                known_next_sellable=sum(lot.quantity for lot in book.lots.get(h.symbol,[])
                    if lot.listed_on<=previous and lot.sellable_on<=day)
                if (h.listed_quantity!=listed or h.sellable_quantity>known_next_sellable
                        or D(h.mark_price)!=D(last_close_context.prices[h.symbol])):
                    raise ValueError('planner holding marks/listing/sellability disagree with journal')
            plan=plan_orders(snapshot,state);plans.append(asdict(plan));state=plan.next_state
            requested=set(expected)|{o.symbol for o in plan.orders}
            # Missing query coverage blocks the whole run, never deletes a
            # troublesome name and substitutes the next-ranked future survivor.
            provider.require_action_coverage(requested,years)
            book.begin(day)
            # Previously recorded entitlements may create new shares in a name
            # whose original shares were sold in full before this ex session.
            # Value all actual opening assets without changing yesterday's plan.
            requested|={s for s in book.lots if book.quantity(s)}
            provider.require_action_coverage(requested,years)
            opening=context(day,requested,'OPEN')
            for order in plan.orders:
                if order.symbol not in opening.rules:
                    raise ValueError('missing/unsupported dated rule: '+order.symbol)
                result=engine.execute(FixedOrder(**order.executor_fields()),opening.rules[order.symbol],
                    opening.prices[order.symbol],opening.prices,opening.members,
                    valuation_source=opening.valuation_source,membership_source=opening.membership_source,
                    eligible_symbols=opening.eligible_symbols,unlisted_valuation_source=opening.unlisted_valuation_source)
                outcomes.append(result)
            held={s for s in book.lots if book.quantity(s)}
            closing=context(day,held,'CLOSE');close_mark(closing)
    except Exception as exc:
        failure=dict(error_type=type(exc).__name__,reason=str(exc),book_day=book.day,
            book_closed=book.closed,last_completed_valuation=marks[-1]['date'] if marks else None)
    book.verify_history()
    if failure is None and not getattr(provider,'execution_coverage_complete',True):
        failure=dict(error_type='IncompleteRawTWAPEvidence',reason='Unknown required raw-minute execution/risk window; holdings retained without a fabricated fill',
            book_day=book.day,book_closed=book.closed,last_completed_valuation=marks[-1]['date'] if marks else None)
    complete=failure is None and len(marks)==len(days)
    return dict(run_id=run_id,model=model,cost_scenario=costs.name,initial_scenario_capital=D(capital),
        declared_valuation_days=days,build_sessions=days[1:4],status='COMPLETE_DIAGNOSTIC_ONLY' if complete else 'INCOMPLETE',
        nav_basis=book.basis,net_NAV_contract_compatible=False,economic_target_accepted=False,
        distribution_tax_liability_included=False,source_and_execution_verified=False,
        full_period_pretax_return=marks[-1]['nav']/D(capital)-1 if complete else None,
        completed_marks=marks,plans=plans,outcomes=outcomes,failure=failure,
        account_event_ledger=deepcopy(book.history),account_event_ledger_hash=book.history[-1]['hash'],
        closing_quantity={s:book.quantity(s) for s in book.lots if book.quantity(s)},
        cash_dividend_tax=tax_bridge.report(actual_cash=book.cash,pretax_nav=marks[-1]['nav'] if marks else D(capital)),
        provider_execution_events=deepcopy(getattr(provider,'events',[])),
        risk_violation_mark_count=sum(bool(m['violations']) for m in marks),
        incomplete_prefix_is_not_a_full_window=not complete)
