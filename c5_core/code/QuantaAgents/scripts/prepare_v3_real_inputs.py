"""One bounded read of the already selected raw archives; no strategy or model."""
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quanta_agents.meta_v3.ledger import digest
from quanta_agents.meta_v3.research_tools import save_once


def prepare(root):
    parent_path = Path("experiment_traces/meta_raw_saved_research_v15/preparations/20260906T201624557622Z/plan.json")
    parent_bytes = parent_path.read_bytes()
    assert hashlib.sha256(parent_bytes).hexdigest() == "dbe5c0ccbbf3bde5ca48167800af0e71a4acfa40e5fd4992a240cf68be95c318"
    parent = json.loads(parent_bytes)
    root.mkdir(parents=True, exist_ok=False)
    plan = {"kind": "scoped_raw_reference_audit", "created_at": datetime.now(timezone.utc).isoformat(),
        "parent_sha256": hashlib.sha256(parent_bytes).hexdigest(), "codes": parent["codes"],
        "calendar": parent["calendar"], "boundary_prior_date": "2019-06-19",
        "source_artifacts": parent["source_artifacts"], "max_files": 4,
        "max_compressed_bytes_each": 2097152, "max_decompressed_bytes_each": 8388608,
        "max_selected_stock_days": 34, "original_csv_reads": 0, "model_calls": 0,
        "strategy_trials": 0, "original_capital": parent["initial_cash"],
        "selection": "unchanged parent two stocks and sixteen dates plus preceding reference date",
        "no_execution_admission": True}
    save_once(root / "plan.json", plan)
    wanted = {"2019-06-19", *parent["calendar"]}
    selected, reference, manifests = [], [], []
    for binding in parent["source_artifacts"]:
        path = Path(binding["root"])
        binary = (path / binding["rows_file"]).read_bytes()
        assert len(binary) <= 2097152 and hashlib.sha256(binary).hexdigest() == binding["rows_sha256"]
        mb = (path / binding["manifest_file"]).read_bytes()
        assert hashlib.sha256(mb).hexdigest() == binding["manifest_sha256"]
        decoded = gzip.decompress(binary)
        assert len(decoded) <= 8388608
        rows = json.loads(decoded)
        assert len(rows) <= 2000
        subset = [x for x in rows if x["date"] in wanted]
        assert len(subset) == 17 and {x["date"] for x in subset} == wanted
        assert all(x.get("code",x.get("symbol")) == binding["code"] for x in subset)
        selected.extend(subset); manifests.append(json.loads(mb))
        for prior, current in zip(subset, subset[1:]):
            change = Decimal(str(current["raw_prev_close"])) - Decimal(str(prior["raw_close"]))
            expected = Decimal("-0.0833") if binding["code"] == "sh600006" and current["date"] == "2019-06-20" else Decimal(0)
            reference.append({"code": binding["code"], "date": current["date"],
                "prior_date": prior["date"], "prior_raw_close": prior["raw_close"],
                "raw_reference": current["raw_prev_close"], "difference": str(change),
                "cash_event_expected_difference": str(expected),
                "within_one_cent_of_cash_reference": abs(change-expected) <= Decimal("0.01"),
                "stock_name": current["stock_name"], "volume": current["volume"],
                "historical_membership": current.get("historical_membership"), "accepted": current.get("accepted")})
    save_once(root / "selected_rows.json", selected)
    save_once(root / "source_manifests.json", manifests)
    save_once(root / "reference_audit.json", {"rows": reference,
        "all_references_explained_within_cent": all(x["within_one_cent_of_cash_reference"] for x in reference),
        "selected_rows_sha256": digest(selected), "scope": "reference consistency, not official tick/limit certification",
        "company_event_date_source": "saved BaoStock candidate; issuer annual report confirms amount; exact 600006 implementation original not yet obtained",
        "raw_open_or_close_not_replaced": True, "execution_valid": False, "formal_target_success": False})
    print(json.dumps({"selected_rows": len(selected), "reference_rows": len(reference),
        "unexplained": [x for x in reference if not x["within_one_cent_of_cash_reference"]],
        "changed_references": [x for x in reference if Decimal(x["difference"]) != 0],
        "row_fields": list(selected[0]), "manifest_keys": list(manifests[0])},ensure_ascii=True))


if __name__ == "__main__":
    prepare(Path("experiment_traces/meta_framework_v3/real_inputs_001"))
