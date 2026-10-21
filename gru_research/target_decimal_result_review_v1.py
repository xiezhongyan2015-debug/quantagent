"""Read only one completed Decimal research attempt; never execute replay.

Default is a pure contract. Explicit review requires the root's externally
pinned final process receipt. Missing, partial or failed evidence is retained
as such; neither process exit nor a valid diagnostic journal accepts economics.
Only this new attempt's JSON/log bytes and explicitly small source identities
are read. No score CSV, checkpoint, old journal, NumPy array or minute archive
is opened. There is no import of a provider, executor, model or old inspector.
"""
from __future__ import annotations
import argparse
import ast
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / 'gru_research'
NAMESPACE = 'TARGET_SOURCE_BOUND_DECIMAL_RESEARCH_V1'
PREP = HERE / 'artifacts/target_decimal_quote_research_preparation_v2_20261002'
CONTRACT = PREP / 'research_contract_v2.json'
CONTRACT_SHA = '3f0baed55c2b9ed172b1eb17c8ccb1262b6fa9ac7767136472782dabd1142668'
CAPTURE = PREP / 'root_actual_replay_capture_attempt1'
WRAPPER_SOURCE = PREP / 'root_actual_replay_capture_v1.py'
RESULTS = HERE / 'artifacts/target_decimal_research_replay_v1/attempt1'
REVIEW_ROOT = HERE / 'artifacts/target_decimal_result_review_v1'
OVERLAY = PREP / 'root_actual_preflight_attempt1/actual_source_bound_overlay_v1.json'
OVERLAY_SHA = 'cfee09fd7826f9706690264b1ae31fa6d2a5f8afb8b5ab87d0e064b5ae26ff58'
OVERLAY_ID = 'c1fabeb130ef1b94c17fedf3b64735d418a6c461e8d9c18d50fc5f9bddeb4340'
MODELS = ('fixed_label_1', 'fixed_label_2', 'fixed_label_3', 'fixed_label_4',
          'blo_selected', 'selected_full', 'pv', 'pv_factors', 'pv_fund')
SCENARIOS = ('primary', 'stress')
BASES = ('pretax', 'tax_upper_reserve')
FLAGS = ('full_PLAN_complete', 'economic_target_accepted', 'all_PLAN_acceptance_complete')
WINDOWS = {'whole_observed': ('2025-01-02', '2026-09-23'),
           'calendar_2025_observed': ('2025-01-02', '2025-12-31'),
           'calendar_2026_through_sep23': ('2025-12-31', '2026-09-23')}
SMALL_CAP = 2 * 1024**2
JOURNAL_CAP = 128 * 1024**2
COMPARISON_CAP = 128 * 1024**2
TOTAL_READ_CAP = 2 * 1024**3


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha_body(body):
    return hashlib.sha256(body).hexdigest()


def stat(path):
    p = Path(path)
    require(p.is_file() and not p.is_symlink(), 'regular non-symlink file required: ' + str(p))
    s = p.stat()
    return dict(bytes=s.st_size, mtime_ns=s.st_mtime_ns, inode=s.st_ino, device=s.st_dev, mode=s.st_mode)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key: ' + key)
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError('nonstandard JSON numeric constant: ' + value)


def decode(body):
    return json.loads(body.decode('utf-8'), object_pairs_hook=unique_object, parse_constant=invalid_constant)


class Reader:
    def __init__(self):
        self.files = {}
        self.read_bytes = 0

    def bytes(self, path, cap=SMALL_CAP, expected=None):
        p = Path(path).resolve()
        require(str(Path(path).absolute()) == str(p), 'symlink/aliased input path refused: ' + str(path))
        before = stat(p)
        require(before['bytes'] <= cap, 'read cap exceeded: ' + str(p))
        self.read_bytes += before['bytes']
        require(self.read_bytes <= TOTAL_READ_CAP, 'bounded new-output read budget exceeded')
        body = p.read_bytes()
        ident = dict(path=str(p), bytes=len(body), sha256=sha_body(body))
        require(stat(p) == before and len(body) == before['bytes'], 'input changed during read: ' + str(p))
        if expected is not None:
            require(ident['sha256'] == expected, 'externally pinned SHA mismatch: ' + str(p))
        if str(p) in self.files:
            require(self.files[str(p)]['identity'] == ident, 'input changed between reads: ' + str(p))
        self.files[str(p)] = dict(identity=ident, before=before)
        return body

    def json(self, path, cap=SMALL_CAP, expected=None):
        return decode(self.bytes(path, cap, expected))

    def pin(self, item):
        body = self.bytes(Path(item['path']), SMALL_CAP, item['sha256'])
        require(len(body) == item['bytes'], 'pinned byte length differs: ' + item['path'])
        return body

    def unchanged(self):
        for filename, item in self.files.items():
            require(stat(filename) == item['before'], 'input STAT changed after review: ' + filename)
        return {filename: item['identity'] for filename, item in sorted(self.files.items())}


class ComparisonStream:
    """Frame top-level/nested JSON objects without retaining all36 summaries.

    Containers are framed using quoted-string/escape state, then each retained
    small value is validated by the standard JSON decoder (including duplicate
    keys). No seek, regex truncation or financial-result reconstruction occurs.
    """
    def __init__(self, reader, path):
        self.reader,self.path=reader,Path(path).resolve()
        require(str(Path(path).absolute())==str(self.path),'aliased comparison input')
        self.before=stat(self.path)
        require(self.before['bytes']<=COMPARISON_CAP,'comparison file exceeds128MiB budget')
        reader.read_bytes+=self.before['bytes']
        require(reader.read_bytes<=TOTAL_READ_CAP,'new output read budget exceeded')
        self.source=self.path.open('rb');self.block=b'';self.index=0;self.pending=None
        self.digest=hashlib.sha256();self.bytes_read=0

    def __enter__(self):return self

    def byte(self):
        if self.pending is not None:
            result=self.pending;self.pending=None;return result
        if self.index==len(self.block):
            self.block=self.source.read(65536);self.index=0
            if not self.block:return None
            self.digest.update(self.block);self.bytes_read+=len(self.block)
        result=self.block[self.index];self.index+=1;return result

    def nonspace(self):
        value=self.byte()
        while value in (9,10,13,32):value=self.byte()
        return value

    def raw_value(self,cap=SMALL_CAP):
        first=self.nonspace();require(first is not None,'truncated comparison JSON')
        result=bytearray((first,))
        if first in (91,123):
            stack=[first];quoted=False;escaped=False
            while stack:
                value=self.byte();require(value is not None,'truncated comparison container')
                result.append(value);require(len(result)<=cap,'one comparison value exceeds budget')
                if quoted:
                    if escaped:escaped=False
                    elif value==92:escaped=True
                    elif value==34:quoted=False
                elif value==34:quoted=True
                elif value in (91,123):stack.append(value)
                elif value in (93,125):
                    require((stack[-1],value) in ((91,93),(123,125)),'mismatched comparison container')
                    stack.pop()
        elif first==34:
            escaped=False
            while True:
                value=self.byte();require(value is not None,'truncated comparison string')
                result.append(value);require(len(result)<=cap,'comparison string over budget')
                if escaped:escaped=False
                elif value==92:escaped=True
                elif value==34:break
        else:
            while True:
                value=self.byte()
                if value is None or value in (9,10,13,32,44,93,125):
                    if value in (44,93,125):self.pending=value
                    break
                result.append(value);require(len(result)<=cap,'comparison scalar over budget')
        return bytes(result)

    def value(self,cap=SMALL_CAP):return decode(self.raw_value(cap))

    def keys(self):
        require(self.nonspace()==123,'comparison object required')
        first=self.nonspace()
        if first==125:return
        require(first is not None,'truncated comparison object');self.pending=first
        seen=set()
        while True:
            key=self.value();require(type(key) is str and key not in seen,'invalid/duplicate comparison key')
            seen.add(key);require(self.nonspace()==58,'comparison colon missing')
            yield key  # Caller consumes exactly one value before resuming.
            separator=self.nonspace()
            if separator==125:return
            require(separator==44,'comparison object separator missing')

    def __exit__(self,kind,value,tb):
        try:
            if kind is None:
                require(self.nonspace() is None,'trailing comparison data')
                require(self.bytes_read==self.before['bytes'] and stat(self.path)==self.before,'comparison changed/truncated during review')
                self.reader.files[str(self.path)]=dict(identity=dict(path=str(self.path),bytes=self.bytes_read,
                    sha256=self.digest.hexdigest()),before=self.before)
        finally:self.source.close()


def core_hash(value):
    return sha_body(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode())


def read_comparison(reader,path,summary_core_hashes):
    retained={};seen_results=False
    with ComparisonStream(reader,path) as stream:
        for key in stream.keys():
            if key!='results':
                retained[key]=stream.value(SMALL_CAP)
                continue
            seen_results=True;models=[]
            for model in stream.keys():
                models.append(model);require(model in MODELS,'unknown comparison result model')
                scenarios=[]
                for scenario in stream.keys():
                    scenarios.append(scenario);require(scenario in SCENARIOS,'unknown comparison result scenario')
                    bases=[]
                    for basis in stream.keys():
                        bases.append(basis);require(basis in BASES,'unknown comparison result account basis')
                        value=stream.value(JOURNAL_CAP)
                        require((model,scenario,basis) in summary_core_hashes
                            and core_hash(value)==summary_core_hashes[(model,scenario,basis)],'streamed comparison summary differs from its exact original core')
                        del value
                    require(set(bases)==set(BASES),'comparison results account groups differ')
                require(set(scenarios)==set(SCENARIOS),'comparison results scenarios differ')
            require(set(models)==set(MODELS),'comparison result9 model roster differs')
    require(seen_results,'comparison results missing')
    return retained


def flags_false(value, label):
    for name in FLAGS:
        require(value.get(name) is False, label + ' must explicitly retain ' + name + '=false')


def not_accepting_recursively(value, label):
    # Eligibility to print a stylized diagnostic is distinct from accepting an
    # economic target. Do not silently relabel an existing nested diagnostic.
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FLAGS or key in ('point_net_target_accepted', 'net_excess_return_point_acceptance'):
                require(item is False, label + ' contains affirmative acceptance: ' + key)
            not_accepting_recursively(item, label)
    elif isinstance(value, list):
        for item in value:
            not_accepting_recursively(item, label)


def datetime_ns(value):
    d = datetime.fromisoformat(value)
    require(d.tzinfo is not None, 'aware timestamp required')
    delta = d.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 10**9 + delta.microseconds * 1000


def slots():
    base = datetime_ns('2025-02-10T00:00:00+08:00')
    return {slot: (base + minute * 60 * 10**9, base + (minute + 5) * 60 * 10**9)
            for slot, minute in enumerate((571, 631, 781, 841))}


def pure_contract():
    return dict(schema_version=1, status='PREPARED_ONLY_READONLY_REVIEW_NOT_EXECUTED', namespace=NAMESPACE,
        default_reads_files=False, default_creates_outputs=False, provider_or_engine_or_old_inspector_imported=False,
        expected_models=list(MODELS), expected_scenarios=list(SCENARIOS), expected_account_bases=list(BASES),
        expected_unique_run_ids=36, fixed_results_directory=str(RESULTS),
        required_completed_wrapper_receipt=str(CAPTURE / 'root_actual_replay_execution_receipt_v1.json'),
        root_receipt_sha_must_be_supplied_out_of_band=True,
        preparation_sha_must_be_supplied_out_of_band=True,
        preparation_receipt_path=str(HERE/'artifacts/target_decimal_result_review_preparation_v3_20261002/preparation_receipt_v3.json'),
        wrapper_schema_basis='Pinned root_actual_replay_capture_v1.py source; completion receipt not yet read by preparation',
        contract_path=str(CONTRACT), contract_sha256=CONTRACT_SHA,
        overlay_sha256=OVERLAY_SHA, overlay_semantic_id=OVERLAY_ID,
        allowed_target_symbol='sh600126', allowed_target_date='2025-02-10', allowed_UTC_windows=slots(),
        interpretation='36 recorded attempts, process exit, diagnostic completeness and economic acceptance are separate',
        queue_zero_fill_evidence='Only actual AT_LIMIT_QUEUE_NOT_ASSUMED outcomes; absence is NOT_OBSERVED',
        delisting_cash_choice_fractional_share_and_FIFO_tax_unknown_not_resolved=True,
        does_not_read_score_CSV_checkpoint_old_journal_or_large_arrays=True,
        does_not_recompute_returns_select_models_or_claim_upstream_market_authenticity=True,
        current_actual_review_count=0, full_PLAN_complete=False, economic_target_accepted=False,
        all_PLAN_acceptance_complete=False, read_caps=dict(small_file=SMALL_CAP, per_journal=JOURNAL_CAP,
            comparison=COMPARISON_CAP, total=TOTAL_READ_CAP))


def actual_source_metadata(reader, contract, overlay):
    require(contract['namespace'] == NAMESPACE and contract['models'] == list(MODELS)
        and contract['scenarios'] == list(SCENARIOS) and contract['account_bases'] == list(BASES)
        and contract['research_combinations'] == 36, 'registered exact36 roster differs')
    require(contract['target_symbol'] == 'sh600126' and contract['target_date'] == '2025-02-10'
        and contract['delisting_cash_choice_fraction_FIFOTax_unknown_not_repaired'] is True
        and contract['economics_or_full_PLAN_acceptance'] is False, 'registered limited scope differs')
    require(contract['retry_with_other_output_authorized'] is False, 'new-attempt retry authorization differs')
    require(set(contract['new_sources']) == {'exact_decimal_twap_v1.py', 'target_decimal_quote_overlay_v1.py',
        'target_decimal_research_replay_v1.py'}, 'new source roster differs')
    for name, expected in contract['new_sources'].items():
        reader.bytes(HERE / name, SMALL_CAP, expected)
    for item in contract['old_small_sources'].values():
        reader.pin(item)
    manifest = decode(reader.pin(contract['original_manifest']))
    require(manifest['models'] == list(MODELS) and set(manifest['model_versions']) == set(MODELS), 'original9 model version scope differs')
    require(isinstance(manifest['freeze_id'], str) and manifest['freeze_id'], 'actual freeze ID required')
    require(overlay['namespace'] == NAMESPACE and overlay['target_symbol'] == 'sh600126'
        and overlay['target_date'] == '2025-02-10' and overlay['overlay_id'] == OVERLAY_ID, 'actual prepared overlay differs')
    require(overlay['old_provider_or_rules_or_data_modified'] is False
        and overlay['quote_eligibility_fields_changed'] is False
        and overlay['independently_verified_historical_arrival'] is False, 'overlay changed eligibility/arrival claims')
    for item in overlay['source_pins'].values():
        reader.pin(item)
    keys = ('namespace', 'target_symbol', 'target_date', 'source_pins', 'conversion_semantics', 'quotes')
    semantic = sha_body(json.dumps({k:overlay[k] for k in keys}, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
    require(semantic == OVERLAY_ID, 'overlay semantic binding mismatch')
    proof = reader.json(overlay['source_pins']['four_window_evidence']['path'])
    require(len(overlay['quotes']) == len(proof['quotes']) == 4, 'exact four registered quotes required')
    for slot, quote in enumerate(overlay['quotes']):
        witness = proof['quotes'][slot]
        require(quote['slot'] == slot and (quote['start_UTC_ns'], quote['end_UTC_ns']) == slots()[slot], 'registered UTC slot changed')
        require(len(quote['individual_open_decimals']) == 5 and all(v == '7.61' for v in quote['individual_open_decimals']), 'target individual opens differ')
        exact = sum((Decimal(v) for v in quote['individual_open_decimals']), Decimal(0))/Decimal(5)
        require(str(exact) == quote['exact_decimal_price'] == witness['source_bound_exact_decimal_mean'] == '7.61', 'exact Decimal mean mismatch')
        require(quote['individual_open_decimals'] == witness['five_observed_FP64_raw_opens_shortest_roundtrip_decimals']
            and quote['individual_open_binary_hex'] == witness['five_raw_open_binary_hex']
            and quote['original_row_ordinals'] == witness['five_original_row_ordinals']
            and quote['volume_shares'] == witness['window_volume_shares'], 'primary row witness mismatch')
        require(quote['original_double_as_decimal'] == str(witness['sealed_original_double_mean'])
            and quote['complete'] is True and quote['all_five_minutes_flat'] is True
            and quote['observed_positive_volume'] is True, 'original quote quality changed')
    source = reader.bytes(HERE / 'trading_comparison_v4.py').decode()
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_manifest')
    old_ast = sha_body(ast.dump(fn, include_attributes=False).encode())
    candidates = [n for n in ast.walk(fn) if isinstance(n, ast.Assign) and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'run_id']
    require(len(candidates) == 1, 'original run-ID assignment not unique')
    candidates[0].value = ast.BinOp(left=ast.Constant(NAMESPACE+':'), op=ast.Add(), right=candidates[0].value)
    new_ast = sha_body(ast.dump(fn, include_attributes=False).encode())
    return manifest, old_ast, new_ast


def provenance(metadata, contract, overlay, asts):
    require(metadata['namespace'] == NAMESPACE and metadata['target_symbol'] == 'sh600126'
        and metadata['target_date'] == '2025-02-10', 'wrong research namespace/target')
    require(metadata['overlay_id'] == OVERLAY_ID and metadata['overlay_sha256'] == OVERLAY_SHA
        and metadata['overlay_path'] == str(OVERLAY), 'wrong actual overlay identity')
    require(metadata['research_contract_path'] == str(CONTRACT) and metadata['research_contract_sha256'] == CONTRACT_SHA
        and metadata['original_manifest_path'] == contract['original_manifest']['path']
        and metadata['original_manifest_sha256'] == contract['original_manifest']['sha256'], 'research/original manifest binding differs')
    require(metadata['new_source_roster'] == contract['new_sources'] and metadata['actual_quote_sources'] == overlay['source_pins'], 'new quote source bindings differ')
    require(metadata['original_driver_sha256'] == contract['old_small_sources']['driver_source:gru_research/trading_comparison_v4.py']['sha256']
        and (metadata['original_run_AST_sha256'], metadata['adapted_run_AST_sha256']) == asts, 'AST/source identity differs')
    require(metadata['local_global_bindings_overridden'] == ['SealedMinuteSource', 'write_new']
        and metadata['original_module_globals_modified'] is False, 'local composition scope differs')
    for name in ('old_V4_seal_fields_bind_unchanged_old_files_only', 'new_decimal_quote_not_represented_as_old_window_raw_price',
                 'original_rule_cost_order_score_model_and_company_event_logic_unchanged', 'actual_fraction_and_delisting_unknown_not_assumed'):
        require(metadata[name] is True, 'original rule/unknown identity guard missing: '+name)
    flags_false(metadata, 'research provenance')


def check_event(event, overlay):
    require(event['namespace'] == NAMESPACE and event['overlay_id'] == OVERLAY_ID and event['symbol'] == 'sh600126', 'overlay affected another target')
    match = [q for q in overlay['quotes'] if q['start_UTC_ns'] == event['execution_start']]
    require(len(match) == 1, 'overlay event outside the four registered UTC starts')
    quote = match[0]
    require(event['execution_end'] == quote['end_UTC_ns'] and type(event['signal_time_utc_ns']) is int
        and event['signal_time_utc_ns'] < event['execution_start'], 'overlay event has wrong end/decision clock')
    require(event['new_quote_raw_price'] == quote['exact_decimal_price'] == '7.61'
        and event['old_quote_raw_price'] == quote['original_double_as_decimal'], 'overlay numeric source differs')
    for ek, qk in (('original_row_ordinals','original_row_ordinals'), ('actual_individual_open_decimals','individual_open_decimals'),
                   ('actual_individual_open_binary_hex','individual_open_binary_hex'), ('volume_shares','volume_shares')):
        require(event[ek] == quote[qk], 'overlay event actual-row witness differs: '+ek)
    require(event['source_payload_sha256'] == overlay['source_pins']['payload']['sha256']
        and event['source_payload_path'] == overlay['source_pins']['payload']['path']
        and event['root_four_window_evidence'] == overlay['source_pins']['four_window_evidence'], 'overlay event source identity differs')
    require(event['original_eligibility_fields_unchanged'] is True
        and event['old_hourly_path_SHA_is_inherited_file_reference_not_new_quote_numeric_source'] is True, 'quote eligibility/original-source scope changed')
    return quote['slot']


def closed_capture_maps(reader,prepared,contract,manifest,root_receipt):
    """Close capture rosters without opening any original large data body."""
    preflight=decode(reader.pin(prepared['root_preflight_receipt']))
    require(preflight['source_count']==56 and preflight['small_sources_before_after_identical'] is True
        and preflight['actual_research_replays_executed']==0,'original root56 preflight scope differs')
    closed=decode(reader.pin(prepared['root_preflight_closed_small_map']))
    require(type(closed) is list and len(closed)==56 and len({item['path'] for item in closed})==56,
        'exact original closed56 small roster required')
    require(all(process['exit_code']==0 for process in preflight['processes']),'original preflight did not complete successfully')
    for item in closed:
        reader.pin(item)  # Only named small files; never their referenced data.
    extras=prepared['root_wrapper_six_extra_small_sources']
    extra_paths=[str(PREP/'root_actual_preflight_attempt1/root_actual_preflight_receipt_v2.json'),
        str(CONTRACT),str(OVERLAY),str(WRAPPER_SOURCE),str(HERE/'artifacts/dataset/meta.json'),
        str(HERE/'artifacts/v4_actual_delivery_job405900/formal/corrected_score_exports/manifest.json')]
    require([item['path'] for item in extras]==extra_paths,'root wrapper exact six-extra roster differs')
    for item in extras:
        reader.pin(item)
    expected_small=closed+extras  # Intentional repeated contract entry matches root source exactly.
    small_before=reader.json(CAPTURE/'small_SHA_before_map.json',COMPARISON_CAP)
    small_after=reader.json(CAPTURE/'small_SHA_after_map.json',COMPARISON_CAP)
    require(small_before==small_after==expected_small and root_receipt['actual_small_SHA_file_count']==62,
        'captured small maps are not the closed56 plus exact six root extras')
    seal_item=contract['old_small_sources']['trading_comparison_source_seal_v4.json']
    seal=decode(reader.pin(seal_item))
    expected_inherited=dict(seal['immutable_files'])
    expected_inherited.update(seal['minute_raw_day_files'])
    expected_inherited.update(manifest['frozen_files'])
    inherited=reader.json(CAPTURE/'inherited_original_source_SHA_map.json',COMPARISON_CAP)
    require(inherited==expected_inherited,'inherited map differs from exact original seal/minute/manifest union')
    dataset=HERE/'artifacts/dataset'
    old_output=HERE/'artifacts/v4_actual_delivery_job405900/v4_capture_v1/driver_output'
    require(dataset.is_dir() and old_output.is_dir(),'fixed original STAT roster directories missing')
    filenames=list(expected_inherited)
    filenames.extend(str(p) for p in dataset.glob('*.npy'))
    filenames.extend(str(p) for p in old_output.rglob('*') if p.is_file())
    expected_keys={str(Path(p).resolve()) for p in filenames}
    before=reader.json(CAPTURE/'original_stat_before_map.json',COMPARISON_CAP)
    after=reader.json(CAPTURE/'original_stat_after_map.json',COMPARISON_CAP)
    require(set(before)==set(after)==expected_keys and before==after
        and len(expected_keys)==root_receipt['original_stat_file_count'],'captured original STAT roster not the exact public root union')
    current={filename:stat(filename) for filename in sorted(expected_keys)}
    require(current==before,'original files STAT no longer matches root captured before/after values')
    return dict(closed_preflight_small_count=56,wrapper_extra_small_count=6,
        captured_small_count=62,actual_small_file_SHA_rechecked=True,
        inherited_union_source_count=len(expected_inherited),inherited_union_exactly_matches_original_seal_minute_manifest=True,
        captured_original_STAT_count=len(expected_keys),original_STAT_exact_roster_and_fresh_values_verified=True,
        original_large_file_bytes_not_opened_or_rehashed=True,
        large_SHA_scope='Inherited saved sealed identities; exact roster and freshSTAT only for large/old bodies')


def review_journal(journal, model, scenario, basis, expected_id, metadata, overlay):
    require(journal['run_id'] == expected_id and journal['model'] == model, 'run/model identity differs')
    require(journal['cost_scenario'] == scenario.upper(), 'cost scenario differs')
    require(journal['status'] in ('COMPLETE_DIAGNOSTIC_ONLY', 'INCOMPLETE'), 'unregistered replay status')
    require(journal['target_decimal_research_provenance'] == metadata, 'journal provenance differs')
    flags_false(journal, 'journal'); not_accepting_recursively(journal, 'journal')
    require(journal['net_NAV_contract_compatible'] is False and journal['source_and_execution_verified'] is False, 'journal promoted diagnostic scope')
    require(journal['distribution_tax_liability_included'] is (basis == 'tax_upper_reserve'), 'account basis differs')
    require(bool(journal['failure']) == (journal['status'] == 'INCOMPLETE')
        and journal['incomplete_prefix_is_not_a_full_window'] is (journal['status'] == 'INCOMPLETE'), 'failure/completeness state differs')
    if journal['status'] == 'INCOMPLETE':
        key = 'full_period_pretax_return' if basis == 'pretax' else 'full_period_tax_reserve_scenario_return'
        require(journal[key] is None, 'incomplete prefix reported a full-period return')
    embedded = [a['target_decimal_quote_overlay'] for a in journal['sealed_minute_source_accesses'] if 'target_decimal_quote_overlay' in a]
    require(journal['target_decimal_overlay_events'] == embedded, 'explicit overlay events differ from original access journal')
    used_slots = [check_event(event, overlay) for event in embedded]
    queue, target_orders = [], []
    for outcome in journal['outcomes']:
        require(type(outcome['filled']) is int and outcome['filled'] >= 0, 'invalid actual outcome filled count')
        if outcome['state'] == 'AT_LIMIT_QUEUE_NOT_ASSUMED':
            require(outcome['filled'] == 0 and outcome['price'] is None and outcome['fees'] is None
                and outcome['recorded_fill_time'] is None, 'limit queue was fabricated as a fill')
        if outcome['symbol'] == 'sh600126' and outcome['date'] == '2025-02-10':
            require(0 in used_slots, 'target order lacks source-bound morning overlay evidence')
            require(datetime_ns(outcome['execution_window_start']) == slots()[0][0]
                and datetime_ns(outcome['execution_window_end']) == slots()[0][1], 'target execution clock differs')
            target_orders.append(dict(order_id=outcome['order_id'], side=outcome['side'], state=outcome['state'], filled=outcome['filled']))
            # 7.61 is the independently bound exact ceiling for this stockday.
            # Any BUY is a queue or an earlier explicit rejection, never a fill.
            if outcome['side'] == 'BUY':
                require(outcome['filled'] == 0, 'target BUY filled at its exact source-bound limit ceiling')
            if outcome['state'] == 'AT_LIMIT_QUEUE_NOT_ASSUMED':
                queue.append(dict(order_id=outcome['order_id'], symbol=outcome['symbol'], date=outcome['date'],
                    side=outcome['side'], filled=0, price=None, fees=None, recorded_fill_time=None))
    ledger = journal['account_event_ledger']
    require(ledger and ledger[-1]['hash'] == journal['account_event_ledger_hash'], 'declared journal tail hash differs')
    for event in ledger:
        if event.get('day') == '2025-02-10' and event['kind'] == 'FILL' and event['payload'].get('symbol') == 'sh600126':
            require(event['payload']['side'] != 'BUY', 'target queue BUY has an account FILL event')
    marks = journal['completed_marks']
    actual_dates = [m['date'] for m in marks]
    require(actual_dates == sorted(set(actual_dates)), 'completed marks not unique/chronological')
    if journal['status'] == 'COMPLETE_DIAGNOSTIC_ONLY':
        require(actual_dates == journal['declared_valuation_days'], 'diagnostic-complete journal lacks full declared marks')
    return dict(run_id=expected_id, status=journal['status'], failure=journal['failure'], completed_mark_count=len(marks),
        last_completed_mark=actual_dates[-1] if actual_dates else None, overlay_event_count=len(embedded),
        actual_overlay_slots=sorted(set(used_slots)), target_orders=target_orders, target_queue_zero_fill_events=queue,
        limit_queue_evidence_status='OBSERVED_ZERO_FILL' if queue else 'NOT_OBSERVED_NO_QUEUE_ASSERTED',
        account_journal_tail_hash=journal['account_event_ledger_hash'], ledger_hash_chain_recomputed=False,
        unknown_fraction_delisting_cash_choice_and_FIFO_tax_not_resolved_by_this_review=True)


def review_summary(summary, journal, basis, metadata):
    flags_false(summary, 'summary'); not_accepting_recursively(summary, 'summary')
    require(summary['target_decimal_research_provenance'] == metadata and summary['account_basis'] == basis
        and summary['replay_status'] == journal['status'] and summary['continuous_account'] is True, 'summary journal/account binding differs')
    require(summary['source_and_minute_hashes_verified_before_and_after_run'] is True, 'summary lacks original byte guard outcome')
    require(set(summary['windows']) == set(WINDOWS), 'fixed three summary windows differ')
    result = {}
    for name, window in summary['windows'].items():
        require((window['anchor'], window['end']) == WINDOWS[name] and window['reset_account'] is False, 'continuous-account boundary changed')
        require(window['point_net_excess_return'] is None and window['point_net_target_accepted'] is False, 'diagnostic promoted to point-net acceptance')
        if not window['full_mark_and_execution_coverage']:
            require(window['scenario_values'] is None and window['same_path_net_tax_bounds'] is None, 'incomplete full window substituted a finite estimate')
        instrument = window['instrument_qualification_exposures'] + window['prior_instrument_qualification_exposures_affect_anchor']
        if instrument:
            require(window['same_path_net_tax_bounds'] is None and window['economic_comparison_eligible'] is False, 'unknown CDR instrument qualification cleared')
        result[name] = dict(full_mark_and_execution_coverage=window['full_mark_and_execution_coverage'],
            economic_comparison_eligible_as_recorded=window['economic_comparison_eligible'],
            recorded_eligibility_is_not_target_acceptance=True,
            current_missing_execution_window_count=len(window['missing_execution_windows']),
            prior_execution_evidence_affect_anchor_count=len(window['prior_execution_evidence_affect_anchor']),
            current_unresolved_reference_count=len(window['exposed_unresolved_ex_references']),
            prior_unresolved_reference_count=len(window['prior_ex_reference_exposures_affect_anchor']),
            unknown_instrument_current_or_prior_count=len(instrument),
            finite_same_path_tax_bounds_present=window['same_path_net_tax_bounds'] is not None,
            full_PLAN_complete=False, economic_target_accepted=False)
    return result


def run_review(args, output):
    reader = Reader()
    require(Path(args.results).resolve() == RESULTS, 'only the one registered attempt may be inspected')
    require(Path(args.wrapper_receipt).resolve() == CAPTURE / 'root_actual_replay_execution_receipt_v1.json', 'root final receipt path differs')
    root_receipt = reader.json(Path(args.wrapper_receipt), expected=args.wrapper_sha)
    require(root_receipt['status'] == 'ACTUAL_CHILD_RETURNED_PENDING_INDEPENDENT_NEW_JOURNAL_INSPECTION', 'root completion receipt missing/not completed')
    require(type(root_receipt['exit_code']) is int and root_receipt['new_output'] == str(RESULTS), 'root process identity differs')
    require(root_receipt['actual_small_SHA_before_after_identical'] is True
        and root_receipt['original_stat_before_after_identical'] is True, 'root source guard failed')
    for key in ('wrapper_does_not_infer_36_completion_from_exit', 'new_namespace_driver_executed_once'):
        require(root_receipt[key] is True, 'root capture guard missing: '+key)
    require(root_receipt['old_driver_or_capture_or_inspector_rerun'] is False
        and root_receipt['model_training_or_forward_executed'] is False, 'root capture exceeded replay scope')
    flags_false(root_receipt, 'root execution receipt')
    contract = reader.json(CONTRACT, expected=CONTRACT_SHA)
    overlay = reader.json(OVERLAY, expected=OVERLAY_SHA)
    manifest, old_ast, new_ast = actual_source_metadata(reader, contract, overlay)
    wrapper_body = reader.bytes(WRAPPER_SOURCE)
    # The preparation pins the actual wrapper source; actual review requires it.
    prepared = reader.json(HERE / 'artifacts/target_decimal_result_review_preparation_v3_20261002/preparation_receipt_v3.json',
        expected=args.preparation_sha)
    require(sha_body(wrapper_body) == prepared['root_wrapper_source']['sha256'], 'root wrapper source differs from prepared schema')
    require(reader.bytes(Path(__file__)) and reader.files[str(Path(__file__).resolve())]['identity']['sha256'] == prepared['review_source']['sha256'], 'review source differs from preparation')
    expected_argv = [root_receipt['argv'][0], str(HERE/'target_decimal_research_replay_v1.py'), 'run', '--execute',
        '--contract-file', str(CONTRACT), '--contract-sha', CONTRACT_SHA, '--overlay', str(OVERLAY), '--overlay-sha', OVERLAY_SHA,
        '--manifest', contract['original_manifest']['path'], '--output', str(RESULTS)]
    require(root_receipt['argv'] == expected_argv, 'actual root dispatched command differs')
    launch=reader.json(CAPTURE/'actual_child_launch_receipt_v1.json')
    dispatch=reader.json(CAPTURE/'ROOT_DISPATCH_STARTED_v1.json')
    require(launch['status']=='ACTUAL_CHILD_LAUNCHED_NOT_COMPLETED' and launch['pid']==root_receipt['child_pid']
        and launch['argv']==dispatch['argv']==expected_argv and launch['output']==dispatch['output']==str(RESULTS)
        and launch['started_utc']==dispatch['started_utc']==root_receipt['started_utc'], 'root launch/dispatch/process identities differ')
    require(dispatch['status']=='ONE_ACTUAL_NEW_NAMESPACE_DISPATCH_COMMITTED' and dispatch['retry_authorized'] is False,
        'root dispatch did not preserve single-attempt boundary')
    require(datetime_ns(root_receipt['ended_utc'])>=datetime_ns(root_receipt['started_utc']), 'root process clock order differs')
    stdout = reader.pin(root_receipt['stdout']).decode('utf-8')
    reader.pin(root_receipt['stderr'])
    require(root_receipt['stdout']['path'] == str(CAPTURE/'actual_replay.stdout.log')
        and root_receipt['stderr']['path'] == str(CAPTURE/'actual_replay.stderr.log'), 'root log paths differ')
    closed_maps=closed_capture_maps(reader,prepared,contract,manifest,root_receipt)
    marker = reader.json(RESULTS.parent/'EXECUTION_STARTED_v1.json')
    provenance(marker, contract, overlay, (old_ast, new_ast))
    require(marker['requested_output'] == str(RESULTS) and marker['completed'] is False
        and marker['restart_with_other_output_authorized'] is False, 'namespace marker does not identify this sole attempt')
    metadata_keys = ('namespace','target_symbol','target_date','overlay_id','overlay_path','overlay_sha256','research_contract_path',
        'research_contract_sha256','original_manifest_path','original_manifest_sha256','original_driver_sha256','original_run_AST_sha256',
        'adapted_run_AST_sha256','AST_difference','local_global_bindings_overridden','actual_quote_sources','new_source_roster',
        'old_V4_seal_fields_bind_unchanged_old_files_only','new_decimal_quote_not_represented_as_old_window_raw_price',
        'original_module_globals_modified','quote_change_scope','original_rule_cost_order_score_model_and_company_event_logic_unchanged',
        'actual_fraction_and_delisting_unknown_not_assumed',*FLAGS)
    metadata = {k:marker[k] for k in metadata_keys}
    expected_paths = {'input_manifest.json', 'comparison_protocol.json', 'comparison.json', 'research_namespace_receipt.json', 'new_namespace_failure_scope.json'}
    for model in MODELS:
        expected_paths.add(model+'_score_coverage.json')
        for scenario in SCENARIOS:
            for basis in BASES:
                expected_paths |= {f'{model}/{scenario}/{basis}/{name}' for name in ('replay.json','summary.json','source_guard_rejection.json')}
    require(RESULTS.is_dir() and not RESULTS.is_symlink(), 'exclusive actual output directory missing')
    inventory = {str(p.relative_to(RESULTS)):stat(p) for p in RESULTS.rglob('*') if p.is_file()}
    require(set(inventory) <= expected_paths, 'unregistered files/identities in new attempt')
    require(not any(p.is_symlink() for p in RESULTS.rglob('*')), 'new output contains symlink')
    if (RESULTS/'input_manifest.json').exists():
        require(reader.json(RESULTS/'input_manifest.json') == manifest, 'copied original manifest differs')
    protocol_item = contract['old_small_sources']['trading_comparison_protocol_v4.json']
    if (RESULTS/'comparison_protocol.json').exists():
        require(reader.json(RESULTS/'comparison_protocol.json') == decode(reader.pin(protocol_item)), 'copied original protocol differs')
    expected = [(m,s,b,NAMESPACE+':'+manifest['freeze_id']+':'+m+':'+s+':'+b) for m in MODELS for s in SCENARIOS for b in BASES]
    rows, ids, summaries = [], [], {}
    for model, scenario, basis, run_id in expected:
        destination = RESULTS/model/scenario/basis
        row = dict(model=model,scenario=scenario,account_basis=basis,expected_run_id=run_id,
            replay_present=(destination/'replay.json').exists(),summary_present=(destination/'summary.json').exists())
        if row['replay_present']:
            journal = reader.json(destination/'replay.json', JOURNAL_CAP)
            info = review_journal(journal, manifest['model_versions'][model], scenario, basis, run_id, metadata, overlay)
            ids.append(info['run_id']);row.update(info)
            row['replay_identity'] = reader.files[str(destination/'replay.json')]['identity']
            journal_status={'status':journal['status']}
            del journal
            if row['summary_present']:
                summary = reader.json(destination/'summary.json', JOURNAL_CAP)
                row['summary_windows'] = review_summary(summary, journal_status, basis, metadata)
                row['summary_identity'] = reader.files[str(destination/'summary.json')]['identity']
                core=dict(summary)
                core.pop('target_decimal_research_provenance');core.pop('full_PLAN_complete');core.pop('economic_target_accepted')
                summaries[(model,scenario,basis)] = core_hash(core)
                del core,summary
        else:
            require(not row['summary_present'], 'summary exists without its journal')
        if (destination/'source_guard_rejection.json').exists():
            rejection=reader.json(destination/'source_guard_rejection.json')
            require(rejection['status']=='REJECTED_CHANGED_SOURCE_BYTES' and rejection['journal_retained'] is True
                and rejection['comparison_values_accepted'] is False, 'source rejection scope differs')
            row['source_guard_rejection']=rejection
        rows.append(row)
    require(len(ids)==len(set(ids)), 'duplicate actual run IDs')
    expected_ids=[row[3] for row in expected]
    require(ids==expected_ids[:len(ids)], 'actual journals are not the declared ordered attempt prefix')
    begin=[line[6:] for line in stdout.splitlines() if line.startswith('BEGIN ')]
    end=[line[4:].rsplit(' ',1) for line in stdout.splitlines() if line.startswith('END ')]
    require(begin==expected_ids[:len(begin)] and len(begin)<=36, 'root original BEGIN journal is not the fixed ordered prefix')
    require([item[0] for item in end]==expected_ids[:len(end)] and len(end)<=len(begin), 'root original END journal is not the fixed ordered prefix')
    require(len(ids)<=len(begin),'actual journal count exceeds original flushed BEGIN prefix')
    require(len(end)<=len(summaries),'original END count exceeds actual written summaries')
    for index,(run_id,status) in enumerate(end):
        require(rows[index].get('status')==status and rows[index]['summary_present'], 'root original END status differs from actual artifacts')
    comparison_path=RESULTS/'comparison.json'
    returned_path=RESULTS/'research_namespace_receipt.json'
    failure_path=RESULTS/'new_namespace_failure_scope.json'
    complete_records=False
    if comparison_path.exists():
        comparison=read_comparison(reader,comparison_path,summaries)
        flags_false(comparison,'comparison');not_accepting_recursively(comparison,'comparison')
        require(comparison['target_decimal_research_provenance']==metadata and comparison['status']=='TARGET_SOURCE_BOUND_DECIMAL_RESEARCH_ATTEMPTS_RECORDED', 'comparison namespace/status differs')
        require(comparison['models']==list(MODELS) and comparison['model_versions']==manifest['model_versions']
            and comparison['freeze_id']==manifest['freeze_id'] and comparison['test_winner_selected'] is False
            and comparison['fixed_2024_selected_model']=='selected_full', 'original freeze/model selection changed')
        for key,item_name in (('comparison_protocol_sha256','trading_comparison_protocol_v4.json'),
                             ('source_seal_sha256','trading_comparison_source_seal_v4.json'),
                             ('driver_receipt_sha256','trading_comparison_driver_receipt_v4.json')):
            require(comparison[key]==contract['old_small_sources'][item_name]['sha256'], 'comparison old reference binding differs: '+key)
        require(comparison['input_manifest_sha256']==contract['original_manifest']['sha256'], 'comparison original input manifest SHA differs')
        require(set(comparison['artifacts'])==set(MODELS), 'comparison model groups differ')
        for index,(model,scenario,basis,run_id) in enumerate(expected):
            for outer in ('artifacts',):
                require(set(comparison[outer][model])==set(SCENARIOS)
                    and set(comparison[outer][model][scenario])==set(BASES), 'comparison scenario/account groups differ')
            record=comparison['artifacts'][model][scenario][basis];row=rows[index]
            require(row['replay_present'] and row['summary_present'], 'comparison omitted actual named files')
            require(record['replay_path']==str(RESULTS/model/scenario/basis/'replay.json')
                and record['replay_sha256']==row['replay_identity']['sha256']
                and record['summary_sha256']==row['summary_identity']['sha256'] and record['status']==row['status'], 'comparison journal/summary original byte references differ')
        complete_records=len(ids)==len(end)==len(begin)==36
    if returned_path.exists():
        returned=reader.json(returned_path)
        provenance(returned,contract,overlay,(old_ast,new_ast))
        require(returned['status']=='SOURCE_BOUND_RESEARCH_NAMESPACE_RUN_RETURNED_NOT_FINANCIAL_ACCEPTANCE'
            and returned['process_exit_independently_observed'] is False
            and returned['wrapper_capture_and_independent_review_still_required'] is True
            and returned['complete_actual_36_replays_not_inferred_from_function_return'] is True, 'function-return scope promoted')
        require(complete_records, 'function-return receipt lacks all36 original journal/summary files')
        status_by_key={(m,s,b):rows[i]['status'] for i,(m,s,b,_) in enumerate(expected)}
        status_map={m:{s:{b:status_by_key[(m,s,b)] for b in BASES} for s in SCENARIOS} for m in MODELS}
        require(complete_records and returned['actual_recorded_replay_statuses']==status_map, 'function-return actual statuses differ')
    if failure_path.exists():
        failure=reader.json(failure_path)
        provenance(failure,contract,overlay,(old_ast,new_ast))
        require(failure['status']=='ACTUAL_RESEARCH_RUN_ABORTED' and failure['original_partial_outputs_retained'] is True, 'failed scope/partial retention differs')
    successful=root_receipt['exit_code']==0
    require(root_receipt['output_receipt_exists']==returned_path.exists(), 'root receipt existence observation differs')
    if successful:
        require(complete_records and returned_path.exists() and not failure_path.exists(), 'successful process lacks36 recorded attempts and completion receipt')
        required_complete={'input_manifest.json','comparison_protocol.json','comparison.json','research_namespace_receipt.json'}
        required_complete.update(m+'_score_coverage.json' for m in MODELS)
        required_complete.update(f'{m}/{s}/{b}/{name}' for m in MODELS for s in SCENARIOS for b in BASES
            for name in ('replay.json','summary.json'))
        require(set(inventory)==required_complete, 'successful36 file roster is not the exact85 registered artifacts')
    else:
        require(not returned_path.exists() or complete_records, 'aborted process has unproven function-return claims')
    # Coverage files are metadata only; never open their referenced score CSVs.
    for model in MODELS:
        path=RESULTS/(model+'_score_coverage.json')
        if path.exists():
            coverage=reader.json(path,JOURNAL_CAP)
            not_accepting_recursively(coverage,'score coverage metadata')
    for filename, before in inventory.items():
        require(stat(RESULTS/filename)==before, 'actual attempt changed during review: '+filename)
    require(set(inventory)=={str(p.relative_to(RESULTS)) for p in RESULTS.rglob('*') if p.is_file()}, 'actual file inventory changed during review')
    actual_identities=reader.unchanged()
    diagnostic_complete=sum(row.get('status')=='COMPLETE_DIAGNOSTIC_ONLY' for row in rows)
    return dict(schema_version=1,status='PASS_READONLY_ACTUAL_36_ATTEMPTS_RECORDED_NOT_FINANCIAL_ACCEPTANCE' if successful else 'PASS_READONLY_FAILED_OR_PARTIAL_ATTEMPT_EVIDENCE_NOT_FINANCIAL_ACCEPTANCE',
        namespace=NAMESPACE, observed_utc=datetime.now(timezone.utc).isoformat(), process_exit_code=root_receipt['exit_code'],
        actual_process_success=successful, actual_all36_recorded=complete_records,
        expected_attempt_count=36, actual_journal_count=len(ids), actual_summary_count=len(summaries),
        complete_diagnostic_journal_count=diagnostic_complete, incomplete_journal_count=sum(row.get('status')=='INCOMPLETE' for row in rows),
        missing_journal_count=36-len(ids), actual_rows=rows,
        actual_target_overlay_event_count=sum(row.get('overlay_event_count',0) for row in rows),
        actual_target_queue_zero_fill_event_count=sum(len(row.get('target_queue_zero_fill_events',[])) for row in rows),
        missing_target_queue_event_is_unknown_not_zero_fill_proof=True,
        original_log_BEGIN_count=len(begin),original_log_END_count=len(end),actual_file_identities=actual_identities,
        closed_capture_map_review=closed_maps,
        captured_original_large_SHA_scope='Inherited root map; this inspector opens no old arrays, checkpoints, score CSVs, minute archive or old journals',
        captured_before_after_maps_separately_hashed_by_this_review_not_falsely_claimed_as_root_receipt_hash_fields=True,
        exact_four_window_primary_numeric_proof_rechecked_from_small_saved_evidence=True,
        original_payload_SHA_rechecked_without_decoding_or_rescanning_source=True,
        same_path_net_bounds_or_nested_diagnostic_eligibility_do_not_clear_fraction_delisting_or_unknown_tax=True,
        unresolved_scope=['Delisting cash-choice/payout evidence','Fractional-share disposal and exact cash-in-lieu',
            'Unresolved noncash FIFO/tax and CDR instrument qualifications when actually exposed','Original arrival-assumption and unobserved executable depth'],
        economic_target_accepted=False, full_PLAN_complete=False, all_PLAN_acceptance_complete=False,
        actual_read_bytes=reader.read_bytes, no_engine_provider_models_old_inspector_tests_or_network_executed=True)


def write_new(path,value):
    with Path(path).open('x') as stream:
        stream.write(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review',action='store_true')
    parser.add_argument('--results',type=Path)
    parser.add_argument('--wrapper-receipt',type=Path)
    parser.add_argument('--wrapper-sha')
    parser.add_argument('--preparation-sha')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not args.review:
        require(not any((args.results,args.wrapper_receipt,args.wrapper_sha,args.preparation_sha,args.output)), 'explicit actual review required for file arguments')
        print(json.dumps(pure_contract(),ensure_ascii=False,sort_keys=True))
        return
    require(all((args.results,args.wrapper_receipt,args.wrapper_sha,args.preparation_sha,args.output)), 'actual review requires every explicit identity/output argument')
    output=args.output.resolve()
    require(output.is_relative_to(REVIEW_ROOT) and output!=REVIEW_ROOT and not output.exists(), 'new append-only review output required')
    require(not REVIEW_ROOT.is_symlink(), 'review root cannot be a symlink')
    output.mkdir(parents=True,exist_ok=False)
    try:
        result=run_review(args,output)
    except Exception as error:
        write_new(output/'actual_result_review_rejection_v1.json',dict(schema_version=1,status='REJECTED_READONLY_EVIDENCE_NO_ACCEPTANCE',
            namespace=NAMESPACE,observed_utc=datetime.now(timezone.utc).isoformat(),error_type=type(error).__name__,reason=str(error),
            requested_results=str(args.results),requested_root_completion_receipt=str(args.wrapper_receipt),
            externally_expected_root_receipt_sha256=args.wrapper_sha,externally_expected_preparation_sha256=args.preparation_sha,
            original_partial_artifacts_preserved=True,automatic_retry=False,economic_target_accepted=False,
            full_PLAN_complete=False,all_PLAN_acceptance_complete=False))
        traceback.print_exc()
        raise SystemExit(1)
    write_new(output/'actual_result_review_receipt_v1.json',result)
    print(json.dumps(dict(status=result['status'],receipt=str(output/'actual_result_review_receipt_v1.json'),
        actual_all36_recorded=result['actual_all36_recorded'],complete_diagnostic_journal_count=result['complete_diagnostic_journal_count'],
        economic_target_accepted=False,full_PLAN_complete=False),ensure_ascii=False))


if __name__=='__main__':
    main()
