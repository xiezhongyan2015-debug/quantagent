"""Research-only 2025/2026 extension of the old ordinary-rule scenarios.

All metadata/availability/calendar/special-regime/IPO gates remain the frozen
contract. Main-board ST bands change on the official 2026-07-06 effective date.
Vendor flags/reference are retrospective assumptions, never verified arrival.
"""
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'research_v3/src'))
import dev_rule_scenarios as old
from dev_rule_scenarios import CalendarEvidence, SecurityMetadata, RuleInputSource, ScenarioRuleResult
from next_session_execution import DatedRule

ST_CHANGE = date(2026, 7, 6)
OFFICIAL_RULE_SOURCES = {
    'sse_2026': 'https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml',
    'sse_2026_change_summary': 'https://star.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml',
    'szse_2026': 'https://investor.szse.cn/lawrules/rule/trade/t20260424_620190.html',
    'szse_2026_full': 'https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf',
    'szse_2026_warning_guidance': 'https://www.szse.cn/lawrules/service/member/t20260630_621404.html',
}


def _common(symbol, metadata, cutoff, target_day, mode):
    errors, assumptions, sources = old._common(symbol, metadata, cutoff, target_day, mode)
    if type(target_day) is date and date(2025, 1, 1) <= target_day <= date(2026, 12, 31):
        errors = [e for e in errors if e != 'OUTSIDE_2021_2023']
        sources.append('OFFICIAL_RESEARCH_DATED_RULE_EXTENSION:' + '|'.join(OFFICIAL_RULE_SOURCES.values()))
    return errors, assumptions, sources


def dated_limit_rate(board, day, vendor_is_st):
    if type(day) is not date or not date(2025, 1, 1) <= day <= date(2026, 12, 31):
        raise ValueError('research dated bands bounded to 2025--2026')
    if vendor_is_st not in ('0', '1') or type(vendor_is_st) is not str or board not in old.BOARD_PREFIXES:
        raise ValueError('explicit supported board and daily risk-warning flag required')
    if board in ('STAR', 'CHINEXT'):
        return Decimal('.20')
    return Decimal('.05') if vendor_is_st == '1' and day < ST_CHANGE else Decimal('.10')


def prior_grid(symbol, asof, security_metadata, source, *, mode='STRICT'):
    if (type(asof) is not datetime or asof.tzinfo is None or asof.utcoffset() is None
            or asof.astimezone(old.CN).time() < time(15)):
        return ScenarioRuleResult(unsupported_reasons=('PREVIOUS_CLOSE_AWARE_ASOF_REQUIRED',))
    if type(security_metadata) is not SecurityMetadata or old._calendar_errors(security_metadata.calendar):
        return ScenarioRuleResult(unsupported_reasons=('VALID_SECURITY_AND_CALENDAR_EVIDENCE_REQUIRED',))
    ds = security_metadata.calendar.sessions
    day = asof.astimezone(old.CN).date()
    if day not in ds or ds.index(day) + 1 == len(ds):
        return ScenarioRuleResult(unsupported_reasons=('NEXT_OFFICIAL_SESSION_NOT_IN_CALENDAR',))
    errors, assumptions, sources = _common(symbol, security_metadata, asof, ds[ds.index(day) + 1], mode)
    if not old._text(source): errors.append('GRID_SOURCE_REQUIRED')
    if errors:
        return ScenarioRuleResult(unsupported_reasons=tuple(errors), unverified_assumptions=tuple(assumptions), sources=tuple(sources))
    grid = old._grid(security_metadata.board, asof, old._stamp(source, assumptions))
    grid.validate(asof)
    return ScenarioRuleResult(grid=grid, unverified_assumptions=tuple(assumptions), sources=tuple(sources + [source]))


def execution_rule(symbol, day, raw_preclose, vendor_is_st, vendor_trade_status,
                   security_metadata, members, source, *, mode='STRICT'):
    if not old._day(day) or type(security_metadata) is not SecurityMetadata or old._calendar_errors(security_metadata.calendar):
        return ScenarioRuleResult(unsupported_reasons=('VALID_DAY_SECURITY_AND_CALENDAR_EVIDENCE_REQUIRED',))
    ds = security_metadata.calendar.sessions
    if day not in ds or ds.index(day) == 0:
        return ScenarioRuleResult(unsupported_reasons=('PREVIOUS_OFFICIAL_SESSION_NOT_IN_CALENDAR',))
    cutoff = datetime.combine(ds[ds.index(day)-1], time(15), old.CN)
    errors, assumptions, sources = _common(symbol, security_metadata, cutoff, day, mode)
    if type(vendor_is_st) is not str or vendor_is_st not in ('0', '1'):
        errors.append('VENDOR_IS_ST_UNKNOWN_EXPECT_EXACT_STRING_0_OR_1')
    if type(vendor_trade_status) is not str or vendor_trade_status not in ('0', '1'):
        errors.append('VENDOR_TRADE_STATUS_UNKNOWN_EXPECT_EXACT_STRING_0_OR_1')
    if type(members) not in (set, frozenset) or any(not old._symbol(s) for s in members):
        errors.append('EXPLICIT_DATED_MEMBER_SET_REQUIRED')
    elif symbol in members and security_metadata.eligible is not True:
        errors.append('MEMBER_CANNOT_BE_OUTSIDE_ELIGIBLE_POOL')
    if (type(source) is not RuleInputSource or source.symbol != symbol or source.day != day or source.membership_on != day
            or any(not old._text(v) for v in (source.source_id, source.reference_price_source, source.status_source, source.membership_source))
            or source.reference_price_kind not in old.REFERENCE_KINDS):
        errors.append('DATED_REFERENCE_STATUS_MEMBERSHIP_SOURCES_REQUIRED')
    else:
        sources.extend((source.source_id, source.reference_price_source, source.status_source, source.membership_source))
        if source.reference_price_kind == 'VENDOR_PREVCLOSE_REFERENCE_SCENARIO':
            assumptions.append('vendor preclose assumed to be exchange reference including ex-rights/ex-dividend adjustment')
    try:
        if type(raw_preclose) not in (Decimal, str, int): raise ValueError('type')
        reference = Decimal(raw_preclose)
        if not reference.is_finite() or reference <= 0 or reference % old.TICK != 0: raise ValueError('reference')
    except (ValueError, InvalidOperation, TypeError):
        errors.append('POSITIVE_CENT_GRID_REFERENCE_PRICE_REQUIRED'); reference = None
    floor = ceiling = None
    if reference is not None and type(vendor_is_st) is str and vendor_is_st in ('0', '1'):
        try: rate = dated_limit_rate(security_metadata.board, day, vendor_is_st)
        except ValueError: rate = None; errors.append('OUTSIDE_RESEARCH_DATED_LIMIT_SCOPE')
        if rate is not None:
            floor = (reference * (1-rate)).quantize(old.TICK, rounding=ROUND_HALF_UP)
            ceiling = (reference * (1+rate)).quantize(old.TICK, rounding=ROUND_HALF_UP)
            if reference <= Decimal('.10') or floor <= 0 or reference-floor < old.TICK or ceiling-reference < old.TICK:
                errors.append('LOW_PRICE_TICK_EDGE_UNSUPPORTED_REQUIRES_DATED_EXTERNAL_RULE')
    assumptions.append('vendor daily status and reference modeled available at 09:15; true arrival and opening status unverified')
    if errors:
        return ScenarioRuleResult(unsupported_reasons=tuple(errors), unverified_assumptions=tuple(assumptions), sources=tuple(sources))
    at = datetime.combine(day, time(9, 15), old.CN)
    grid = old._grid(security_metadata.board, at, old._stamp(source.source_id, assumptions))
    rule = DatedRule(day=day, source=grid.source, available_at=at, tick=grid.tick, floor=floor, ceiling=ceiling,
        buy_min=grid.buy_min, buy_step=grid.buy_step, sell_min=grid.sell_min, sell_step=grid.sell_step,
        max_limit_quantity=grid.max_limit_quantity, allow_final_odd_sell=grid.allow_final_odd_sell,
        eligible=security_metadata.eligible, csi500=symbol in members, tradable_at_open=vendor_trade_status == '1')
    rule.validate()
    return ScenarioRuleResult(grid=grid, rule=rule, unverified_assumptions=tuple(assumptions), sources=tuple(sources))
