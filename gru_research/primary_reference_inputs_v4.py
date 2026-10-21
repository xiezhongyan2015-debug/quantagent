"""Append-only reviewed primary inputs, separate from instrument qualification.

Only original publicly listed implementation bytes and fixed source-only orders
are read. No scores, portfolio holdings, returns, fitting or strategy selection.
Cash/stock terms remain original ledger terms; virtual ratios prove references.
"""
from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal as D, ROUND_HALF_UP, localcontext
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from primary_action_supplement_v3 import (HERE, NORMALIZED, EX_FIELDS, sha,
    raw_ex_identity, PrimarySupplementCatalogV3)
from scenario_action_catalog import ACTION_FIELDS, distribution_terms, vendor_action
import pandas as pd
from pypdf import PdfReader

CN = ZoneInfo('Asia/Shanghai')
INPUT = HERE/'trading_data/ex_reference_supplement_20261001/unknown100_historical_cases.json'
INPUT_SHA = 'd049e857b4b47c487b8ffe6570ce053ec111a3ff1f6e243422c9d8906802727f'
FIRST = HERE/'trading_data/ex_reference_followup_20261001'
SECOND = HERE/'trading_data/ex_reference_followup_batch21_60_20261001'
REVIEW20 = HERE/'artifacts/experiment_acceptance_ex_reference_followup_001_020_review_20261001.json'
REVIEW20_SHA = 'a1fdf65d1e5da0b300d6be805e1a6372c194ef8098b39e1979f1b89410fc2cb9'
RECEIPT20_SHA = '41e67511a527cefd99e5fa9629ed46358f276d10aae05d3117fbea9d275c7712'
CORRECTIONS_SHA = '9bead26c7396917b93502f1f4a3b6174aa1e41cc3b72b36d6211bcd7f6405703'
RECEIPT40 = SECOND/'supplemental_receipt_batch21_60_v1.json'
RECEIPT40_SHA = 'a512c9197127b6771e0ba1f13dbef9f348373a10e203a26bffb3863592e60693'
OVERLAY40 = SECOND/'primary_factual_overlay_batch21_60_v1.json'
OVERLAY40_SHA = '7b0970e3228b3463ca79b8b4e17a5aeb65ddfa32bcffe84d48537f3e441057c2'
REVIEW40 = HERE/'artifacts/experiment_acceptance_ex_reference_followup_021_060_review_20261001.json'
REVIEW40_SHA = 'ce623ec07100872f3000f6bea22e5872cb8c7cbc061ff85c684f27933705d23e'
RECEIPT100 = FIRST/'receipt_061_100.json'
RECEIPT100_SHA = 'd4fd636d294a1f1b75ef183323dcc449097909aa85a327e6ed8630b9f89daaf0'
REVIEW100 = HERE/'artifacts/experiment_acceptance_ex_reference_followup_061_100_review_20261001.json'
REVIEW100_SHA = '0a7b1eb34a518306dc78f7d7c18031e91cd61dc4ddd867e8e15fbf29c0ede9a4'
CDR_POLICY = HERE/'trading_data/cdr_policy_scope_20261001/policy_scope_receipt_v1.json'
CDR_POLICY_SHA = '38dba6dd7a1bf74f34b6f2898072f2fb19da32146e866c91c8b118947a8dc224'
CDR_UNIVERSE_FILES = {
    HERE/'artifacts/dataset/symbols.json': 'ee2dbf9ea6c052b68167c644586bcf6384ba4dfa215f2a4c12b6dfac79fb6ba8',
    HERE/'trading_data/full_2025_2026/historical_union_symbols.json': '37829ceeb2a98052eb488a3a5826d49d2b61cf48564416157b7a3c0f1ddffa03',
}
PREFIX_PARSER=HERE.parent/'research_v3/src/distribution_prefix.py'
PREFIX_PARSER_SHA='4623c2e73bb874a0161c76d282bdbc513b1822f227c298f12c8542ebe7139bfa'
PREFIX_HISTORICAL_MANIFEST=HERE.parent/'research_v3/reports/pretax_chain_review_v3/input_manifest.json'
PREFIX_HISTORICAL_MANIFEST_SHA='655ef05dd1bf6b00cfb044f7feaa02e85f854a4a1c12648a990b0fc529b9c194'
NORMALIZED_SHA = {
    'ex_reference_checks.parquet': 'eaaef68a6dd2cfb838bed225428bab431eac99fc57db8a679e642b87ff955ba3',
    'corporate_actions.parquet': '583b6d9d474212cd7814568808b632c6c43b0e44b60d54c195f6daa8a7711144',
}


def compact(text):
    return ''.join(text.split()).translate(str.maketrans(
        {'（':'(', '）':')', '−':'-', '－':'-', '／':'÷', '/':'÷'})).replace('元','')


def signature_date_present(text, value):
    day = date.fromisoformat(value)
    digits = '零一二三四五六七八九'
    def chinese_number(number):
        if number < 10: return digits[number]
        return (digits[number//10] if number >= 20 else '')+'十'+(digits[number%10] if number%10 else '')
    normalized = compact(text).replace('〇','零').replace('○','零')
    arabic = re.compile(fr'{day.year}年0?{day.month}月0?{day.day}日')
    chinese = ''.join(digits[int(n)] for n in str(day.year))+'年'+chinese_number(day.month)+'月'+chinese_number(day.day)+'日'
    return bool(arabic.search(normalized)) or chinese in normalized


def require_review_case_roster(reviewed, fixed_order, first, last):
    """A batch PASS never substitutes for every original case/index identity."""
    if [r['source_only_order'] for r in reviewed]!=list(range(first,last+1)):
        raise ValueError('independent PASS review roster missing, duplicated or out of fixed order')
    for r in reviewed:
        original_index,original=fixed_order[r['source_only_order']-1]
        if (r['original_input_index'],r['symbol'],r['ex_date'])!=(original_index,original['symbol'],original['ex_date']):
            raise ValueError('independent PASS review original case/index identity differs')
    return {r['source_only_order']:r for r in reviewed}


def apply_signature_correction(fact, corrections, checked):
    """Copy a single metadata field; original fact and all dates stay immutable."""
    result = deepcopy(fact)
    matches = [c for c in corrections['corrections']
               if c['source_only_order'] == fact['source_only_order']]
    if len(matches) > 1:
        raise ValueError('duplicate append-only signature correction')
    if not matches:
        return result, None
    correction = matches[0]
    if (correction['symbol'], correction['ex_date'], correction['original_input_index']) != (
            fact['symbol'], fact['ex_date'], fact['original_input_index']):
        raise ValueError('signature correction original case identity differs')
    if correction['supersedes_field'] != 'publication.pdf_signature_date' or not correction['no_other_fields_overridden']:
        raise ValueError('only append-only signature metadata correction authorized')
    if correction['original_value'] != fact['publication']['pdf_signature_date']:
        raise ValueError('signature correction does not bind old field value')
    for field in ('original_fact', 'primary_pdf', 'text', 'signature_page_visual_review'):
        item = correction[field]
        checked(item['path'], item['sha256'])
    if correction['primary_pdf'] != fact['primary_document']['pdf']:
        raise ValueError('correction primary PDF differs from original fact')
    pdf = checked(correction['primary_pdf']['path'], correction['primary_pdf']['sha256'])
    text = PdfReader(pdf).pages[correction['signature_page_number']-1].extract_text() or ''
    if not signature_date_present(text, correction['corrected_value']):
        raise ValueError('corrected signature not on original final signature page')
    result['publication']['pdf_signature_date'] = correction['corrected_value']
    return result, deepcopy(correction)


class PrimaryReferenceInputsV4:
    def __init__(self, *, path_relocations=None):
        self.relocations = {str(Path(k).resolve()): Path(v).resolve()
                            for k, v in (path_relocations or {}).items()}
        self.verify()

    def locate(self, path):
        return self.relocations.get(str(Path(path).resolve()), Path(path).resolve())

    def checked(self, path, expected):
        found = self.locate(path)
        if not found.is_file() or sha(found) != expected:
            raise ValueError('V4 original source/review missing or changed: '+str(path))
        self.source_hashes[str(Path(path).resolve())] = expected
        return found

    def item(self, item):
        result=self.checked(item['path'], item['sha256'])
        if 'bytes' in item and result.stat().st_size!=item['bytes']:
            raise ValueError('V4 original source/review recorded size differs')
        return result

    def item_map(self, mapping):
        for path,item in mapping.items():
            if str(Path(path).resolve())!=str(Path(item['path']).resolve()):
                raise ValueError('independent review source-map path identity differs')
            self.item(item)

    def verify_review_lineage(self,case,reviewed):
        """Bind independently inspected original queries back to every raw row."""
        observed_actions=set()
        for proof in reviewed['original_action_source_identity_checks']:
            query=json.loads(self.item(proof['raw_query']).read_text())
            row=dict(zip(query['fields'],query['rows'][proof['source_row']]))
            if row!=proof['raw_fields']:
                raise ValueError('independent original action query row differs')
            observed_actions.add(tuple(row[f] for f in ACTION_FIELDS))
        original_actions={tuple(r[f] for f in ACTION_FIELDS) for r in case['original_action_rows']}
        if observed_actions!=original_actions:
            raise ValueError('independent original action component identities differ')
        observed_roles=set()
        for proof in reviewed['original_raw_daily_row_identity_checks']:
            query=json.loads(self.item(proof['raw_query']).read_text())
            row=dict(zip(query['fields'],query['rows'][proof['source_row']]))
            close=proof.get('raw_close_string',proof.get('raw_close'))
            preclose=proof.get('raw_preclose_string',proof.get('raw_preclose'))
            if (row['date']!=proof['date'] or row['code'].replace('.','')!=case['symbol']
                    or row['close']!=close or row['preclose']!=preclose):
                raise ValueError('independent original daily query row differs')
            role=proof['role'];observed_roles.add(role)
            if role=='preceding_close':
                if row['date']>=case['ex_date'] or D(close)!=D(case['original_reference']['previous_raw_close']):
                    raise ValueError('independent preceding raw close identity differs')
            elif role in ('ex_day_preclose','ex_preclose'):
                if row['date']!=case['ex_date'] or D(preclose)!=D(case['original_reference']['vendor_ex_preclose']):
                    raise ValueError('independent original raw ex preclose identity differs')
            else:
                raise ValueError('independent daily proof role differs')
        if 'preceding_close' not in observed_roles or not observed_roles.intersection(('ex_day_preclose','ex_preclose')):
            raise ValueError('independent original raw daily pair is incomplete')

    def query_proof(self, document, published, record, symbol):
        query = json.loads(self.item(document['official_query']).read_text())
        url = document['official_url']
        if not url.startswith(('https://static.cninfo.com.cn/', 'https://static.sse.com.cn/', 'https://star.sse.com.cn/')):
            raise ValueError('primary official host differs')
        if 'result' in query:
            rows = [r for r in query['result'] if r['SECURITY_CODE'] == symbol[2:]
                    and url.endswith(r['URL']) and r['SSEDATE'] == published]
            if len(rows) != 1:
                raise ValueError('SSE query exact code/path/publication differs')
            stamp = datetime.fromisoformat(rows[0]['ADDDATE']).replace(tzinfo=CN)
        else:
            rows = [r for r in query.get('announcements', [])
                    if r['secCode'] == symbol[2:] and url == 'https://static.cninfo.com.cn/'+r['adjunctUrl']]
            if len(rows) != 1:
                raise ValueError('CNINFO query exact code/path differs')
            stamp = datetime.fromtimestamp(rows[0]['announcementTime']/1000, CN)
            if stamp.date().isoformat() != published:
                raise ValueError('CNINFO publication date differs')
        if stamp > datetime.combine(date.fromisoformat(record), time(15), CN):
            raise ValueError('publication timestamp after record close')
        return stamp.isoformat()

    def normalized_case(self, *, order, symbol, ex_date, original_index, original,
                        cash, capital, bonus, record, pay, signed, published,
                        v_cash, v_ratio, formula, pdf, evidence_pages, review_sha,
                        correction=None, instrument='A_SHARE', source_kind='FIRST20'):
        expected_index, expected_case = self.fixed_order[order-1]
        if (expected_index, expected_case['symbol'], expected_case['ex_date']) != (original_index, symbol, ex_date):
            raise ValueError('source-only fixed order/index differs')
        if raw_ex_identity(original) != raw_ex_identity(expected_case):
            raise ValueError('original reference row not exactly bound to fixed source case')
        if not (date.fromisoformat(published) <= date.fromisoformat(record) < date.fromisoformat(ex_date) <= date.fromisoformat(pay)
                and date.fromisoformat(signed) <= date.fromisoformat(record)):
            raise ValueError('corrected primary event chronology differs')
        original_rows = [r for r in self.actions if r['code'].replace('.', '') == symbol
                         and r['dividOperateDate'] == ex_date]
        if not original_rows or any(r['dividRegistDate'] != record or r['dividPayDate'] != pay for r in original_rows):
            raise ValueError('original action date identity differs')
        gross = sum((distribution_terms(r)[0] for r in original_rows), D(0))
        shares = sum((distribution_terms(r)[1] for r in original_rows), D(0))
        if gross != D(cash) or shares != D(capital)+D(bonus):
            raise ValueError('actual entitled terms must equal complete original action text')
        pages = PdfReader(pdf).pages
        text = '\n'.join(pages[n-1].extract_text() or '' for n in evidence_pages)
        if v_cash not in compact(text) or (D(v_ratio) and v_ratio not in compact(text)):
            # Some SZ expressions explicitly print PER10 then divide by10.
            if format((D(v_cash)*10).normalize(),'f') not in compact(text) or (D(v_ratio) and format((D(v_ratio)*10).normalize(),'f') not in compact(text)):
                raise ValueError('printed virtual terms absent from bound primary pages: '+str(order))
        if not any(word in compact(text) for word in ('收盘价', '收盘价格', '参考价', '除息价')):
            raise ValueError('primary reference expression context missing')
        with localcontext() as context:
            context.prec = 60
            reference = (D(original['previous_raw_close'])-D(v_cash))/(1+D(v_ratio))
            cent = reference.quantize(D('.01'), rounding=ROUND_HALF_UP)
        if cent != D(original['vendor_ex_preclose']):
            raise ValueError('published primary formula does not exactly match unmodified vendor cent')
        key = (symbol, ex_date)
        if key in self.candidates:
            raise ValueError('duplicate primary case/component admission')
        self.candidates[key] = dict(source_only_order=order, original_input_index=original_index,
            symbol=symbol, ex_date=ex_date, original_reference=raw_ex_identity(original),
            original_action_ids=[vendor_action(r).action_id for r in original_rows],
            original_action_rows=deepcopy(original_rows), actual_cash_per_entitled_unit=cash,
            actual_capital_per_entitled_unit=capital, actual_bonus_per_entitled_unit=bonus,
            instrument_type=instrument, actual_unit='ONE_CDR' if instrument=='CDR' else 'ONE_A_SHARE',
            record_date=record, pay_date=pay, published_date=published,
            corrected_signature_date=signed, append_only_signature_correction=correction,
            signature_date_after_api_publication_date=date.fromisoformat(signed)>date.fromisoformat(published),
            printed_formula=formula, printed_virtual_cash=v_cash, printed_virtual_ratio=v_ratio,
            reference_unrounded=str(reference), reference_cent=str(cent),
            primary_pdf_sha256=sha(pdf), evidence_pages_1based=evidence_pages,
            independent_pass_review_sha256=review_sha, source_kind=source_kind,
            retrospective_publication_only=True, actual_historical_arrival_verified=False,
            tax_fee_noncash_or_broker_rounding_certified=False)
        if review_sha:
            self.cases[key] = self.candidates[key]

    def verify(self):
        self.source_hashes = {}
        self.cases = {}; self.candidates = {}
        # This running legacy parser was historically hashed but omitted from
        # the V3 comparison map. V4 adds its unchanged bytes explicitly.
        prefix_manifest=json.loads(self.checked(PREFIX_HISTORICAL_MANIFEST,PREFIX_HISTORICAL_MANIFEST_SHA).read_text())
        if prefix_manifest['sha256']['src/distribution_prefix.py']!=PREFIX_PARSER_SHA:
            raise ValueError('original full-text distribution parser historical identity differs')
        self.checked(PREFIX_PARSER,PREFIX_PARSER_SHA)
        raw = json.loads(self.checked(INPUT, INPUT_SHA).read_text())
        self.fixed_order = sorted(enumerate(raw), key=lambda x:(x[1]['ex_date'], x[1]['symbol']))
        for name, h in NORMALIZED_SHA.items():
            self.checked(NORMALIZED/name, h)
        original = pd.read_parquet(self.locate(NORMALIZED/'ex_reference_checks.parquet')).to_dict('records')
        self.exrows = {(r['symbol'], str(r['ex_date'])[:10]):r for r in original}
        self.actions = pd.read_parquet(self.locate(NORMALIZED/'corporate_actions.parquet')).to_dict('records')
        review20 = json.loads(self.checked(REVIEW20, REVIEW20_SHA).read_text())
        if review20['status'] != 'PASS_SOURCE_FACTS_AND_EXACT_PRINTED_CENT_AFTER_APPEND_ONLY_SIGNATURE_CORRECTIONS':
            raise ValueError('001--020 independent review is not PASS')
        self.checked(FIRST/'receipt_001_020.json', RECEIPT20_SHA)
        corrections = json.loads(self.checked(FIRST/'corrections_001_020_signature_dates.json', CORRECTIONS_SHA).read_text())
        if [c['source_only_order'] for c in corrections['corrections']] != [6,10,19]:
            raise ValueError('fixed append-only correction roster differs')
        for item in review20['actual_byte_guard']['map'].values():
            self.item(item)
        for path,h in review20['original_daily_and_action_query_actual_byte_guards'].items():
            self.checked(path,h)
        receipt20 = json.loads(self.locate(FIRST/'receipt_001_020.json').read_text())
        for item in receipt20['originals_and_rendered_evidence_inventory']:
            self.item(item)
        reviews = review20['independent_reviewed_cases']
        require_review_case_roster(reviews,self.fixed_order,1,20)
        for reviewed in reviews:
            fact_path = self.item(reviewed['fact'])
            original_fact = json.loads(fact_path.read_text())
            fact, correction = apply_signature_correction(original_fact, corrections, self.checked)
            if reviewed['independently_observed_pdf_signature_date'] != fact['publication']['pdf_signature_date']:
                raise ValueError('append-only corrected signature differs from independent review')
            doc=fact['primary_document']; pdf=self.item(doc['pdf'])
            if (reviewed['primary_pdf']!=doc['pdf'] or reviewed['dates']!=fact['dates']
                    or reviewed['actual_entitlement_components']!=fact['actual_entitlement_components']):
                raise ValueError('001--020 independent reviewed PDF/dates/actual components differ')
            saved=json.loads(self.item(doc['text']).read_text())
            fresh=[p.extract_text() or '' for p in PdfReader(pdf).pages]
            if fresh != [p['text'] for p in saved['pages']]:
                raise ValueError('fresh primary PDF text differs')
            if not signature_date_present(fresh[-1], fact['publication']['pdf_signature_date']):
                raise ValueError('effective signature not present on final primary page')
            self.query_proof(doc, fact['publication']['api_listed_publication_date'], fact['dates']['record_date'],fact['symbol'])
            components=fact['actual_entitlement_components']
            cash=sum((D(c.get('gross_CNY_per_eligible_share','0')) for c in components if c['component']=='cash_dividend'),D(0))
            capital=sum((D(c['new_shares_per_eligible_share']) for c in components if c['component']=='capital_reserve_transfer'),D(0))
            bonus=sum((D(c['new_shares_per_eligible_share']) for c in components if c['component']=='bonus_share'),D(0))
            terms=fact['reference_price_terms'];key=(fact['symbol'],fact['ex_date'])
            self.normalized_case(order=fact['source_only_order'],symbol=key[0],ex_date=key[1],
                original_index=fact['original_input_index'],original=self.exrows[key], cash=str(cash),
                capital=str(capital),bonus=str(bonus),record=fact['dates']['record_date'],
                pay=fact['dates']['CSDC_agent_cash_payment_date'],signed=fact['publication']['pdf_signature_date'],
                published=fact['publication']['api_listed_publication_date'],v_cash=terms['virtual_cash_CNY_per_all_share'],
                v_ratio=terms['virtual_stock_increase_ratio'],formula=terms['published_formula_normalized'],
                pdf=pdf,evidence_pages=terms['source_pages'],review_sha=REVIEW20_SHA,correction=correction)
            case=self.cases[key]
            if case['reference_unrounded']!=reviewed['independent_unrounded_reference'] or case['reference_cent']!=reviewed['half_up_cent']:
                raise ValueError('fresh arithmetic differs from independent PASS')
            for lineage in reviewed['original_action_query_lineage']:
                rawquery=json.loads(self.checked(lineage['source_path'],lineage['source_sha256']).read_text())
                # Exact original query row remains its own vendor row, never a synthetic component.
                if rawquery['rows'][lineage['source_row']] != [lineage['original_action'][f] for f in rawquery['fields']]:
                    raise ValueError('original action raw-row lineage changed')
        receipt40=json.loads(self.checked(RECEIPT40,RECEIPT40_SHA).read_text())
        overlay40=json.loads(self.checked(OVERLAY40,OVERLAY40_SHA).read_text())
        for path,h in receipt40['all_originals_and_new_artifacts_sha256'].items():
            self.checked(path,h)
        review40_sha=None;review40_cases={}
        if REVIEW40 is not None and REVIEW40_SHA is not None:
            review40=json.loads(self.checked(REVIEW40,REVIEW40_SHA).read_text())
            if not str(review40.get('status','')).startswith('PASS'):
                raise ValueError('021--060 independent review is not PASS')
            # The final exact independent receipt/source binding is versioned here
            # once the independent reviewer reports it; no caller bypass exists.
            if (review40.get('original_batch_receipt',{}).get('sha256')!=RECEIPT40_SHA
                    or review40.get('original_overlay',{}).get('sha256')!=OVERLAY40_SHA):
                raise ValueError('021--060 independent review original receipt differs')
            for path,h in review40['actual_byte_guard']['actual_sha256'].items():
                self.checked(path,h)
            for path,h in review40['original_daily_and_action_query_actual_byte_guards'].items():
                original_file=self.checked(path,h['sha256'])
                if original_file.stat().st_size!=h['bytes']:
                    raise ValueError('021--060 original vendor source size differs')
            review40_cases=require_review_case_roster(review40['independent_reviewed_cases'],self.fixed_order,21,60)
            review40_sha=REVIEW40_SHA
        if [c['source_only_order'] for c in overlay40['cases']] != list(range(21,61)):
            raise ValueError('021--060 fixed roster differs')
        for c in overlay40['cases']:
            if not all(c['validation'].values()) or c['missing_components']:
                raise ValueError('021--060 source facts/components not verified')
            key=(c['symbol'],c['ex_date'])
            if raw_ex_identity(self.exrows[key]) != c['original_normalized_ex_row']:
                raise ValueError('021--060 original row identity differs')
            rows=[r for r in self.actions if r['code'].replace('.','')==key[0] and r['dividOperateDate']==key[1]]
            if rows!=c['original_action_rows']:
                raise ValueError('021--060 original action identity differs')
            doc=c['official_primary'];pdf=self.checked(doc['download_receipt']['local_original_path'],doc['download_receipt']['sha256'])
            e=c['entitlement'];ref=c['reference']
            querydoc=dict(official_query=dict(path=doc['query_path'],sha256=sha(self.locate(doc['query_path']))),official_url=doc['url'])
            self.query_proof(querydoc,e['published_date'],e['record_date'],c['symbol'])
            self.normalized_case(order=c['source_only_order'],symbol=key[0],ex_date=key[1],
                original_index=c['original_input_index'],original=self.exrows[key],
                cash=e['actual_cash_per_entitled_unit'],capital=e['actual_capital_conversion_per_entitled_unit'],
                bonus=e['actual_bonus_per_entitled_unit'],record=e['record_date'],pay=e['cash_pay_date'],
                signed=e['signed_date'],published=e['published_date'],v_cash=ref['printed_virtual_cash'],
                v_ratio=ref['printed_virtual_share_ratio'],formula=ref['printed_formula_literal'],pdf=pdf,
                evidence_pages=c['selected_evidence_pages_1based'],review_sha=review40_sha,
                instrument=e['instrument_type'],source_kind='FIXED21_60')
            if review40_sha:
                verified=review40_cases[c['source_only_order']]
                created=self.cases[key]
                if (verified['official_primary_pdf']['sha256']!=created['primary_pdf_sha256']
                        or verified['official_primary_pdf']['path']!=str(Path(doc['download_receipt']['local_original_path']).resolve())
                        or verified['observed_signature_date']!=created['corrected_signature_date']
                        or D(verified['independent_half_up_cent'])!=D(created['reference_cent'])
                        or D(verified['independent_unrounded_reference'])!=D(created['reference_unrounded'])
                        or verified['original_input_index']!=created['original_input_index']
                        or D(verified['independently_parsed_actual_cash'])!=D(created['actual_cash_per_entitled_unit'])
                        or D(verified['independently_parsed_actual_capital'])!=D(created['actual_capital_per_entitled_unit'])
                        or D(verified['independently_parsed_bonus'])!=D(created['actual_bonus_per_entitled_unit'])):
                    raise ValueError('021--060 actual reconstructed facts differ from independent PASS')
                self.verify_review_lineage(created,verified)
        self.load_last40_candidates()
        policy=json.loads(self.checked(CDR_POLICY,CDR_POLICY_SHA).read_text())
        for path,h in policy['artifact_sha256'].items():
            self.checked(path,h)
        if policy['CDR_instrument_tax_fee_eligibility']!='UNKNOWN_MUST_BLOCK_FULL_NET_AND_ECONOMIC_ACCEPTANCE':
            raise ValueError('diagnostic CDR statutory source does not authorize qualification clearance')
        for path,h in CDR_UNIVERSE_FILES.items():
            universe=json.loads(self.checked(path,h).read_text())
            symbols=universe if isinstance(universe,list) else universe['symbols']
            if sorted(s for s in symbols if s.startswith('689'))!=['689009.SH']:
                raise ValueError('V4 historical CDR symbol scope differs; future instruments not certified')
        self.instrument_scope=dict(historical_cdr_symbols=['sh689009'],
            fixed_universe_source_sha256={str(p):h for p,h in CDR_UNIVERSE_FILES.items()},
            future_new_cdr_symbols_or_other_universe_not_certified=True)
        self.pending_orders=sorted(c['source_only_order'] for k,c in self.candidates.items() if k not in self.cases)
        self.instrument_qualifications={'sh689009':dict(instrument_type='CDR',status='UNKNOWN',
            tax_policy_applicability='UNKNOWN',fee_policy_applicability='UNKNOWN',
            equal_numeric_2015_a_share_rates_do_not_certify_cdr=True,
            source_case=('sh689009','2025-06-13'),source_input_sha256=INPUT_SHA,
            primary_pdf_sha256=self.candidates[('sh689009','2025-06-13')]['primary_pdf_sha256'])}
        self.source_identity=hashlib.sha256(json.dumps(self.source_hashes,sort_keys=True).encode()).hexdigest()
        self.verified=True
        return self

    def load_last40_candidates(self):
        receipt=json.loads(self.checked(RECEIPT100,RECEIPT100_SHA).read_text())
        if receipt['assigned_source_only_order_range']!=[61,100]:
            raise ValueError('061--100 candidate scope differs')
        for item in receipt['originals_and_rendered_evidence_inventory']:
            self.item(item)
        review_sha=None;reviewed_cases={}
        if REVIEW100 is not None and REVIEW100_SHA is not None:
            review=json.loads(self.checked(REVIEW100,REVIEW100_SHA).read_text())
            if review.get('status')!='PASS_SOURCE_FACTS_AND_EXACT_PRINTED_CENT' or review.get('original_batch_receipt',{}).get('sha256')!=RECEIPT100_SHA:
                raise ValueError('061--100 independent PASS or original receipt binding differs')
            self.item_map(review['actual_byte_guard']['actual_sha256'])
            self.item_map(review['original_daily_and_action_query_actual_byte_guards'])
            self.item_map(review['all_independent_read_evidence_actual_byte_guards'])
            reviewed_cases=require_review_case_roster(review['independent_reviewed_cases'],self.fixed_order,61,100)
            review_sha=REVIEW100_SHA
        for item in receipt['facts']:
            fact=json.loads(self.item(item).read_text())
            doc=fact['primary_document'];pdf=self.item(doc['pdf'])
            fresh=[p.extract_text() or '' for p in PdfReader(pdf).pages]
            saved=json.loads(self.item(doc['text']).read_text())
            if fresh!=[p['text'] for p in saved['pages']]:
                raise ValueError('061--100 fresh primary PDF text differs')
            signed=fact['publication']['pdf_signature_date']
            if not signature_date_present(fresh[-1],signed):
                raise ValueError('061--100 candidate signature not on bound final page')
            self.query_proof(doc,fact['publication']['api_listed_publication_date'],fact['dates']['record_date'],fact['symbol'])
            is_cdr=fact['symbol']=='sh689009'
            cash_key='gross_CNY_per_eligible_CDR' if is_cdr else 'gross_CNY_per_eligible_share'
            ref_cash_key='virtual_cash_CNY_per_all_CDR' if is_cdr else 'virtual_cash_CNY_per_all_share'
            ref_ratio_key='virtual_CDR_increase_ratio' if is_cdr else 'virtual_stock_increase_ratio'
            components=fact['actual_entitlement_components']
            cash=sum((D(c[cash_key]) for c in components if c['component']=='cash_dividend'),D(0))
            capital=sum((D(c['new_shares_per_eligible_share']) for c in components if c['component']=='capital_reserve_transfer'),D(0))
            bonus=sum((D(c['new_shares_per_eligible_share']) for c in components if c['component']=='bonus_share'),D(0))
            ref=fact['reference_price_terms'];key=(fact['symbol'],fact['ex_date'])
            self.normalized_case(order=fact['source_only_order'],symbol=key[0],ex_date=key[1],
                original_index=fact['original_input_index'],original=self.exrows[key],cash=str(cash),
                capital=str(capital),bonus=str(bonus),record=fact['dates']['record_date'],
                pay=fact['dates']['CSDC_agent_cash_payment_date'],signed=signed,
                published=fact['publication']['api_listed_publication_date'],v_cash=ref[ref_cash_key],
                v_ratio=ref[ref_ratio_key],formula=ref['published_formula_normalized'],pdf=pdf,
                evidence_pages=ref['source_pages'],review_sha=review_sha,
                instrument='CDR' if is_cdr else 'A_SHARE',source_kind='FIXED61_100')
            self.candidates[key]['actual_credit_date_observed']=all(
                c.get('credit_date') is not None for c in components if c['component']!='cash_dividend')
            self.candidates[key]['noncash_credit_dates']=[c.get('credit_date') for c in components if c['component']!='cash_dividend']
            if review_sha:
                observed=reviewed_cases[fact['source_only_order']];created=self.cases[key]
                reviewed_bonus=sum((D(c['new_shares_per_eligible_share']) for c in observed['actual_entitlement_components'] if c['component']=='bonus_share'),D(0))
                if (observed['original_fact']!=item or observed['official_primary_pdf']!=doc['pdf']
                        or raw_ex_identity(observed['original_normalized_ex_row'])!=created['original_reference']
                        or observed['actual_entitlement_components']!=components
                        or observed['dates']!=fact['dates']
                        or observed['observed_signature_date']!=signed or observed['stored_signature_date']!=signed
                        or not observed['signature_exact_match']
                        or D(observed['actual_cash'])!=cash or D(observed['actual_capital'])!=capital or reviewed_bonus!=bonus
                        or D(observed['vendor_full_literal_cash'])!=cash or D(observed['vendor_full_literal_capital'])!=capital
                        or observed['printed_formula_normalized']!=ref['published_formula_normalized']
                        or D(observed['printed_virtual_cash'])!=D(ref[ref_cash_key])
                        or D(observed['printed_virtual_ratio'])!=D(ref[ref_ratio_key])
                        or D(observed['independent_unrounded_reference'])!=D(created['reference_unrounded'])
                        or D(observed['independent_half_up_cent'])!=D(created['reference_cent'])
                        or D(observed['unmodified_raw_preclose'])!=D(created['original_reference']['vendor_ex_preclose'])
                        or not observed['exact_cent_match']
                        or observed['original_action_component_count']!=len(created['original_action_ids'])):
                    raise ValueError('061--100 individual source/actual terms/signature/reference differs from independent PASS')
                self.verify_review_lineage(created,observed)

    def require_all100_admitted(self):
        if len(self.cases)!=100 or self.pending_orders:
            raise ValueError('V4 001--100 admission waits for exact independent PASS review')


class PrimaryInputCatalogV4:
    """Delegate all actions/account rules to the frozen V3 catalog unchanged."""
    def __init__(self, original, reference_inputs):
        if type(original) is not PrimarySupplementCatalogV3 or type(reference_inputs) is not PrimaryReferenceInputsV4 or not reference_inputs.verified:
            raise ValueError('V4 requires verified frozen V3 catalog and independent primary inputs')
        self.original=original;self.reference_inputs=reference_inputs
        self.coverage=original.coverage;self.records=original.records;self.by_record=original.by_record
        self.query_reconciliation=original.query_reconciliation
        self.supplement=original.supplement
        self.source_identity=hashlib.sha256((original.source_identity+reference_inputs.source_identity).encode()).hexdigest()

    def require_coverage(self,symbol,years):
        self.original.require_coverage(symbol,years)

    def announce_held_record_actions(self,account,day):
        return self.original.announce_held_record_actions(account,day)

    def reference_resolution(self,row,result):
        previous=self.original.reference_resolution(row,result)
        if previous is not None:
            return previous
        key=(row['symbol'],str(row['ex_date'])[:10])
        case=self.reference_inputs.cases.get(key)
        if case is None:
            return None
        if raw_ex_identity(row)!=case['original_reference']:
            raise ValueError('V4 reference exposure original row differs')
        return dict(symbol=key[0],ex_date=key[1],original_status=row['status'],
            supplemental_status='VERIFIED_AS_PUBLISHED_PRIMARY_REFERENCE_V4_NOT_ORDINARY_FORMULA',
            original_vendor_reference_unmodified=True,printed_reference_cent=case['reference_cent'],
            official_primary_pdf_sha256=case['primary_pdf_sha256'],
            independent_pass_review_sha256=case['independent_pass_review_sha256'],
            instrument_type=case['instrument_type'],instrument_qualification_status=
                self.reference_inputs.instrument_qualifications.get(key[0],{}).get('status','UNCHANGED_V3_SCOPE'),
            actual_entitled_cash_and_stock_not_virtual=True,actual_historical_source_arrival_verified=False,
            noncash_fifo_or_official_broker_tax_rounding_verified=False)
