"""Deterministic bounded context. Full source evidence is never overwritten."""
import json

from .ledger import digest, serial


def _unique_arguments_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate argument key has no unambiguous source span')
        value[key] = item
    return value


def _invalid_argument_constant(value):
    raise ValueError('Nonfinite argument constant: ' + value)


def bounded(value, maximum=8000):
    encoded = serial(value).encode("utf-8")
    if len(encoded) <= maximum:
        return value
    # An excerpt is explicitly text, never presented as a complete JSON record.
    room = maximum - 1200
    while True:
        first = encoded[:room * 2 // 3].decode("utf-8", errors="ignore")
        last = encoded[-room // 3:].decode("utf-8", errors="ignore")
        out = {"context_projection": "incomplete source excerpt; omitted middle remains in original",
               "original_sha256": digest(value), "original_bytes": len(encoded),
               "first_excerpt": first, "last_excerpt": last,
               "caution": "Do not infer missing trades, fees or events are absent. Consult original evidence pages; if unavailable, state uncertainty."}
        if len(serial(out).encode("utf-8")) <= maximum:
            return out
        room = room * 3 // 4


def history_context(history):
    return [{"id": x["id"], "status": x["status"],
             "response": bounded(x["response"]), "result": bounded(x["result"])} |
            ({'original_application_result':bounded(x['original_application_result']),
              'saved_result_recovery':x['saved_result_recovery']}
             if 'saved_result_recovery' in x else {}) for x in history]


def working_evidence_index(history, *, allowed_actions=None, statement_bytes=1800,
                           projection='omitted_only'):
    """Opt-in additive index of explicit public statements and saved evidence.

    This is neither a replacement for history_context nor an inferred research
    state. Statements are copied from named fields, with hashes and explicit
    excerpts when needed. Old contradictions, failures and superseded statements
    remain in chronological order; the researcher must judge their meaning.
    Byte counts describe the projection, not tokens, billing or research quality.
    The default indexes only source objects intersecting an actual legacy
    omission. projection='full' preserves the original additive index API.
    """
    if projection not in ('full', 'omitted_only'):
        raise ValueError("projection must be full or omitted_only")
    if type(statement_bytes) is not int or not 1600 <= statement_bytes <= 8000:
        raise ValueError("statement_bytes must be an integer from 1600 to 8000")
    rows = []
    for entry in history:
        response, result = entry.get('response'), entry.get('result')
        row = {'id': entry['id'], 'status': entry['status'],
               'response_sha256': digest(response), 'result_sha256': digest(result),
               'statements': [], 'evidence_references': [], 'page_queries': []}

        def statement(path, value):
            projected = bounded(value, statement_bytes)
            row['statements'].append({'source_path': path, 'sha256': digest(value),
                'complete': projected == value, 'value': projected})

        if isinstance(response, dict):
            row['action'] = response.get('action')
            if 'public_summary' in response:
                statement('/response/public_summary', response['public_summary'])
            review = response.get('self_review')
            if isinstance(review, dict):
                for key in ('assessment', 'uncertainty', 'next_step', 'falsifier'):
                    if key in review:
                        statement('/response/self_review/' + key, review[key])
            try:
                args = json.loads(response.get('arguments_json', '{}'),
                    object_pairs_hook=_unique_arguments_object, parse_constant=_invalid_argument_constant)
            except (TypeError, ValueError):
                args = None
            if not isinstance(args, dict):
                row['arguments_index_status'] = 'unparsed; original response retained'
            else:
                # These are existing researcher declarations, not controller
                # hypotheses or an automatic verdict that a falsifier occurred.
                for key in ('mechanism_hypotheses', 'distinguishing_prediction',
                            'success_rule', 'failure_rule', 'falsifiers',
                            'limitations', 'problem', 'expected_information_gain',
                            'selection_rule', 'stop_rule', 'comparisons'):
                    if key in args:
                        statement('/response/arguments_json#/' + key, args[key])
                if row['action'] == 'register_batch' and isinstance(args.get('families'), list):
                    for offset, family in enumerate(args['families']):
                        if isinstance(family, dict):
                            for key in ('id', 'mechanism_status', 'mechanism'):
                                if key in family:
                                    statement('/response/arguments_json#/families/' +
                                              str(offset) + '/' + key, family[key])
                for key in ('evidence_ids', 'baseline_evidence_id',
                            'diagnosis_evidence_id', 'program_evidence_id'):
                    if key in args:
                        row['evidence_references'].append({
                            'source_path': '/response/arguments_json#/' + key,
                            'value': args[key]})
        if 'saved_result_recovery' in entry:
            row['saved_result_recovery'] = entry['saved_result_recovery']
            row['original_application_result_sha256'] = digest(entry.get('original_application_result'))
            statement('/original_application_result', entry.get('original_application_result'))
        if isinstance(result, dict):
            for key in ('error', 'execution_valid', 'formal_target_success'):
                if key in result:
                    statement('/result/' + key, result[key])
            public = result.get('public')
            if isinstance(public, dict):
                for key in ('error', 'detail_query', 'all_candidate_results_query'):
                    if key in public:
                        statement('/result/public/' + key, public[key])
                # Every attribution account gets a page, irrespective of return
                # or completion. The full record may fall in an omitted middle.
                if (result.get('action') == 'diagnose_execution'
                        and result.get('evidence_id') == entry['id']
                        and result.get('artifact_hash')
                        and isinstance(public.get('accounts'), list)):
                    for offset, account in enumerate(public['accounts']):
                        if not isinstance(account, dict):
                            continue
                        row['page_queries'].append({
                            'source_path': '/result/public/accounts/' + str(offset),
                            'account_evidence_id': account.get('evidence_id'),
                            'source_sha256': digest(account),
                            'action': 'read_evidence',
                            'arguments': {'evidence_id': entry['id'],
                                'table': 'attribution_accounts', 'offset': offset, 'limit': 1},
                            'currently_allowed': None if allowed_actions is None else
                                'read_evidence' in allowed_actions})
        rows.append(row)
    full = {'version': 'explicit_working_evidence_v1',
            'semantics': 'Additive field index, not inferred or reconciled research state. '
                'Statements are researcher declarations or copied tool fields, not verified claims. '
                'All source IDs and failure statuses remain. Excerpts are incomplete; originals prevail. '
                'A page query is only a hint and grants no tool, scope or budget permission. '
                'arguments_json#/ paths refer to fields after decoding that JSON string. '
                'No model quality acceptance or token-cost saving has been established.',
            'history_sha256': digest(history), 'rows': rows}
    return full if projection == 'full' else _omitted_index(history, full)


def _canonical_span(value, path):
    """Byte interval of one original object in ledger.serial, not word matching."""
    start = 0
    for key in path:
        start += 1  # Opening object/array delimiter.
        if isinstance(value, dict):
            for name in sorted(value):
                start += len(serial(name).encode('utf-8')) + 1
                if name == key:
                    value = value[name]
                    break
                start += len(serial(value[name]).encode('utf-8')) + 1
            else:
                raise KeyError(key)
        else:
            index = int(key)
            start += sum(len(serial(item).encode('utf-8')) + 1 for item in value[:index])
            value = value[index]
    return start, start + len(serial(value).encode('utf-8'))


def _json_text_span(text, path):
    """Character interval in an arguments_json string, preserving its spacing."""
    decoder = json.JSONDecoder()

    def whitespace(index):
        while index < len(text) and text[index].isspace():
            index += 1
        return index

    start = whitespace(0)
    value, end = decoder.raw_decode(text, start)
    for wanted in path:
        cursor = whitespace(start + 1)
        offset = 0
        while cursor < end:
            if isinstance(value, dict):
                key, cursor = decoder.raw_decode(text, cursor)
                cursor = whitespace(whitespace(cursor) + 1)  # Colon.
            else:
                key = str(offset)
            child, stop = decoder.raw_decode(text, cursor)
            if str(key) == wanted:
                start, end, value = cursor, stop, child
                break
            cursor = whitespace(whitespace(stop) + 1)  # Comma.
            offset += 1
        else:
            raise KeyError(wanted)
    return start, end


def _source_span(entry, source_path):
    ordinary, separator, nested = source_path.partition('#/')
    parts = ordinary.lstrip('/').split('/')
    source, path = parts[0], parts[1:]
    start, end = _canonical_span(entry[source], path)
    if separator:
        # Map the decoded declaration's exact span back through JSON string
        # escaping into the response's canonical byte offsets.
        text = entry[source]['arguments_json']
        first, last = _json_text_span(text, nested.split('/'))
        prefix = lambda offset: len(serial(text[:offset]).encode('utf-8')) - 2
        start, end = start + 1 + prefix(first), start + 1 + prefix(last)
    return source, start, end


def _omitted_index(history, full):
    rows = []
    for entry, original in zip(history, full['rows']):
        omitted = {}
        sources = ['response', 'result']
        if 'saved_result_recovery' in entry:
            sources.append('original_application_result')
        for source in sources:
            value = entry.get(source)
            size = len(serial(value).encode('utf-8'))
            if size > 8000:
                excerpt = bounded(value)
                omitted[source] = {'source_path': '/' + source,
                    'original_sha256': digest(value), 'original_bytes': size,
                    'omitted_byte_interval': [len(excerpt['first_excerpt'].encode('utf-8')),
                        size - len(excerpt['last_excerpt'].encode('utf-8'))]}
        if not omitted:
            continue

        def missing(item):
            source, start, end = _source_span(entry, item['source_path'])
            if source not in omitted:
                return False
            first, last = omitted[source]['omitted_byte_interval']
            return start < last and end > first

        row = {'id': original['id'], 'status': original['status'],
               'action': original.get('action'), 'truncated_sources': list(omitted.values())}
        for field in ('statements', 'evidence_references', 'page_queries'):
            selected = [item for item in original[field] if missing(item)]
            if selected:
                row[field] = selected
        # No listed field is evidence of completeness: unknown omitted content
        # can remain even when this narrow index has no retrieval hint for it.
        if 'saved_result_recovery' in original:
            row['saved_result_recovery'] = original['saved_result_recovery']
            row['original_application_result_sha256'] = original['original_application_result_sha256']
        if 'arguments_index_status' in original and 'response' in omitted:
            row['arguments_index_status'] = original['arguments_index_status']
        rows.append(row)
    return {'version': 'omitted_working_evidence_v1', 'projection': 'omitted_only',
        'history_sha256': full['history_sha256'], 'rows': rows,
        'semantics': 'Additive index of selected source objects intersecting legacy omitted byte ranges. '
            'Fully retained objects are not repeated; no keyword matching or conclusion inference. '
            'Other omitted content remains unknown; this index does not restore complete history. '
            'All original actions, statuses and failures remain in the accompanying history. '
            'Declarations are copied, not verified or reconciled. Queries grant no permission. '
            'arguments_json#/ paths refer to the decoded JSON string. Model quality is unverified.'}
