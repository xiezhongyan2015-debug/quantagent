"""Versioned final-only handling of two observed report serialization shapes.

The original model response is never changed. An opted-in parse is strict first.
V1 permits deterministic literal-LF escaping. V2 additionally extracts the
claims array from an exact one-level envelope, optionally carrying the known
claim contract version. All claims survive in order and ordinary report
validation still applies. This module grants no call,
submission, evidence acceptance or recovery of an old failed ledger action.
"""
import hashlib
import json
import math

from .ledger import need


VERSION = 'report_lf_escape_v1'
STRUCTURED_VERSION = 'report_structured_args_v2'


def validate_policy(policy):
    need(policy is None or policy in ({'version': VERSION}, {'version': STRUCTURED_VERSION}),
         'unknown report argument policy')
    return policy


def _strict(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f'Non-finite JSON value: {value}')

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def _finite(value):
    if type(value) is float:
        need(math.isfinite(value), 'nonfinite decoded report number')
    elif type(value) is list:
        for item in value:
            _finite(item)
    elif type(value) is dict:
        for item in value.values():
            _finite(item)


def _escape_lf(text):
    inside = escaped = False
    output, positions = [], []
    for index, char in enumerate(text):
        if inside and ord(char) < 32:
            need(char == '\n' and not escaped, 'only unescaped LF inside report JSON strings may be normalized')
            output.append('\\n')
            positions.append(index)
            continue
        output.append(char)
        if inside:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                inside = False
        elif char == '"':
            inside = True
    need(positions, 'no permitted report LF normalization found')
    return ''.join(output), positions


def decode(response, policy=None):
    """Return ``(decoded_arguments, proof_or_none)`` without writing anything.

    No policy retains plain json.loads semantics for legacy binding callers;
    execution callers retain their own pre-existing argument checks. With the
    policy, only submit_research_report may normalize LF; V2 can also extract
    the known claims envelope. Proofs cover deterministic argument decoding,
    never acceptance; callers still validate all report fields and references.
    """
    validate_policy(policy)
    text = response['arguments_json']
    if policy is None:
        return json.loads(text), None
    need(type(text) is str and len(text) <= 64000, 'bounded argument JSON text required')
    enabled = policy is not None and response['action'] == 'submit_research_report'
    normalized, positions = text, []
    try:
        args = _strict(text)
    except json.JSONDecodeError:
        if not enabled:
            raise
        normalized, positions = _escape_lf(text)
        args = _strict(normalized)
    need(type(args) is dict, 'arguments must decode to an object')
    if not enabled:
        return args, None
    _finite(args)
    claims_proof = None
    if policy['version'] == STRUCTURED_VERSION and type(args.get('claims')) is dict:
        from .claim_support import VERSION as claim_version
        wrapped = args['claims']
        need((set(wrapped) == {'claims'} or
              (set(wrapped) == {'claims', 'version'} and wrapped['version'] == claim_version))
             and type(wrapped['claims']) is list
             and 1 <= len(wrapped['claims']) <= 16, 'unsupported claims envelope')
        # The wrapper and optional verified version are transport metadata. Preserve every item,
        # its ordering and content, then run the unchanged domain validator.
        canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False)
        sha_value = lambda value: hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()
        claims_proof = {'kind': 'exact_single_claims_envelope_v1',
            'original_envelope_sha256': sha_value(wrapped),
            'extracted_claims_sha256': sha_value(wrapped['claims']),
            'verified_envelope_version': wrapped.get('version'),
            'claim_count': len(wrapped['claims']), 'claim_items_modified': False,
            'discarded_claims': 0, 'added_claims': 0}
        args = {**args, 'claims': wrapped['claims']}
    sha = lambda value: hashlib.sha256(value.encode('utf-8')).hexdigest()
    proof = {'version': policy['version'], 'action': response['action'],
        'mode': 'literal_lf_escaped' if positions else 'strict_json',
        'original_text_sha256': sha(text), 'normalized_text_sha256': sha(normalized),
        'original_characters': len(text), 'normalized_characters': len(normalized),
        'changed_character_indices': positions, 'index_unit': 'zero-based Unicode code points',
        'changed_count': len(positions), 'original_response_modified': False,
        'transformation': 'Only literal LF inside JSON strings becomes backslash-n; all other text is unchanged.',
        'report_domain_and_evidence_validation_required': True}
    if policy['version'] == STRUCTURED_VERSION:
        proof.update(claims_envelope_normalization=claims_proof,
            decoded_arguments_sha256=sha(json.dumps(args, ensure_ascii=False, sort_keys=True,
                separators=(',', ':'), allow_nan=False)),
            normalized_text_scope='JSON text after LF handling, before optional claims envelope extraction')
    return args, proof
