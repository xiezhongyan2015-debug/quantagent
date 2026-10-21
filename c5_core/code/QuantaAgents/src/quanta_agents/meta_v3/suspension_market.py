"""Execution-only halt facts and explicitly estimated marks; never signal data.

No network, quote repair, stock selection, fee changes or coverage inference.
The controller verifies the issuer document and supplies a pinned review. File
hashes bind that review; they are not independent authenticity certification.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path


VERSION = 'documented-halt-last-close-v1'
POLICY = {'version': VERSION,
    'halt_valuation': 'last_pre_halt_raw_close_estimate',
    'halt_orders': 'reject_all',
    'resumption_capacity': 'require_previous_calendar_session_observed_volume_and_name',
    'corporate_action_during_halt': 'reject_unimplemented_revaluation'}
FIELDS = {'symbol', 'first_halted', 'first_resumed', 'source_document_date',
          'document_path', 'document_sha256', 'review_path', 'review_sha256'}


def need(value, message):
    if not value:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def decimal_price(row, field):
    if not row:
        return None
    texts = row.get('raw_price_text')
    value = texts.get(field) if type(texts) is dict else None
    if value is None:
        value = row.get(field)
    if value is None or isinstance(value, bool):
        return None
    try:
        value = Decimal(str(value))
    except Exception:
        return None
    return value if value.is_finite() and value > 0 else None


class SuspensionMarket:
    def __init__(self, schedule, codes, calendar, corporate_actions=()):
        need(type(schedule) is dict and set(schedule) == {'policy', 'episodes'},
             'explicit suspension policy and episodes required')
        need(schedule['policy'] == POLICY, 'unknown suspension valuation/execution policy')
        need(type(schedule['episodes']) is list and len(schedule['episodes']) <= 64,
             'bounded suspension episodes required')
        self.schedule = deepcopy(schedule)
        self.calendar = list(calendar)
        need(self.calendar == sorted(set(calendar)) and len(calendar) >= 3, 'halt calendar order')
        self.by_key = {}
        for episode in self.schedule['episodes']:
            need(type(episode) is dict and set(episode) == FIELDS, 'exact suspension fact fields required')
            need(episode['symbol'] in codes, 'suspension outside frozen universe')
            for field in ('first_halted', 'first_resumed', 'source_document_date'):
                value = episode[field]
                need(type(value) is str and date.fromisoformat(value).isoformat() == value, 'ISO source dates required')
            start, resumed = episode['first_halted'], episode['first_resumed']
            need(start in calendar[1:] and resumed in calendar and start < resumed, 'halt and resumption calendar coverage')
            for field in ('document_path', 'review_path'):
                need(type(episode[field]) is str and Path(episode[field]).is_absolute(), 'absolute controller source path required')
            for field in ('document_sha256', 'review_sha256'):
                h = episode[field]
                need(type(h) is str and len(h) == 64 and all(c in '0123456789abcdef' for c in h), 'source SHA256 required')
            for action in corporate_actions:
                need(not (action['symbol'] == episode['symbol'] and start <= action['ex_date'] < resumed),
                     'corporate action during halt needs explicit revaluation; stale unadjusted price forbidden')
            for day in calendar:
                if start <= day < resumed:
                    key = (day, episode['symbol'])
                    need(key not in self.by_key, 'overlapping suspension episodes')
                    self.by_key[key] = episode
        self.sha256 = digest(self.schedule)

    def verify_sources(self):
        for episode in self.schedule['episodes']:
            payloads = {}
            for kind, limit in [('document', 16 * 1024**2), ('review', 2 * 1024**2)]:
                path = Path(episode[kind + '_path'])
                need(path.is_file() and path.resolve() == path.absolute() and not path.is_symlink(), 'missing or redirected halt source')
                need(path.stat().st_size <= limit, 'halt source byte limit')
                with path.open('rb') as stream:
                    raw = stream.read(limit + 1)
                need(len(raw) <= limit and hashlib.sha256(raw).hexdigest() == episode[kind + '_sha256'], 'halt source hash mismatch')
                payloads[kind] = raw
            need(payloads['document'].startswith(b'%PDF-'), 'reviewed issuer PDF required')
            review = json.loads(payloads['review'])
            need(review.get('kind') == 'suspension_event_source_review' and review.get('facts_verified') is True,
                 'controller-reviewed suspension facts required')
            need(all(review.get(k) == episode[k] for k in ('symbol', 'first_halted', 'first_resumed',
                 'source_document_date', 'document_sha256')), 'halt review facts do not match frozen episode')
            need(type(review.get('reviewed_pages')) is list and review['reviewed_pages'] and
                 all(type(x) is int and x > 0 for x in review['reviewed_pages']), 'reviewed document page references required')

    def validate_rows(self, rows):
        for (day, symbol), episode in self.by_key.items():
            row = rows.get((day, symbol))
            need(row is not None, 'retain every requested halted stock-day row')
            need(not any(decimal_price(row, field) is not None for field in ('raw_open', 'raw_close', 'raw_high', 'raw_low')),
                 'observed trading price contradicts declared full-session halt')
            volume = row.get('volume')
            if volume is not None:
                try:
                    v = Decimal(str(volume))
                except Exception:
                    v = Decimal('NaN')
                need(not v.is_finite() or v == 0, 'observed trading volume contradicts full-session halt')
            anchor = self.calendar[self.calendar.index(episode['first_halted']) - 1]
            price = decimal_price(rows.get((anchor, symbol)), 'raw_close')
            need(price is not None,
                 'documented halt lacks last preceding session raw close')
            need(price % Decimal('.01') == 0, 'pre-halt raw close must respect admitted cent tick')

    def halted(self, day, symbol):
        return (day, symbol) in self.by_key

    def mark(self, rows, day, symbol, quantity, at):
        episode = self.by_key.get((day, symbol))
        if episode is None:
            return None
        anchor_index = self.calendar.index(episode['first_halted']) - 1
        anchor = self.calendar[anchor_index]
        price = decimal_price(rows.get((anchor, symbol)), 'raw_close')
        need(price is not None, 'halt valuation requires verified pre-halt close')
        evidence = {'kind': 'estimated_halted_position_mark', 'symbol': symbol, 'valuation_date': day,
            'last_observed_quote_date': anchor, 'last_observed_raw_close': str(price),
            'stale_calendar_sessions': self.calendar.index(day) - anchor_index,
            'held_quantity': quantity, 'locked_share_value_estimate': format(price * quantity, '.2f'),
            'tradable': False, 'observed_current_price': False, 'sellability_not_assumed': True,
            'historical_arrival_verified': False, 'schedule_sha256': self.sha256,
            'document_sha256': episode['document_sha256'], 'review_sha256': episode['review_sha256']}
        # This is a current accounting estimate, explicitly distinct from the
        # old quote date. It is never inserted into the executable quote table.
        mark = {'raw_price': format(price, '.2f'), 'effective_at': at, 'available_at': at,
            'source': {'ref': 'simulated-halt-valuation://' + symbol + '/' + day,
                       'sha256': digest(evidence), 'verified': True, 'evidence_type': 'simulated',
                       'assumption_ref': VERSION, 'assumption_sha256': self.sha256}}
        return mark, evidence
