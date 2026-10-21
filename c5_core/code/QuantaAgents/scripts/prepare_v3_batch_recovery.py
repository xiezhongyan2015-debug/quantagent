"""Admit the once-only saved batch contract repair; never call a model here."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quanta_agents.meta_v3.saved_recovery import prepare

if __name__=='__main__':
    ledger=prepare(ROOT/'experiment_traces/meta_framework_v3/batch_calibration_001',
        ROOT/'experiment_traces/meta_framework_v3/batch_recovery_001','batch_tool_calibration_001',
        reason='The original public submit_research_report.program_evidence_id description incorrectly restricted it to develop_strategy while the batch_candidate_id branch actually requires an execute_batch call ID. This controller contract defect was caught before a final attempt; the original five settled model calls and nine completed generated-data candidates are preserved. The corrected contract explicitly gives both ID branches and the abstention branch.',
        max_calls=8,max_tokens=400000)
    print(json.dumps({'root':str(ledger.root),'status':ledger.status('batch_tool_calibration_001')},ensure_ascii=True))
