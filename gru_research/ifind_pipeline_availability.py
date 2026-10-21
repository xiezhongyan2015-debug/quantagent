"""Read-only original iFinD pipeline availability check; never imports its worker.

No credential value is printed/copied, no login/logout/market API is called, and
no producer queue is changed. Public documentation is fetched once over TLS.
"""
import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import urllib.request

ROOT=Path(__file__).resolve().parent.parent
DAILY=ROOT/'中证500_历年成分股_日频_2010-2026_iFinD_后复权'
HERE=ROOT/'gru_research'
DATES=['2026-09-24','2026-09-28','2026-09-29','2026-09-30']
PUBLIC_MANUAL='https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/manual.html'


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path,data):
    with Path(path).open('x') as stream:
        json.dump(data,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')


def inspect(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    scripts=DAILY/'scripts_backup'
    pipeline=scripts/'download_ifind_csi500_1min.py'
    tree=ast.parse(pipeline.read_text());configuration={}
    for node in tree.body:
        if isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id in ['FIELDS','PARAMS','LABELS']:
                    configuration[target.id]=ast.literal_eval(node.value)
    worker=scripts/'ifind_api_worker.py';tree=ast.parse(worker.read_text())
    authentication=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Call) and ast.unparse(node.func)=='api.THS_iFinDLogin':
            authentication.append(dict(line=node.lineno,method='api.THS_iFinDLogin',
                authentication_origin='GUI_USERNAME_AND_PASSWORD_ENTRY_GETTERS',
                arguments_contain_nonempty_literal_string=any(isinstance(a,ast.Constant)
                    and isinstance(a.value,str) and bool(a.value) for a in node.args)))
    paths=[]
    for parent in [ROOT,DAILY,ROOT/'中证500_历年成分股_基本面_2010-2026_iFinD_PIT']:
        for relative in ['data_expansion/ifind_1min_hfq_control/worker_status.json',
            'data_expansion/ifind_1min_hfq_control/runtime_config.json',
            'data_expansion/ifind_daily_hfq_control/worker_status.json']:
            path=parent/relative;record=dict(path=str(path),exists=path.is_file())
            if path.is_file():
                obj=json.loads(path.read_text());record['keys_only']=sorted(obj)
                record['safe_status']=obj.get('status');record['sha256']=digest(path)
            paths.append(record)
    snapshot=DAILY/'audit/runtime_config_at_setup.json'
    obj=json.loads(snapshot.read_text())
    runtime=dict(platform=platform.system(),python=sys.executable,
        native_ifind_module_available=importlib.util.find_spec('iFinDPy') is not None,
        hithink_finance_cli_available=shutil.which('hithink-finance') is not None,
        related_environment_variable_names_only=[k for k in os.environ
            if any(s in k.upper() for s in ['IFIND','HITHINK','THS_'])])
    documentation=dict(url=PUBLIC_MANUAL,requested_at_utc=datetime.now(timezone.utc).isoformat())
    try:
        request=urllib.request.Request(PUBLIC_MANUAL,headers={'User-Agent':'Mozilla/5.0 market-source-readonly-audit'})
        with urllib.request.urlopen(request,timeout=20) as response:
            body=response.read(3*1024*1024+1)
            if len(body)>3*1024*1024:raise ValueError('public manual exceeds bounded read')
            path=output/'public_ifind_manual.html'
            with path.open('xb') as stream:stream.write(body)
            documentation.update(status=response.status,bytes=len(body),sha256=digest(path),
                response_date=response.headers.get('Date'),content_type=response.headers.get('Content-Type'),
                source_is_public_documentation_not_authenticated_market_response=True)
    except Exception as exc:
        documentation.update(status='PUBLIC_DOCUMENTATION_FETCH_FAILED',error_type=type(exc).__name__)
    source_hashes={str(p):digest(p) for p in [pipeline,worker,scripts/'ifind_parallel_dispatch.py',
        scripts/'ifind_daily_worker.py',scripts/'setup_ifind_daily.py',snapshot]}
    result=dict(status='PENDING_SOURCE_LOCAL_AUTHENTICATED_PIPELINE_NOT_AVAILABLE',
        inspected_at_utc=datetime.now(timezone.utc).isoformat(),runtime=runtime,source_hashes=source_hashes,
        original_pipeline_configuration=configuration,authentication_structure=authentication,
        related_control_file_availability=paths,
        persisted_runtime_snapshot=dict(path=str(snapshot),keys_only=sorted(obj),
            authentication_keys_present=[k for k in obj if re.search(r'token|secret|password|credential|username',k,re.I)]),
        original_process_platform_evidence='E_DRIVE_OUTPUT_GUARD_AND_MSVCRT_LOCK_AND_D_ANACONDA_PYTHONW_LAUNCH',
        local_auth_not_available_does_not_prove_original_account_expired=True,
        original_gui_worker_not_started_or_imported=True,market_api_calls=0,
        source_success_empty=False,source_failed_response=False,provider_market_availability_unverified=True,
        single_symbol='600004.SH',requested_days=[dict(date=d,query_status='NOT_ATTEMPTED_LOCAL_AUTH_PATH_NOT_AVAILABLE',
            returned_minute_rows=None,regular240bar_evidence=None,original_market_response_sha256=None) for d in DATES],
        observed_minute_price_time_label=None,observed_minute_volume_unit=None,
        original_backup_HFQ_not_proven_identical_to_frozen_raw_plus_QFQ_archive=True,
        future_admission_requires=['actual240regularbars_each_of4days_plus_any_auction_row_explicitly_separated',
            'real_OHLC_duplicate_lunch_and_price_timestamp_label_validation',
            'volume_unit_verified_against_independent_raw_daily_sum',
            'raw_CPS_no_and_consistent_QFQ_bridge_and_all80h_adjustment_basis_audit',
            'actual_current500_dated_member_coverage_before_full_pool_admission'],
        public_official_documentation=documentation,
        credentials_values_logged_or_copied=False,new_credentials_requested=False,
        dependencies_installed=False,old_data_model_PLAN_bridge_comparison_V1_V2_modified=False,
        actual_model_test_scores_read=False,minute_bars_fabricated=False)
    write_new(output/'availability_receipt.json',result)
    return dict(status=result['status'],receipt=str(output/'availability_receipt.json'),
        receipt_sha256=digest(output/'availability_receipt.json'),market_api_calls=0)


if __name__=='__main__':
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    print(json.dumps(inspect(HERE/'trading_data/ifind_pipeline_probe'/stamp),ensure_ascii=False,indent=2))
