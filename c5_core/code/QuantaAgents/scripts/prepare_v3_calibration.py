"""Freeze one bounded synthetic development case without searching results."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quanta_agents.meta_v3.ledger import digest, serial
from quanta_agents.meta_v3.research_tools import save_once


def prepare(root, *, flat=False):
    import pandas as pd
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    days = list(pd.bdate_range("2019-03-04", periods=12).strftime("%Y-%m-%d"))
    codes = ["sh600001", "sz000001"]
    description = ("New synthetic calibration, deliberately short and development-only. Activity and price fields are supplied as nominally available after close. "
                   "Raw execution includes declared costs and limited capital. This is not real market data or certified net OOS evidence.")
    source_note = {"kind": "synthetic_generation_record", "flat_prices": flat, "days": days, "codes": codes,
                   "rule_frozen_before_research": True, "market_reads": 0, "outcome_selection": False}
    save_once(root / "generation.json", source_note)
    evidence = digest(source_note)
    field_rows, eligibility_rows, artifacts = [], [], []
    for j, code in enumerate(codes):
        prices = [10.0 + j * 2]
        activity = [float((i * 7 + j * 3) % 5 + 1) for i in range(len(days))]
        for i in range(1, len(days)):
            change = 0.0 if flat else (0.016 if activity[max(0, i - 2)] >= 4 else -0.007)
            prices.append(round(prices[-1] * (1 + change), 2))
        rows = []
        for i, day in enumerate(days):
            prices_text = {"raw_open": f"{prices[i]:.2f}", "raw_close": f"{prices[i]:.2f}",
                           "raw_prev_close": f"{prices[max(0, i - 1)]:.2f}"}
            rows.append({"code": code, "date": day, **prices_text, "volume": 1000000,
                "stock_name": "synthetic_calibration", "accepted": True, "historical_membership": True,
                "execution_valid": False, "raw_price_text": prices_text})
            for name, value in (("activity", activity[i]), ("close", prices[i])):
                field_rows.append({"session": day, "symbol": code, "field": name, "value": value,
                    "effective_at": day + "T15:05:00+08:00", "available_at": day + "T15:09:00+08:00", "source_evidence_id": evidence})
            eligibility_rows.append({"session": day, "symbol": code, "eligible": True,
                "effective_at": day + "T09:00:00+08:00", "available_at": day + "T09:00:00+08:00", "source_evidence_id": evidence})
        payload = gzip.compress(serial(rows).encode(), mtime=0)
        (root / (code + ".json.gz")).write_bytes(payload)
        manifest = {"codes": [code], "execution_valid": False, "adjusted_price_or_factor_inversion_used": False,
                    "scope": "new_synthetic_v3_development"}
        save_once(root / (code + ".manifest.json"), manifest)
        artifacts.append({"code": code, "root": str(root), "rows_file": code + ".json.gz",
            "rows_sha256": hashlib.sha256(payload).hexdigest(), "manifest_file": code + ".manifest.json",
            "manifest_sha256": hashlib.sha256((root / (code + ".manifest.json")).read_bytes()).hexdigest()})
    kinds = ("corporate_actions", "market_status", "capacity", "fee_policy", "availability", "membership")
    obligations = [{"code": c, "date": d, "kind": k, "status": "fixture_complete",
                    "evidence_sha256": evidence, "note": "Synthetic fixture only; no certification of real historical coverage."}
                   for c in codes for d in days for k in kinds]
    case = {"research_class": "synthetic_calibration", "description": description, "initial_cash": "10000.00",
        "decision_fixture": {"kind": "exposed_synthetic_decision_fixture", "codes": codes, "calendar": days,
            "fields": [{"name": "activity", "unit": "synthetic_activity_index"}, {"name": "close", "unit": "CNY"}],
            "field_rows": field_rows, "eligibility_rows": eligibility_rows},
        "raw_source_bindings": {"source_artifacts": artifacts, "obligations": obligations, "corporate_actions": []}}
    save_once(root / "case.json", case)
    return case


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--flat", action="store_true")
    args = parser.parse_args()
    case = prepare(args.root, flat=args.flat)
    print(json.dumps({"case": str(Path(args.root).resolve() / "case.json"), "case_hash": digest(case)}, ensure_ascii=True))
