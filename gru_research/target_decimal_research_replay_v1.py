"""Explicit new research namespace over the unchanged sealed V4 replay AST.

Default emits a pure contract. Execution requires the original manifest, an
exclusive new output directory, prepared source-bound overlay, and explicit
--execute. Author preparation never runs provider/replay/models/inspection.
"""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from target_decimal_quote_overlay_v1 import (ROOT, HERE, NAMESPACE, TARGET_SYMBOL, TARGET_DATE,
    read_contract, read_pin, guard, build_overlay, semantic_identity, make_source_class, require)

DRIVER = HERE / 'trading_comparison_v4.py'
DRIVER_SHA = 'ce3ae7c4ac18f6022abecb93287905556ea1d410716a83d554f21049c24373eb'
OUTPUT_ROOT = HERE / 'artifacts/target_decimal_research_replay_v1'


def write_new(path, payload):
    with Path(path).open('x') as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def namespace_ast(source):
    tree = ast.parse(source)
    original = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run_manifest')
    original_dump = ast.dump(original, include_attributes=False)
    assignments = [node for node in ast.walk(original) if isinstance(node, ast.Assign)
                   and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'run_id']
    require(len(assignments) == 1, 'original run_id assignment roster differs')
    assignment = assignments[0]
    assignment.value = ast.BinOp(left=ast.Constant(NAMESPACE + ':'), op=ast.Add(), right=assignment.value)
    ast.fix_missing_locations(original)
    return original, original_dump


def run(args):
    require(args.execute is True, 'actual replay requires explicit --execute')
    contract = read_contract(args.contract_file, args.contract_sha)
    require(str(Path(args.manifest).resolve()) == contract['original_manifest']['path'], 'original manifest identity path differs')
    pinned_manifest = json.loads(read_pin(args.manifest, contract['original_manifest']['sha256'], contract['original_manifest']['bytes']))
    require(pinned_manifest['models'] == contract['models'] and len(pinned_manifest['model_versions']) == 9,
            'original nine frozen model/version identities required')
    body = read_pin(args.overlay, args.overlay_sha, 100000)
    overlay = json.loads(body)
    # Do not trust prepared decimal strings without revalidating the actual source.
    actual_overlay = build_overlay(contract, args.contract_file, args.contract_sha)
    require(overlay == actual_overlay and overlay['overlay_id'] == semantic_identity(overlay), 'prepared overlay differs from actual sources')
    output = Path(args.output).resolve()
    require(output.is_relative_to(OUTPUT_ROOT.resolve()) and output != OUTPUT_ROOT.resolve()
            and not output.exists(), 'exclusive new target-decimal research output namespace required')
    original_body = read_pin(DRIVER, DRIVER_SHA, 100000)
    source = original_body.decode()
    amended, original_dump = namespace_ast(source)
    # Only explicit execution imports the old replay module; no globals are patched.
    sys.path.insert(0, str(HERE))
    import trading_comparison_v4 as old
    check_guard = lambda: guard(contract, args.contract_file, args.contract_sha, args.overlay, args.overlay_sha)
    check_guard()
    metadata = dict(namespace=NAMESPACE, target_symbol=TARGET_SYMBOL, target_date=TARGET_DATE,
        overlay_id=overlay['overlay_id'], overlay_path=str(Path(args.overlay).resolve()), overlay_sha256=args.overlay_sha,
        research_contract_path=str(Path(args.contract_file).resolve()), research_contract_sha256=args.contract_sha,
        original_manifest_path=str(Path(args.manifest).resolve()), original_manifest_sha256=contract['original_manifest']['sha256'],
        original_driver_sha256=DRIVER_SHA, original_run_AST_sha256=hashlib.sha256(original_dump.encode()).hexdigest(),
        adapted_run_AST_sha256=hashlib.sha256(ast.dump(amended, include_attributes=False).encode()).hexdigest(),
        AST_difference='Only prefix the one run_id expression with the new research namespace',
        local_global_bindings_overridden=['SealedMinuteSource', 'write_new'],
        actual_quote_sources=overlay['source_pins'], new_source_roster=contract['new_sources'],
        old_V4_seal_fields_bind_unchanged_old_files_only=True,
        new_decimal_quote_not_represented_as_old_window_raw_price=True,
        original_module_globals_modified=False, quote_change_scope='Only raw_price for one stockday/four source-bound windows',
        original_rule_cost_order_score_model_and_company_event_logic_unchanged=True,
        actual_fraction_and_delisting_unknown_not_assumed=True,
        economic_target_accepted=False, all_PLAN_acceptance_complete=False, full_PLAN_complete=False)
    guarded_source = make_source_class(old.SealedMinuteSource, overlay, check_guard)
    def research_writer(path, payload):
        check_guard()
        require(Path(path).resolve().is_relative_to(output), 'new writer cannot publish outside its exclusive research subtree')
        filename = Path(path).name
        if filename == 'input_manifest.json':
            require(payload == pinned_manifest, 'original input manifest copy changed')
        if filename in ('replay.json', 'summary.json', 'comparison.json'):
            payload = dict(payload, target_decimal_research_provenance=metadata)
            payload['economic_target_accepted'] = False
            payload['all_PLAN_acceptance_complete'] = False
            payload['full_PLAN_complete'] = False
            if filename == 'comparison.json':
                payload['status'] = 'TARGET_SOURCE_BOUND_DECIMAL_RESEARCH_ATTEMPTS_RECORDED'
            if filename == 'replay.json':
                payload['target_decimal_overlay_events'] = [access['target_decimal_quote_overlay']
                    for access in payload.get('sealed_minute_source_accesses', []) if 'target_decimal_quote_overlay' in access]
        old.write_new(path, payload)
    env = dict(vars(old))
    env['SealedMinuteSource'] = guarded_source
    env['write_new'] = research_writer
    exec(compile(ast.Module(body=[amended], type_ignores=[]), str(Path(__file__)), 'exec'), env)
    started = datetime.now(timezone.utc).isoformat()
    # One actual attempt for this registered research namespace, including failures.
    # A different output path never authorizes another engine attempt.
    require(not OUTPUT_ROOT.is_symlink(), 'research output root cannot be a symlink')
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    write_new(OUTPUT_ROOT / 'EXECUTION_STARTED_v1.json', dict(metadata,
        started_utc=started, requested_output=str(output),
        status='ACTUAL_NAMESPACE_ATTEMPT_MARKED_BEFORE_REPLAY_CALL',
        completed=False, restart_with_other_output_authorized=False))
    # Original function owns exclusive output creation and every old guard/loop.
    try:
        result = env['run_manifest'](args.manifest, output)
    except BaseException:
        # Preserve partial journals; never repair status or retry another basis.
        if output.is_dir():
            try:
                write_new(output / 'new_namespace_failure_scope.json', dict(metadata, status='ACTUAL_RESEARCH_RUN_ABORTED',
                    started_utc=started, observed_utc=datetime.now(timezone.utc).isoformat(), original_partial_outputs_retained=True))
            except Exception as failed_write:
                print('Failure scope could not be published: ' + type(failed_write).__name__, file=sys.stderr, flush=True)
        raise
    check_guard()
    write_new(output / 'research_namespace_receipt.json', dict(metadata,
        status='SOURCE_BOUND_RESEARCH_NAMESPACE_RUN_RETURNED_NOT_FINANCIAL_ACCEPTANCE',
        started_utc=started, returned_utc=datetime.now(timezone.utc).isoformat(),
        actual_recorded_replay_statuses={model:{scenario:{basis:record['status'] for basis,record in groups.items()}
            for scenario,groups in scopes.items()} for model,scopes in result['artifacts'].items()},
        process_exit_independently_observed=False, wrapper_capture_and_independent_review_still_required=True,
        complete_actual_36_replays_not_inferred_from_function_return=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command')
    subs.add_parser('contract')
    prepare = subs.add_parser('prepare')
    for command in (prepare,):
        command.add_argument('--contract-file', type=Path, required=True)
        command.add_argument('--contract-sha', required=True)
        command.add_argument('--output', type=Path, required=True)
    execute = subs.add_parser('run')
    execute.add_argument('--execute', action='store_true')
    execute.add_argument('--contract-file', type=Path, required=True)
    execute.add_argument('--contract-sha', required=True)
    execute.add_argument('--overlay', type=Path, required=True)
    execute.add_argument('--overlay-sha', required=True)
    execute.add_argument('--manifest', type=Path, required=True)
    execute.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command in (None, 'contract'):
        print(json.dumps(dict(status='PREPARED_ONLY_NO_PROVIDER_OR_ENGINE_EXECUTED', namespace=NAMESPACE,
            expected_research_combinations=36, default_reads_old_files=False, default_creates_outputs=False,
            original_seals_and_frozen_driver_preserved=True, economic_target_accepted=False, full_PLAN_complete=False)))
    elif args.command == 'prepare':
        contract = read_contract(args.contract_file, args.contract_sha)
        require(not args.output.exists(), 'exclusive overlay output required')
        write_new(args.output, build_overlay(contract, args.contract_file, args.contract_sha))
    else:
        run(args)


if __name__ == '__main__':
    main()
