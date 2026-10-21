"""GRU score/raw-TWAP adapter for the existing cash/lot/T+1 replay contract.

No production or frozen training file is modified. The adapter replaces only
scores and execution-window reference prices; the supplied base provider owns
dated market rules, action coverage and actual account observation construction.
It does not extend the old executor's date/fee certification or manufacture
corporate actions. A research driver must bind its separately verified extended
executor/cost schedule before attempting 2025--2026 replay.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import Decimal
import functools
import hashlib
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'gru_research' / '.deps'))
sys.path.insert(0, str(ROOT / 'research_v3' / 'src'))
import numpy as np
import pandas as pd
from prior_day_order_planner import Snapshot, Security
from pretax_portfolio_replay import MarketContext

CN = ZoneInfo('Asia/Shanghai')
DAILY_TWAP = 'DAILY_CLOSE_SIGNAL_NEXT_SESSION_0931_0936_RAW_OPEN_TWAP'
LEGACY_OPEN = 'FROZEN_DAILY_OPEN_SCENARIO_DISTINCT_FROM_PLAN_TWAP'
ARRIVAL_ASSUMPTION = 'REUSE_FROZEN_PROVIDER_PRIOR_CLOSE_AVAILABILITY_ASSUMPTION_NOT_RECEIPT_PROOF'
SCORE_FIELDS = frozenset({'symbol', 'signal_time', 'score', 'model_version', 'available_at',
    'earliest_execution_time', 'percentile', 'data_quality_flag'})


class IncompleteTradingEvidence(ValueError):
    """A required sourced observation is absent; caller retains the book."""


def cash_symbol(value):
    """Explicit canonical translation, never guess exchange from price history."""
    value = str(value)
    if re.fullmatch(r'(sh|sz)\d{6}', value):
        return value
    m = re.fullmatch(r'(\d{6})\.(SH|SZ)', value)
    if not m:
        raise ValueError('Unsupported security identity: ' + value)
    return m.group(2).lower() + m.group(1)


def minute_symbol(value):
    value = cash_symbol(value)
    return value[2:] + '.' + value[:2].upper()


def utc_ns(value):
    if isinstance(value, (int, np.integer)):
        return int(value)
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError('Timezone or integer UTC nanoseconds required')
    return int(stamp.tz_convert('UTC').value)


@dataclass(frozen=True)
class TWAPQuote:
    symbol: str
    raw_price: Decimal | None
    complete: bool
    observed_positive_volume: bool
    all_five_minutes_flat: bool | None
    reason: str | None


@dataclass(frozen=True)
class TWAPWindow:
    signal_time: int
    execution_start: int
    execution_end: int
    quotes: dict[str, TWAPQuote]
    source: str
    price_kind: str = 'MEAN_FIVE_COMPLETE_RAW_MINUTE_OPENS_NO_VWAP_NO_ADJUSTED_PRICE'


class FrozenMinuteTWAPSource:
    """Read only the already audited raw-window columns, never training labels."""
    def __init__(self, artifacts=ROOT / 'gru_research' / 'artifacts'):
        self.artifacts = Path(artifacts)
        self.metadata = json.loads((self.artifacts / 'minute_feature_metadata.json').read_text())
        if self.metadata['timestamp_format'] != 'utc_ns' or self.metadata['timezone'] != str(CN):
            raise ValueError('Unexpected frozen minute timestamp contract')
        dates = json.loads((self.artifacts / 'minute_calendar.json').read_text())['dates']
        self.calendar = [date.fromisoformat(d) for d in dates]
        tail = json.loads((self.artifacts / 'minute_tail_calendar_evidence.json').read_text())
        nxt = pd.Timestamp(tail['next_known_execution_time'], tz='UTC').tz_convert(CN).date()
        if nxt > self.calendar[-1]:
            self.calendar.append(nxt)
        self.calendar_source = 'minute_calendar.json|historical_iFinD_membership_sessions|minute_tail_calendar_evidence.json'

    @functools.lru_cache(maxsize=12)
    def _day(self, day):
        path = self.artifacts / 'minute_hourly_raw' / f'{day.isoformat()}.parquet'
        if not path.exists():
            return None, str(path)
        columns = ['symbol', 'slot', 'window_start_time', 'window_end_time', 'window_complete',
                   'window_raw_price', 'window_volume', 'window_tradable', 'window_all_flat']
        f = pd.read_parquet(path, columns=columns)
        if f.duplicated(['symbol', 'slot']).any():
            raise IncompleteTradingEvidence('Conflicting audited raw-window keys: ' + str(path))
        return f.set_index(['symbol', 'slot']), str(path)

    def window(self, signal_time, symbols):
        ns = utc_ns(signal_time)
        local = pd.Timestamp(ns, tz='UTC').tz_convert(CN)
        day = local.date()
        if day not in self.calendar or local.second or local.microsecond or local.nanosecond:
            raise ValueError('Signal must lie exactly on a verified standard-hour boundary')
        if local.time().replace(tzinfo=None) == time(10, 30):
            slot, start = 1, time(10, 31)
        elif local.time().replace(tzinfo=None) == time(11, 30):
            slot, start = 2, time(13, 1)
        elif local.time().replace(tzinfo=None) == time(14):
            slot, start = 3, time(14, 1)
        elif local.time().replace(tzinfo=None) == time(15):
            i = self.calendar.index(day)
            if i + 1 == len(self.calendar):
                raise IncompleteTradingEvidence('No verified next trading session')
            day, slot, start = self.calendar[i + 1], 0, time(9, 31)
        else:
            raise ValueError('Only four standard hourly signals are supported')
        first = utc_ns(datetime.combine(day, start, CN))
        end = first + 5 * 60 * 10**9
        frame, source = self._day(day)
        quotes = {}
        for supplied in symbols:
            symbol = cash_symbol(supplied)
            key = (minute_symbol(symbol), slot)
            if frame is None or key not in frame.index:
                quotes[symbol] = TWAPQuote(symbol, None, False, False, None, 'MISSING_RAW_FIVE_MINUTE_WINDOW')
                continue
            r = frame.loc[key]
            if int(r.window_start_time) != first or int(r.window_end_time) != end:
                raise IncompleteTradingEvidence('Execution timestamp disagrees with audited source: ' + symbol)
            price = r.window_raw_price
            positive_volume = bool(r.window_tradable) and np.isfinite(r.window_volume) and r.window_volume > 0
            complete = bool(r.window_complete) and positive_volume and np.isfinite(price) and price > 0
            reason = None if complete else ('COMPLETE_WINDOW_WITH_NO_OBSERVED_TRADE'
                if bool(r.window_complete) and np.isfinite(r.window_volume) and r.window_volume == 0
                else 'INCOMPLETE_CORRUPT_RAW_WINDOW')
            quotes[symbol] = TWAPQuote(symbol, Decimal(str(price)) if complete else None,
                complete, positive_volume, bool(r.window_all_flat),
                reason)
        return TWAPWindow(ns, first, end, quotes, source)


class GRUTradingProviderAdapter:
    """Replace forecast scores while reusing the base's actual cash/lot snapshot.

    The trade policy remains the frozen daily close decision and next-session
    attempt. All four hourly quotes are available independently via
    ``hourly_execution_window``; intraday scores are evaluated separately and do
    not silently trigger extra orders or bypass T+1.
    """
    def __init__(self, base_provider, scores, *, model_version, planning_model,
                 score_source, arrival_policy, execution_mode=DAILY_TWAP,
                 minute_source=None):
        if execution_mode not in (DAILY_TWAP, LEGACY_OPEN):
            raise ValueError('Explicit supported execution scenario required')
        if arrival_policy != ARRIVAL_ASSUMPTION:
            raise ValueError('The frozen provider arrival assumption must remain explicit')
        if not model_version or not planning_model or not score_source:
            raise ValueError('Frozen model, planning inputs and score provenance required')
        f = pd.read_parquet(scores) if isinstance(scores, (str, Path)) else scores.copy()
        if not {'symbol', 'signal_time', 'score', 'model_version'} <= set(f) or set(f) - SCORE_FIELDS:
            raise ValueError('Score-only whitelist required; no future labels/returns/execution fields')
        if not f.model_version.eq(model_version).all():
            raise ValueError('Mixed/unfrozen score model versions')
        f['symbol'] = f.symbol.map(cash_symbol)
        f['signal_time'] = f.signal_time.map(utc_ns)
        if f.duplicated(['signal_time', 'symbol']).any():
            raise ValueError('Duplicate GRU score keys')
        if 'available_at' in f:
            f['available_at'] = f.available_at.map(utc_ns)
            if (f.available_at < f.signal_time).any():
                raise ValueError('Score cannot be available before its input hour completes')
        if np.isinf(pd.to_numeric(f.score, errors='raise').to_numpy()).any():
            raise ValueError('Infinite score')
        self.base = base_provider
        self.scores = f.set_index(['signal_time', 'symbol'])
        self.model_version, self.planning_model = model_version, planning_model
        self.score_source, self.arrival_policy = score_source, arrival_policy
        self.execution_mode = execution_mode
        self.minute = minute_source or FrozenMinuteTWAPSource()
        self.calendar = list(base_provider.calendar)
        self.calendar_source = base_provider.calendar_source
        self.events = []

    def planning_snapshot(self, previous, day, first, book, model, costs):
        if model != self.model_version:
            raise ValueError('Requested model differs from frozen GRU version')
        original = self.base.planning_snapshot(previous, day, first, book, self.planning_model, costs)
        if type(original) is not Snapshot:
            raise ValueError('Base provider must return the original frozen Snapshot contract')
        expected = utc_ns(datetime.combine(previous, time(15), CN))
        cutoff = utc_ns(original.decision_at)
        if expected > cutoff:
            raise IncompleteTradingEvidence('GRU close score unavailable at the frozen decision')
        securities, missing = [], []
        for security in original.securities:
            if type(security) is not Security:
                raise ValueError('Original Security contract required')
            key = (expected, cash_symbol(security.symbol))
            score = None
            if security.member and key in self.scores.index:
                r = self.scores.loc[key]
                if 'available_at' in r and int(r.available_at) > cutoff:
                    raise IncompleteTradingEvidence('GRU score arrived after decision: ' + security.symbol)
                if pd.notna(r.score):
                    score = Decimal(str(r.score))
            if security.member and score is None:
                missing.append(security.symbol)
            securities.append(replace(security, score=score,
                source=security.source + '|GRU=' + self.model_version + '|score=' + self.score_source + '|' + self.arrival_policy))
        # Every security and holding survives; cash/NAV/lots/fee reserves and
        # market knowledge cutoffs are inherited without recomputing anything.
        self.events.append({'kind': 'GRU_SCORE_SNAPSHOT', 'decision_at': original.decision_at.isoformat(),
            'signal_time': expected, 'missing_score_members': missing, 'all_member_keys_retained': True})
        return replace(original, securities=tuple(securities))

    def hourly_execution_window(self, signal_time, symbols):
        return self.minute.window(signal_time, symbols)

    def market_context(self, day, symbols, phase):
        context = self.base.market_context(day, symbols, phase)
        if type(context) is not MarketContext:
            raise ValueError('Original MarketContext required')
        if phase != 'OPEN' or self.execution_mode == LEGACY_OPEN:
            return context
        i = self.calendar.index(day)
        if not i:
            raise IncompleteTradingEvidence('Previous session required for next-session TWAP')
        signal = datetime.combine(self.calendar[i - 1], time(15), CN)
        window = self.hourly_execution_window(signal, symbols)
        if pd.Timestamp(window.execution_start, tz='UTC').tz_convert(CN).date() != day:
            raise IncompleteTradingEvidence('Base and minute next-session calendars disagree')
        prices, rules = dict(context.prices), dict(context.rules)
        blocked = []
        for supplied in symbols:
            s = cash_symbol(supplied)
            quote = window.quotes[s]
            if quote.complete:
                prices[s] = quote.raw_price
            else:
                # Preserve the base's explicitly sourced raw valuation mark for
                # an unfilled holding/order; it is NEVER an execution substitute.
                # The unchanged executor sees tradable=False and creates no fill.
                if s not in prices or s not in rules:
                    raise IncompleteTradingEvidence('No sourced valuation/rule for unfilled position: ' + s)
                confirmed_no_trade = quote.reason == 'COMPLETE_WINDOW_WITH_NO_OBSERVED_TRADE' or not rules[s].tradable_at_open
                rules[s] = replace(rules[s], tradable_at_open=False)
                blocked.append({'symbol': s, 'reason': quote.reason,
                    'valuation_only_raw_mark_source': context.valuation_source,
                    'fill_price': None, 'holding_retained': True,
                    'confirmed_no_trade_from_sourced_window_or_daily_status':confirmed_no_trade,
                    'execution_evidence_missing':not confirmed_no_trade})
        self.events.append({'kind': 'NEXT_SESSION_RAW_TWAP_CONTEXT', 'day': day.isoformat(),
            'window_start_utc_ns': window.execution_start, 'window_end_utc_ns': window.execution_end,
            'source': window.source, 'no_fill_windows': blocked})
        return replace(context, prices=prices, rules=rules,
            valuation_source=context.valuation_source + '|0931_0936_RAW_OPEN_TWAP=' + window.source +
                '|MISSING_WINDOWS_BASE_RAW_MARKS_VALUATION_ONLY_FILL_PROHIBITED')

    @property
    def execution_coverage_complete(self):
        return not any(b.get('execution_evidence_missing') for event in self.events
            for b in event.get('no_fill_windows',[]))

    def require_action_coverage(self, symbols, years):
        return self.base.require_action_coverage(symbols, years)

    def record_actions(self, book, day):
        return self.base.record_actions(book, day)

    def compatibility(self, years):
        years = sorted(set(map(int, years)))
        return {'model_version': self.model_version, 'execution_mode': self.execution_mode,
            'signal_schedule_for_trading': 'prior-session 15:00 only; four hourly prediction diagnostics retained',
            'portfolio_cash_lots_fees_rules': 'unchanged supplied base provider and cash executor',
            'old_executor_fee_date_guard_supports_requested_years': all(2017 <= y <= 2023 for y in years),
            'old_rule_scenario_provider_supports_requested_years': all(2021 <= y <= 2023 for y in years),
            'requires_separately_frozen_verified_extended_executor_for_2025_2026': any(y > 2023 for y in years),
            'net_return_claim': False, 'nav_basis_of_original_replay': 'AFTER_TRADING_COSTS_BEFORE_DISTRIBUTION_TAX',
            'historical_arrival': self.arrival_policy}


def inspect_local_sources(root=ROOT):
    """Inventory evidence; no market replay, fitting, downloading or mutation."""
    root = Path(root)
    paths = [
        'acquired_data/cash_replay_inputs/complete_raw_v3/execution_raw.parquet',
        'acquired_data/current_prediction_20260920/normalized_v3/daily_native.parquet',
        'acquired_data/latest_preparation_20260916_full/normalized/current_raw_daily.parquet',
        'acquired_data/latest_preparation_20260916_full/normalized/current_risk_liquidity.parquet',
    ]
    sources = []
    for relative in paths:
        p = root / relative
        if not p.exists():
            sources.append({'path': relative, 'exists': False})
            continue
        f = pd.read_parquet(p)
        entry = {'path': relative, 'exists': True, 'rows': len(f), 'columns': list(f),
                 'symbols': int(f.symbol.nunique()) if 'symbol' in f else None}
        if 'date' in f:
            entry.update(start=str(f.date.min()), end=str(f.date.max()), sessions=int(f.date.nunique()))
        entry['explicit_trade_status'] = 'tradestatus' in f
        entry['explicit_risk_warning_status'] = 'isST' in f
        entry['explicit_ex_day_reference_price'] = 'preclose' in f
        sources.append(entry)
    coverage_path = root / 'acquired_data/data_round2/normalized/corporate_actions_DEV_planned_query_coverage.csv'
    coverage = pd.read_csv(coverage_path)
    counts = [{'year': int(y), 'query_status': s, 'queries': int(n)}
              for (y, s), n in coverage.groupby(['operating_year', 'query_status']).size().items()]
    protocol_path = root / 'research_v3/config/round2/pretax_diagnostic_protocol_v3.json'
    protocol = json.loads(protocol_path.read_text())
    hashes = {}
    for relative in ['research_v3/src/pretax_portfolio_replay.py', 'research_v3/src/prior_day_order_planner.py',
        'research_v3/src/next_session_execution.py', 'research_v3/src/pretax_replay_account.py',
        'research_v3/src/causal_cash_book.py', 'research_v3/src/dev_rule_scenarios.py',
        'research_v3/config/round2/pretax_diagnostic_protocol_v3.json']:
        hashes[relative] = hashlib.sha256((root / relative).read_bytes()).hexdigest()
    return {'status': 'ADAPTER_READY_DATA_EVIDENCE_INCOMPLETE_FOR_2025_2026_REPLAY',
        'raw_daily_sources': sources, 'corporate_action_query_coverage': counts,
        'action_coverage_2025_2026_successful_queries': int(coverage.operating_year.isin([2025, 2026]).sum()),
        'frozen_protocol_id': protocol['protocol_id'], 'frozen_code_hashes': hashes,
        'available': ['Historical iFinD daily memberships and calendar',
            'Audited four-hour raw OHLCV and real five-minute raw-open TWAP for source-covered names through2026-09-23',
            'Original cash/lots/T+1/record-rights/cash-payment/fee journal and frozen allocation/order policy'],
        'missing': ['No complete successful per-symbol2025/2026 corporate-action operate-year query coverage found locally',
            'No complete2025/2026 dated trade-status/risk-warning/ex-day reference/special-regime panel found locally',
            'Minute package contains historical members; required prices for holdings after pool exit are not guaranteed',
            'Original executor fees stop2023 and ordinary-rule scenario stops2023; official verified extension required',
            'Original diagnostic journal omits distribution tax; no final net-NAV claim'],
        'execution_alignment': {'trading': DAILY_TWAP, 'prediction': 'four standard hourly signal times',
            'no_daily_open_substitution_for_twap': True, 'blocked_windows_keep_actual_holdings': True}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['inspect'])
    parser.parse_args()
    print(json.dumps(inspect_local_sources(), ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
