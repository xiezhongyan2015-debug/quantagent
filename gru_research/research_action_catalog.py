"""Exact-source research catalog allowing proven same-day distribution components.

The production catalog assumes one event per security/ex-date. A company may
implement annual and interim distributions together. Distinct vendor rows stay
distinct and require primary confirmation before this research-only extension
accepts that case; ordinary event rules are reused unchanged.
"""
from collections import defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'research_v3/src'))
from scenario_action_catalog import (ACTION_FIELDS, ALL_STATUS, SUCCESS,
    IncompleteActionEvidence, blank, iso, distribution_terms, vendor_action)

COMPOUND_PRIMARY = {
    ('sz.301308', '2026-06-02'): {
        'path': ROOT / 'gru_research/trading_data/official_sources/301308_2026_half_year_official.bin',
        'sha256': 'd217cdf138618f290fab4e07f71e90eef54c84d52142c38f8593ed726cdf5184',
        'url': 'https://disc.static.szse.cn/disc/disk03/finalpage/2026-08-11/35b248b6-6b97-4fe1-92f5-15a610d4e19c.PDF',
        'page_zero_based': 47, 'cash_per_ten': '9.907442',
        'component_cash_per_ten': ['3.467605', '6.439837'],
        'record_on': '2026-06-01', 'pay_on': '2026-06-02',
        'historical_arrival_verified': False,
    },
}


def verified_compound_rows(identity, rows):
    proof = COMPOUND_PRIMARY.get(identity)
    if proof is None:
        return False
    path = proof['path']
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != proof['sha256']:
        raise ValueError('compound distribution primary source missing or changed')
    from pypdf import PdfReader
    text = ''.join(PdfReader(path).pages[proof['page_zero_based']].extract_text().split())
    if not all(s in text for s in ['共同构成', '9.907442', '股权登记日', '2026-065']):
        raise ValueError('compound distribution primary source does not prove declared terms')
    distinct = {tuple(row.get(f, '') for f in ACTION_FIELDS): row for row in rows}
    cash = []
    for row in distinct.values():
        gross, shares = distribution_terms(row)
        if shares or row['dividRegistDate'] != proof['record_on'] or row['dividPayDate'] != proof['pay_on']:
            raise ValueError('compound distribution component dates/noncash terms not confirmed')
        cash.append(gross * 10)
    if sorted(cash) != sorted(map(Decimal, proof['component_cash_per_ten'])):
        raise ValueError('compound distribution components differ from primary-confirmed cash allocation')
    if sum(cash, Decimal(0)) != Decimal(proof['cash_per_ten']):
        raise ValueError('compound distribution component total differs from primary source')
    return True


class ResearchActionCatalog:
    def __init__(self, rows, coverage, *, source_identity, query_bytes_by_sha256):
        if not re.fullmatch(r'[0-9a-f]{64}', source_identity):
            raise ValueError('frozen catalog source identity required')
        rows = list(rows); coverage = list(coverage)
        self.source_identity = source_identity
        self.coverage = {}; self.records = {}; self.by_record = {}; self.query_reconciliation = []
        queries = {}; original_indices = defaultdict(set); same_day = defaultdict(list)
        for cov in coverage:
            key = (str(cov['symbol']), int(cov['operating_year']))
            if key in self.coverage or cov['year_type'] != 'operate' or cov['query_status'] not in ALL_STATUS:
                raise ValueError('invalid or duplicate query coverage identity')
            self.coverage[key] = cov['query_status']
            if cov['query_status'] not in SUCCESS:
                continue
            h = cov.get('source_sha256'); data = query_bytes_by_sha256.get(h)
            if type(data) is not bytes or hashlib.sha256(data).hexdigest() != h:
                raise ValueError('successful query raw bytes missing or hash mismatch')
            q = json.loads(data); p = q.get('params', {}); fields = q.get('fields'); raw = q.get('rows')
            if (q.get('api') != 'query_dividend_data' or str(q.get('error_code')) != '0'
                    or str(p.get('code', '')).replace('.', '') != key[0] or str(p.get('year')) != str(key[1])
                    or p.get('yearType') != 'operate' or not isinstance(fields, list)
                    or len(fields) != len(ACTION_FIELDS) or set(fields) != set(ACTION_FIELDS)
                    or not isinstance(raw, list)):
                raise ValueError('complete original action query identity/schema required')
            if len(raw) != Decimal(str(cov.get('provider_rows'))) or bool(raw) != (cov['query_status'] == 'provider_success_nonempty'):
                raise ValueError('coverage count/empty state differs from raw response')
            originals = []
            for values in raw:
                if not isinstance(values, list) or len(values) != len(fields) or any(type(v) is not str for v in values):
                    raise ValueError('malformed original dividend row')
                item = dict(zip(fields, values))
                if item['code'].replace('.', '') != key[0] or iso(item['dividOperateDate'], 'raw ex date').year != key[1]:
                    raise ValueError('raw returned action identity/year mismatch')
                originals.append(item)
            queries[h] = (key, originals)
        seen = set()
        for row in rows:
            symbol = row['code'].replace('.', ''); ex = iso(row['dividOperateDate'], 'ex date')
            identity = tuple(row.get(f, '') for f in ACTION_FIELDS)
            if identity in seen:
                raise ValueError('normalized distinct action duplicated')
            seen.add(identity)
            provenance = json.loads(row.get('source_provenance_json', '[]'))
            if not provenance:
                raise ValueError('original source provenance required')
            cited = []
            for src in provenance:
                h = src.get('source_sha256'); query = queries.get(h); index = src.get('source_row')
                if query is None or query[0] != (symbol, ex.year) or type(index) is not int or not 0 <= index < len(query[1]):
                    raise ValueError('original query source index/identity mismatch')
                original = query[1][index]
                if any(not blank(original.get(f)) and original[f] != row.get(f, '') for f in ACTION_FIELDS):
                    raise ValueError('original nonempty action component changed in normalization')
                cited.append(original); original_indices[h].add(index)
            if any(not blank(row.get(f)) and not any(original.get(f) == row[f] for original in cited) for f in ACTION_FIELDS):
                raise ValueError('normalized action field has no exact original support')
            same_day[(row['code'], str(ex))].append(row)
            record = iso(row.get('dividRegistDate'), 'record date', False)
            self.records.setdefault(symbol, []).append(row)
            if record:
                self.by_record.setdefault(record, []).append(row)
        for ident, events in same_day.items():
            if len(events) > 1 and not verified_compound_rows(ident, events):
                raise ValueError('distinct same-day actions require primary compound confirmation')
        for h, (key, originals) in queries.items():
            if original_indices[h] != set(range(len(originals))):
                raise ValueError('original action rows omitted in normalization')
            self.query_reconciliation.append({'symbol': key[0], 'operating_year': key[1], 'source_sha256': h,
                'original_rows': len(originals), 'all_distinct_rows_matched': True,
                'all_original_row_indices_matched': True})

    def require_coverage(self, symbol, years):
        for year in years:
            state = self.coverage.get((symbol, int(year)), 'not_requested_yet')
            if state not in SUCCESS:
                raise IncompleteActionEvidence(f'{symbol} {year}: {state}; unknown is not zero distribution')
        for row in self.records.get(symbol, []):
            if iso(row['dividOperateDate'], 'ex date').year in years and blank(row.get('dividRegistDate')):
                raise IncompleteActionEvidence(symbol + ' action has unknown record date')

    def announce_held_record_actions(self, account, day):
        if account.day != day or account.closed:
            raise ValueError('record actions need current open book')
        announced = []
        for row in self.by_record.get(day, []):
            symbol = row['code'].replace('.', '')
            if not account.quantity(symbol):
                continue
            action = vendor_action(row)
            account.announce(action); announced.append(action.action_id)
        return announced
