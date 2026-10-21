"""Actual-data base provider for the GRU adapter and research cash/T+1 replay.

No dummy model score columns are created. Prior-close market observations and
actual book lots feed the unchanged planner; GRUTradingProviderAdapter supplies
frozen scores separately. Rules/actions remain explicitly sourced scenarios.
"""
from datetime import date, datetime, time
from pathlib import Path
import re
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
sys.path.insert(0, str(ROOT / 'research_v3/src'))
import pandas as pd
from causal_cash_book import D
from cash_replay_data import RAW_FIELDS, prior_close_observations
from frozen_pretax_provider import FrozenPretaxProvider, numeric_flag, finite_decimal
from prior_day_order_planner import Security, Holding, Snapshot, FeeReserve, CN
from pretax_portfolio_replay import MarketContext
from dev_rule_scenarios import CalendarEvidence, RuleInputSource, SCENARIO
from research_rule_scenarios import prior_grid, execution_rule

MARKET_ONLY = 'NO_MODEL_SCORE_MARKET_ONLY_BASE'
RAW_LINEAGE_FIELDS = {'code','adjustflag','turn','pctChg','source_sha256','source_row','source_path','cross_source_check_status'}


class ResearchMarketProvider(FrozenPretaxProvider):
    def __init__(self, *, raw, members, official_calendar, lifecycle, catalog,
                 protocol_sha256, source_identity, calendar_source):
        if set(raw) - set(RAW_FIELDS) - RAW_LINEAGE_FIELDS - {'cross_source_price_conflict'}:
            raise ValueError('raw data whitelist excludes model/label/evaluator fields')
        if not set(RAW_FIELDS + ['cross_source_price_conflict']) <= set(raw):
            raise ValueError('complete actual raw daily/status/reference schema required')
        if not re.fullmatch(r'[0-9a-f]{64}', protocol_sha256) or not re.fullmatch(r'sha256:[0-9a-f]{64}', source_identity):
            raise ValueError('separately frozen research protocol/source identity required')
        self.calendar = list(official_calendar)
        if (not self.calendar or any(type(d) is not date for d in self.calendar)
                or self.calendar != sorted(set(self.calendar)) or not calendar_source):
            raise ValueError('complete named dated calendar required')
        if raw.duplicated(['date','symbol']).any() or members.duplicated(['date','symbol']).any():
            raise ValueError('unique raw/member keys required')
        if raw.date.dt.tz is not None or members.date.dt.tz is not None:
            raise ValueError('local trading date columns must be naive normalized dates')
        self.calendar_source = calendar_source
        self.calendar_evidence = CalendarEvidence(tuple(self.calendar), calendar_source, self.calendar[0], self.calendar[-1])
        self.source = source_identity; self.protocol = protocol_sha256; self.catalog = catalog
        self.raw = {d.date(): g.set_index('symbol') for d,g in raw.groupby('date')}
        self.members = {d.date(): frozenset(g.symbol) for d,g in members.groupby('date')}
        if any(len(pool) != 500 for d,pool in self.members.items() if d.year in (2025,2026)):
            raise ValueError('true dated CSI500 member count required; never fill a missing member')
        self.lifecycle = lifecycle.set_index('symbol', verify_integrity=True)
        observations = []
        calendar = pd.DatetimeIndex(self.calendar)
        for symbol,group in raw.groupby('symbol'):
            clean = group.copy()
            clean.loc[clean.cross_source_price_conflict, ['close','volume']] = float('nan')
            observations.append(prior_close_observations(clean, calendar))
        self.market = pd.concat(observations, ignore_index=True).set_index(['date','symbol'], verify_integrity=True)
        self.rule_assumptions = set(); self.unsupported_prior = []

    def planning_snapshot(self, previous, day, first, book, model, costs):
        if model != MARKET_ONLY:
            raise ValueError('base provider has no model scores; bind explicit frozen GRU score adapter')
        if previous not in self.members:
            raise ValueError('missing true historical members on previous decision date')
        members = self.members[previous]
        at = datetime.combine(previous, time(15,1), CN)
        held = {s for s in book.lots if book.quantity(s)}
        rows = []; holdings = []
        for symbol in sorted(set(members) | held):
            key = (pd.Timestamp(previous), symbol)
            row = self.market.loc[key] if key in self.market.index else None
            if row is None and symbol in held:
                raise ValueError('missing prior held valuation cannot delete the position')
            metadata = self._metadata(symbol, previous, day, members)
            result = prior_grid(symbol, at, metadata, self.source+'|PRIOR_RULE_SCENARIO', mode=SCENARIO)
            self.rule_assumptions.update(result.unverified_assumptions)
            if not result.supported:
                self.unsupported_prior.append({'symbol':symbol,'day':str(previous),'reasons':result.unsupported_reasons})
            close = finite_decimal(row.prior_raw_close) if row is not None else None
            adv = finite_decimal(row.adv20_shares) if row is not None else None
            can_submit = numeric_flag(row.prior_tradestatus)=='1' if row is not None else None
            rows.append(Security(symbol, symbol in members, None, close, adv, 20 if adv is not None else 0,
                can_submit, result.grid, previous, at, self.source+'|PRIOR_CLOSE_ONLY_SOURCE_ARRIVAL_ASSUMED'))
            if symbol in held:
                actual = self._raw(previous, symbol)
                listed = sum(l.quantity for l in book.lots[symbol] if l.listed_on <= previous)
                sellable = sum(l.quantity for l in book.lots[symbol] if l.listed_on <= previous and l.sellable_on <= day)
                holdings.append(Holding(symbol, book.quantity(symbol), listed, sellable, D(actual.close), at,
                    self.source+'|ACTUAL_RESEARCH_CASH_LOTS'))
        receivable = sum(book.receivables.values(), D(0))
        nav = book.cash + receivable + sum((h.quantity*h.mark_price for h in holdings), D(0))
        # Keep the original conservative upper reserves. Actual fees are the
        # separately dated research schedule, never these upper-bound sums.
        rate = D(costs.commission_rate) + D('.00002')
        fees = FeeReserve(rate, D(costs.minimum_commission), rate+D('.001'), D(costs.minimum_commission), at,
            costs.source+'|FROZEN_CONSERVATIVE_FEE_UPPER_RESERVE_DISTRIBUTION_TAX_SEPARATE')
        return Snapshot(at, day, tuple(self.calendar), self.calendar_source, first, tuple(rows), tuple(holdings),
            book.cash, nav, receivable, at, self.source+'|ACTUAL_CLOSED_RESEARCH_BOOK', 0, fees)

    def market_context(self, day, symbols, phase):
        if phase not in ('OPEN','CLOSE'):
            raise ValueError('explicit raw phase required')
        if day not in self.members:
            raise ValueError('missing true dated member set')
        members = self.members[day]; prices = {}; rules = {}
        previous = self.calendar[self.calendar.index(day)-1] if self.calendar.index(day) else None
        for symbol in sorted(symbols):
            row = self._raw(day,symbol)
            prices[symbol] = D(row.open if phase=='OPEN' else row.close)
            if phase == 'OPEN':
                metadata = self._metadata(symbol, previous, day, members)
                reference = RuleInputSource(self.source, symbol, day, self.source+'|VENDOR_PREVCLOSE',
                    'VENDOR_PREVCLOSE_REFERENCE_SCENARIO', self.source+'|DAILY_FLAGS_ARRIVAL_NOT_VERIFIED',
                    self.source+'|TRUE_HISTORICAL_IFIND_MEMBERS', day)
                result = execution_rule(symbol, day, D(row.preclose), numeric_flag(row.isST), numeric_flag(row.tradestatus),
                    metadata, members, reference, mode=SCENARIO)
                self.rule_assumptions.update(result.unverified_assumptions)
                if not result.supported:
                    raise ValueError('unsupported dated execution rule '+symbol+':'+','.join(result.unsupported_reasons))
                rules[symbol] = result.rule
        return MarketContext(day, prices, members, members, self.source+'|UNADJUSTED_'+phase,
            self.source+'|TRUE_HISTORICAL_MEMBERS_TIGHTER_500_ONLY', rules,
            self.source+'|UNLISTED_CLAIM_AT_UNDERLYING_SPOT_SCENARIO')
