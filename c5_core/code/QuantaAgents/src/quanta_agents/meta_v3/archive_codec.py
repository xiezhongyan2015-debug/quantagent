"""Opt-in, byte-exact bounded record encoding; no IO or execution authority."""
import hashlib
import re
import zlib

VERSION = 'zlib_records_v1'
MAX_DECODED_RECORD = 64 * 1024**2
FIELDS = {'codec', 'payload', 'stored_bytes', 'stored_sha256', 'decoded_bytes', 'decoded_sha256'}


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def validate_policy(policy):
    _require(policy is None or type(policy) is dict and policy == {'version': VERSION},
             'unknown archive storage policy')


def case_policy(case):
    if 'archive_storage_policy' not in case:
        return None
    policy = case['archive_storage_policy']
    _require(policy is not None, 'explicit archive storage policy cannot be null')
    validate_policy(policy)
    _require(case.get('research_class') == 'real_saved_development' and
             case.get('execution_backend') == 'v3_streamed_001',
             'archive storage policy requires real streamed execution')
    return dict(policy)


def stored_limit(decoded_limit):
    _require(type(decoded_limit) is int and 0 <= decoded_limit <= MAX_DECODED_RECORD,
             'bounded archive decoded limit required')
    # Deliberately generous fixed cap, including an empty zlib stream. This is
    # only a decoder memory/input bound, never an extra raw research allowance.
    return 2 * decoded_limit + 1024


def encode_record(raw_utf8, policy, *, decoded_limit):
    validate_policy(policy)
    _require(policy is not None, 'record encoding requires explicit archive policy')
    cap = stored_limit(decoded_limit)
    _require(type(raw_utf8) is bytes and len(raw_utf8) <= decoded_limit, 'archive decoded record exceeds limit')
    payload = zlib.compress(raw_utf8, 6)
    _require(len(payload) <= cap, 'archive stored record exceeds limit')
    return {'codec': VERSION, 'payload': payload, 'stored_bytes': len(payload),
            'stored_sha256': hashlib.sha256(payload).hexdigest(), 'decoded_bytes': len(raw_utf8),
            'decoded_sha256': hashlib.sha256(raw_utf8).hexdigest()}


def decode_record(stored, policy, *, decoded_limit):
    validate_policy(policy)
    _require(policy is not None, 'record decoding requires explicit archive policy')
    cap = stored_limit(decoded_limit)
    _require(type(stored) is dict and set(stored) == FIELDS and stored['codec'] == VERSION,
             'exact archive record encoding required')
    _require(type(stored['decoded_bytes']) is int and 0 <= stored['decoded_bytes'] <= decoded_limit,
             'archive decoded record exceeds limit')
    cap = min(cap, stored_limit(stored['decoded_bytes']))
    _require(type(stored['stored_bytes']) is int and 0 <= stored['stored_bytes'] <= cap and
             type(stored['payload']) is bytes and len(stored['payload']) == stored['stored_bytes'],
             'archive stored size differs or exceeds limit')
    _require(all(type(stored[k]) is str and re.fullmatch('[a-f0-9]{64}', stored[k])
                 for k in ('stored_sha256', 'decoded_sha256')), 'archive hash schema invalid')
    _require(hashlib.sha256(stored['payload']).hexdigest() == stored['stored_sha256'], 'archive stored hash differs')
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(stored['payload'], stored['decoded_bytes'] + 1)
    except zlib.error as exc:
        raise ValueError('archive compressed stream invalid') from exc
    _require(len(raw) == stored['decoded_bytes'] and decoder.eof and not decoder.unconsumed_tail
             and not decoder.unused_data, 'archive length, incomplete stream, or trailing data differs')
    _require(hashlib.sha256(raw).hexdigest() == stored['decoded_sha256'], 'archive decoded hash differs')
    return raw
