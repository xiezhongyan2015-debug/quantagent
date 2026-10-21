"""One controller-pinned batch candidate; no gateway, discovery or network."""
import argparse
import json
import os
import threading
import time
from pathlib import Path

from .ledger import digest,need
from .program_execution import develop
from .research_tools import save_once
from .runtime import source_pins,verify_case_sources


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);args=parser.parse_args()
    request_path=Path(args.request).resolve();need(request_path.stat().st_size<=16*1024**2,'candidate request bytes')
    request=json.loads(request_path.read_text(encoding='utf-8'));folder=request_path.parent
    need(time.time()<request['deadline_epoch'],'batch deadline elapsed before worker start')
    timer=threading.Timer(request['deadline_epoch']-time.time(),lambda:os._exit(124));timer.daemon=True;timer.start()
    need(request['source_pins']==source_pins(),'batch worker source drift')
    verify_case_sources({'case':request['case'],'case_hash':request['case_hash']})
    need(digest(request['program'])==request['program_hash'],'batch worker program drift')
    # Exceptions leave the durable intent unresolved. A later controller may
    # inspect saved evidence, but cannot silently recompute this candidate.
    artifact=develop(folder,request['case'],request['program'],save_once)
    body={'request_hash':digest(request),'artifact_hash':digest(artifact),'artifact':artifact}
    temporary=folder/'worker_result.pending.json';save_once(temporary,body)
    need(not (folder/'worker_result.json').exists(),'worker result already exists')
    temporary.replace(folder/'worker_result.json')
    timer.cancel()


if __name__=='__main__':main()
