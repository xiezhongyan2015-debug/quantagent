"""Versioned primary facts, never a vendor-row rewrite or trading-policy change.

Public historical publication evidence is retrospective. It does not certify
actual historical source arrival, a live feed, broker rounding, or noncash FIFO.
Explicit printed virtual ratios affect reference-price evidence only; account
cash and share entitlements keep their actual per-entitled-share amounts.
"""
from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal as D, ROUND_HALF_UP, localcontext
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parent.parent
HERE=ROOT/'gru_research'
sys.path.insert(0,str(HERE/'.deps'))
sys.path.insert(0,str(ROOT/'research_v3/src'))
import pandas as pd
from pypdf import PdfReader
from scenario_action_catalog import ACTION_FIELDS, distribution_terms, vendor_action
from pretax_replay_account import PretaxDistribution

SUPPLEMENT=HERE/'trading_data/ex_reference_supplement_20261001'
RECEIPT=SUPPLEMENT/'supplemental_receipt_v1.json'
OVERLAY=SUPPLEMENT/'primary_factual_overlay_v1.json'
RECEIPT_SHA='4857cca0a1d34b6c3192263cb1bcc37d1fbde0450a03c1a6ee56d2d9cd4ee190'
OVERLAY_SHA='8f7a48cbcecd61b1d8c983b971548232910ed1d3d028ca9c535fd47afd7cad62'
NORMALIZED=HERE/'trading_data/full_2025_2026/normalized'
CN=ZoneInfo('Asia/Shanghai')
GAP_KEY=('sh601966','2025-07-10')
EXPECTED_KEYS=frozenset([('sh688498','2026-05-18'),('sz300953','2025-06-03'),
    ('sh688018','2025-06-04'),('sz300953','2026-05-28'),('sh688608','2025-07-02'),
    ('sh688005','2025-03-06'),('sz002432','2026-06-03'),('sh600096','2025-05-22'),GAP_KEY])
EX_FIELDS=('symbol','ex_date','distinct_source_components','raw_preclose_never_changed',
    'official_quote_reference_certified','previous_raw_close','gross_cash_per_share',
    'ordinary_share_ratio','vendor_ex_preclose','ordinary_formula_reference','difference','status')


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def raw_ex_identity(row):
    values={k:row[k] for k in EX_FIELDS}
    values['ex_date']=str(values['ex_date'])[:10]
    return values


def compact(text):
    return ''.join(text.split()).translate(str.maketrans({'（':'(', '）':')', '／':'÷', '/':'÷'})).replace('元','')


class PrimaryActionSupplementV3:
    """Verify all frozen source bytes and recompute each case without scores.

    Optional relocation maps let an unchanged file be read from an archive or
    an isolated negative test. They do not bypass any expected SHA or identity.
    """
    def __init__(self, *, path_relocations=None):
        self.relocations={str(Path(k).resolve()):Path(v).resolve() for k,v in (path_relocations or {}).items()}
        self.verify()

    def locate(self,path):
        path=Path(path).resolve()
        return self.relocations.get(str(path),path)

    def checked(self,path,expected):
        actual=self.locate(path)
        if not actual.is_file() or sha(actual)!=expected:
            raise ValueError('primary V3 sealed source missing or changed: '+str(path))
        return actual

    def verify(self):
        self.verified_files=0
        receipt_path=self.checked(RECEIPT,RECEIPT_SHA)
        overlay_path=self.checked(OVERLAY,OVERLAY_SHA)
        receipt=json.loads(receipt_path.read_text());overlay=json.loads(overlay_path.read_text())
        if (len(receipt['sealed_supplemental_files'])!=79 or receipt['scores_or_model_holdings_read']
                or receipt['original_vendor109_rows_overwritten']!=0
                or overlay['role']!='INDEPENDENT_FACTUAL_EVIDENCE_NOT_ACTIVE_V2_ACCOUNT_OR_RULE_PATCH'):
            raise ValueError('fixed primary supplement receipt identity changed')
        for p,h in receipt['sealed_supplemental_files'].items():self.checked(p,h)
        before=json.loads(self.locate(SUPPLEMENT/'frozen_input_guard_before.json').read_text())
        after=json.loads(self.locate(SUPPLEMENT/'frozen_input_guard_after.json').read_text())
        if before!=after:raise ValueError('original V1/V2 source preservation unproved')
        for p,h in before.items():self.checked(p,h)
        originals=pd.read_parquet(self.locate(NORMALIZED/'ex_reference_checks.parquet')).to_dict('records')
        exrows={(r['symbol'],str(r['ex_date'])[:10]):r for r in originals}
        if len(exrows)!=len(originals):raise ValueError('duplicate original ex-reference identity')
        actions=pd.read_parquet(self.locate(NORMALIZED/'corporate_actions.parquet')).to_dict('records')
        cases=overlay['cases'];keys=[(r['symbol'],r['ex_date']) for r in cases]
        if len(keys)!=len(set(keys)) or set(keys)!=EXPECTED_KEYS:
            raise ValueError('duplicate/missing/new primary case identity')
        if len(overlay['unknown_historical_cases'])!=100:
            raise ValueError('unknown source rows must remain explicit')
        self.cases={};self.base_action_rows={};self.vendor_action_ids={}
        for case in cases:
            key=(case['symbol'],case['ex_date']);original=exrows[key]
            if raw_ex_identity(original)!=raw_ex_identity(case['original_frozen_ex_reference']):
                raise ValueError('original vendor reference row differs from sealed primary identity')
            proof=case['official_primary_source'];pdf=self.checked(proof['path'],proof['sha256'])
            pages=PdfReader(pdf).pages
            text='\n'.join(pages[i].extract_text() for i in case['evidence_pages_zero_based'])
            if any(token not in text for token in case['evidence_terms']):
                raise ValueError('primary PDF does not prove exact case terms')
            expression=case['printed_formula']
            if expression is not None and compact(expression) not in compact(text):
                raise ValueError('literal as-published formula absent from exact primary pages')
            published=date.fromisoformat(case['published_date']);record=date.fromisoformat(case['record_date'])
            ex=date.fromisoformat(case['ex_date'])
            if not published<=record<ex or date.fromisoformat(case['signed_date'])>record:
                raise ValueError('primary implementation publication chronology invalid')
            query=proof['query'];qpath=self.checked(query['path'],query['sha256'])
            q=json.loads(qpath.read_text())
            if 'result' in q:
                rows=[r for r in q['result'] if r['TITLE'].endswith('实施公告')]
                if len(rows)!=1 or rows[0]['SECURITY_CODE']!=case['symbol'][2:] or rows[0]['SSEDATE']!=str(published):
                    raise ValueError('official query symbol/publication date differs')
                if not proof['url'].endswith(rows[0]['URL']):raise ValueError('PDF official query path differs')
                added=rows[0].get('ADDDATE')
                source_stamp=datetime.fromisoformat(added).replace(tzinfo=CN) if added else datetime.combine(published,time.max,CN)
            else:
                rows=q.get('announcements') or []
                if len(rows)!=1 or rows[0]['secCode']!=case['symbol'][2:]:raise ValueError('official disclosure symbol differs')
                source_stamp=datetime.fromtimestamp(rows[0]['announcementTime']/1000,CN)
                if source_stamp.date()!=published or proof['url']!='https://static.cninfo.com.cn/'+rows[0]['adjunctUrl']:
                    raise ValueError('official disclosure timestamp/path differs')
            if source_stamp>datetime.combine(record,time(15),CN):
                raise ValueError('published timestamp after record close')
            selected=[r for r in actions if r['code'].replace('.','')==key[0] and r['dividOperateDate']==key[1]]
            expected=case['original_cash_action_records']
            if len(selected)!=len(expected) or selected!=expected:
                raise ValueError('original normalized action row identity changed')
            gross=sum((distribution_terms(r)[0] for r in selected),D(0))
            shares=sum((distribution_terms(r)[1] for r in selected),D(0))
            cash=D(case['actual_cash_per_entitled_share'])
            ratio=D(case['actual_capital_conversion_per_entitled_share'])+D(case['actual_bonus_per_entitled_share'])
            if shares!=ratio or (key!=GAP_KEY and gross!=cash):
                raise ValueError('entitled terms must not be replaced by virtual reference terms')
            for r in selected:
                if (r['dividRegistDate']!=str(record) or r['dividPayDate']!=(case['pay_date'] or '')):
                    raise ValueError('official/vendor event dates differ')
            t=D(case['total_share_base']);e=D(case['participating_share_base'])
            if t-D(case['excluded_treasury_shares'])!=e:raise ValueError('primary excluded-share formula mismatch')
            with localcontext() as context:
                context.prec=60
                reference=(D(original['previous_raw_close'])-D(case['printed_virtual_cash_per_all_original_share']))/(1+D(case['printed_virtual_share_ratio']))
                cent=reference.quantize(D('.01'),rounding=ROUND_HALF_UP)
                exact=(D(original['previous_raw_close'])-e*cash/t)/(1+e*ratio/t)
            if cent!=D(original['vendor_ex_preclose']):raise ValueError('as-published reference does not exactly match vendor cent')
            calc=case['as_published_expression_using_observed_previous_raw_close']
            if calc['reference_unrounded']!=str(reference) or calc['price_tick_rounding']!='ROUND_HALF_UP' or calc['reference_cent']!=str(cent):
                raise ValueError('sealed overlay published-reference arithmetic differs')
            if case['sharebase_alternatives_diagnostic_only']['exact_participating_basis_reference']!=str(exact):
                raise ValueError('exact-sharebase alternative diagnostic differs')
            if key==GAP_KEY:
                components=case['cash_components_per_entitled_share']
                if (components!=[{'period':'2024Q4','cash':'0.014'},{'period':'2025Q1','cash':'0.070'}]
                        or len(selected)!=1 or gross!=D('.070') or cash!=D('.084')
                        or '差异化分红送转：否' not in compact(text)):
                    raise ValueError('official missing cash component grouping/amount not proven')
            self.cases[key]=deepcopy(case);self.base_action_rows[key]=deepcopy(selected)
            self.vendor_action_ids[key]=tuple(vendor_action(r).action_id for r in selected)
        self.receipt_sha256=RECEIPT_SHA;self.overlay_sha256=OVERLAY_SHA
        self.source_identity=hashlib.sha256((RECEIPT_SHA+OVERLAY_SHA).encode()).hexdigest()
        self.verified_files=79;self.original_vendor_identity_changed=False
        return self

    def official_missing_cash_component(self):
        case=self.cases[GAP_KEY]
        identity=dict(authority='ISSUER_IMPLEMENTATION_PDF',version='PRIMARY_CASH_COMPONENT_V3',
            pdf_sha256=case['official_primary_source']['sha256'],symbol=GAP_KEY[0],period='2024Q4',
            cash_gross_per_share='0.014',record_on=case['record_date'],ex_on=case['ex_date'],pay_on=case['pay_date'])
        ident='PRIMARY_CASH_COMPONENT_V3:'+hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        source=dict(identity,receipt_sha256=RECEIPT_SHA,overlay_sha256=OVERLAY_SHA,
            original_vendor_component_cash_per_share='0.070',original_vendor_action_ids=self.vendor_action_ids[GAP_KEY],
            source_url=case['official_primary_source']['url'],published_on=case['published_date'],
            historical_publication_evidence_only=True,actual_historical_source_arrival_verified=False,
            account_component_not_merged=True,source_kind='OFFICIAL_PRIMARY_NOT_VENDOR_ROW_OR_QUERY_INDEX')
        result=PretaxDistribution(ident,GAP_KEY[0],date.fromisoformat(case['published_date']),
            date.fromisoformat(case['record_date']),date.fromisoformat(case['ex_date']),date.fromisoformat(case['pay_date']),
            None,None,D('.014'),D(0),'LISTED_ONLY',
            'PRIMARY_IMPLEMENTED_RECORD_DATE_CASH_COMPONENT_RETROSPECTIVE_RESEARCH',
            json.dumps(source,sort_keys=True),
            'PAY_AT_CLOSE_LIST_AT_OPEN_SELL_AT_EXPLICIT_DATE_RESEARCH_SCENARIO')
        result.validate();return result


def validate_component_uniqueness(components, original_actions):
    """Never add the same official component twice or double-count a vendor one."""
    ids=set();keys=set();original_ids={r.action_id for r in original_actions}
    original_terms={(r.symbol,r.record_on,r.ex_on,r.pay_on,r.cash_gross_per_share,r.new_share_ratio) for r in original_actions}
    for action in components:
        key=(action.symbol,action.record_on,action.ex_on,action.pay_on,action.cash_gross_per_share,action.new_share_ratio)
        if action.action_id in ids or key in keys or action.action_id in original_ids or key in original_terms:
            raise ValueError('duplicate official/source cash component')
        ids.add(action.action_id);keys.add(key)


class PrimarySupplementCatalogV3:
    def __init__(self, original, supplement):
        supplement.verify()
        self.original=original;self.supplement=supplement
        # The vendor catalog and its exact all-row reconciliation remain intact.
        self.coverage=original.coverage;self.records=original.records;self.by_record=original.by_record
        self.query_reconciliation=original.query_reconciliation
        self.source_identity=hashlib.sha256((original.source_identity+supplement.source_identity).encode()).hexdigest()
        target=[r for r in original.records.get(GAP_KEY[0],[]) if r['dividOperateDate']==GAP_KEY[1]]
        if target!=supplement.base_action_rows[GAP_KEY]:raise ValueError('base catalog differs from verified original action rows')
        originals=[vendor_action(r) for r in target]
        self.official_components=(supplement.official_missing_cash_component(),)
        validate_component_uniqueness(self.official_components,originals)
        self.official_by_record={self.official_components[0].record_on:self.official_components}
        self.original_gap_action_ids=tuple(a.action_id for a in originals)
        self.installed_supplement_component_ids=frozenset(a.action_id for a in self.official_components)

    def require_coverage(self,symbol,years):
        # The runner verifies all bytes before/after every scenario. Re-hashing
        # the whole source per security/day would repeat the unchanged V2 audit
        # hundreds of thousands of times; construction has verified this object.
        years=tuple(years)
        if self.supplement.verified_files!=79:raise ValueError('primary facts were not verified')
        self.original.require_coverage(symbol,years)
        if symbol==GAP_KEY[0] and 2025 in years and len(self.installed_supplement_component_ids)!=1:
            raise ValueError('required official cash component not installed')

    def announce_held_record_actions(self,account,day):
        if account.day!=day or account.closed:raise ValueError('record actions need current open book')
        extra=[a for a in self.official_by_record.get(day,()) if account.quantity(a.symbol)]
        if extra:self.supplement.verify()
        for action in extra:
            if action.action_id in account.actions:raise ValueError('official component record announcement duplicated')
        announced=self.original.announce_held_record_actions(account,day)
        for action in extra:
            account.announce(action);announced.append(action.action_id)
        return announced

    def reference_resolution(self,row,result):
        key=(row['symbol'],str(row['ex_date'])[:10])
        if key not in self.supplement.cases:return None
        case=self.supplement.cases[key]
        if raw_ex_identity(row)!=raw_ex_identity(case['original_frozen_ex_reference']):
            raise ValueError('reference gate original source identity differs')
        if key==GAP_KEY:
            expected=self.supplement.official_missing_cash_component().action_id
            if expected not in self.installed_supplement_component_ids:
                raise ValueError('reference gate missing official cash component')
            announced={e['payload']['action_id'] for e in result.get('account_event_ledger',[])
                if e['kind'] in ('ACTION_ANNOUNCED','PRETAX_ACTION_SCENARIO_ANNOUNCED')}
            target=set(self.original_gap_action_ids)|{expected}
            if announced & target and not target<=announced:
                raise ValueError('actual held-record cash rights missing one implemented component')
        return dict(symbol=key[0],ex_date=key[1],original_status=row['status'],
            supplemental_status=case['status'],original_vendor_reference_unmodified=True,
            printed_reference_cent=case['as_published_expression_using_observed_previous_raw_close']['reference_cent'],
            official_primary_pdf_sha256=case['official_primary_source']['sha256'],
            primary_overlay_sha256=OVERLAY_SHA,cash_component_catalog_installed=key==GAP_KEY,
            historical_publication_evidence_only=True,actual_historical_source_arrival_verified=False,
            noncash_fifo_or_official_broker_tax_rounding_verified=False)
