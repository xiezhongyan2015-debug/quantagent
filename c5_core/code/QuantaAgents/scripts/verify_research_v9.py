"""Read-only verification of a closed V9 study; no provider or account execution."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from quanta_agents.meta_v9.study import V9Study, read, sha
from quanta_agents.meta_v6.gateway import verify_saved_completion
from quanta_agents.research_kernel.store import digest, serial


def close(a, b, label):
    if not np.allclose(a, b, rtol=1e-9, atol=1e-7, equal_nan=False):
        raise ValueError("Independent account reconciliation failed: " + label)


def verify_account(folder):
    manifest = read(folder / "manifest.json")
    for name, expected in manifest["files"].items():
        if sha(folder / name) != expected:
            raise ValueError("Account manifest mismatch: " + str(folder / name))
    frozen = read(folder / "frozen.json")
    if digest(frozen) != manifest["identity"]:
        raise ValueError("Account frozen identity mismatch")
    account = read(folder / "account.json")
    daily = pd.read_parquet(folder / "daily.parquet")
    trades = pd.read_parquet(folder / "trades.parquet")
    targets = pd.read_parquet(folder / "targets.parquet")
    capital = account["policy"]["capital"]
    summary = account["summary"]
    if not daily.index.is_unique or not daily.index.is_monotonic_increasing or daily.empty:
        raise ValueError("Invalid retained account calendar")
    if daily.index.max() > pd.Timestamp("2024-12-31"):
        raise ValueError("Account exceeds authorized numeric range")
    previous = daily["nav"].shift(1).fillna(capital)
    returns = daily["nav"] / previous - 1
    close(returns, daily["return"], "daily return from NAV")
    close(daily["cash"] + daily["position_value"], daily["nav"], "cash plus holdings")
    close(daily["position_value"] / daily["nav"], daily["exposure"], "exposure")
    if (daily["cash"] < -1e-7).any():
        raise ValueError("Borrowed cash in long-only account")
    if not trades.empty:
        if not (pd.to_datetime(trades["signal_date"]) < pd.to_datetime(trades["date"])).all():
            raise ValueError("Trade is not after its signal date")
        grouped = trades.groupby("date")
        for key in ("fees", "slippage"):
            close(grouped[key].sum().reindex(daily.index, fill_value=0), daily[key], key)
        close(grouped["notional"].sum().reindex(daily.index, fill_value=0) / previous,
              daily["turnover"], "turnover")
        close(grouped.size().reindex(daily.index, fill_value=0), daily["trade_count"], "trade count")
    rf = (1 + account["policy"]["risk_free_rate"]) ** (1 / 252) - 1
    expected = {
        "return": daily["nav"].iloc[-1] / capital - 1,
        "max_drawdown": (daily["nav"] / daily["nav"].cummax().clip(lower=capital) - 1).min(),
        "mean_exposure": daily["exposure"].mean(),
        "fees": daily["fees"].sum(), "slippage": daily["slippage"].sum(),
        "turnover": daily["turnover"].sum(), "trade_count": daily["trade_count"].sum(),
    }
    if returns.std(ddof=1) > 0:
        expected["sharpe"] = (returns.mean() - rf) / returns.std(ddof=1) * np.sqrt(252)
    for key, value in expected.items():
        close(value, summary[key], key)
    return {"folder": str(folder), "identity": manifest["identity"],
        "manifest_sha256": sha(folder / "manifest.json"), "sessions": len(daily),
        "trades": len(trades), "target_rows": len(targets),
        "start": str(daily.index.min().date()), "end": str(daily.index.max().date()),
        "reconciled": list(expected), "source_execution_certified": summary.get("execution_certified", False)}


def verify(root, junit):
    study = V9Study(root)
    study.verify()
    closed = read(root / "closed.json")
    cfg = study.config
    if closed["status"] != "closed":
        raise ValueError("Study is not closed")
    source_pins = read(root / "source_pins.json")
    inherited_verification = None
    if (root / "context_repair.json").exists():
        amendment = read(root / "context_repair.json")
        original = Path(amendment["initial_study"])
        for name, expected in amendment["inherited_source_files"].items():
            if sha(original / name) != expected:
                raise ValueError("Initial research evidence changed: " + name)
            relative = Path(name)
            if relative.parts[:2] == ("model_calls", "select"):
                copied = root / "model_calls" / "initial_select" / Path(*relative.parts[2:])
            elif name in {"selection.json", "closed.json"}:
                copied = root / "prior_context_failure" / name
            else:
                copied = root / relative
            if sha(copied) != expected:
                raise ValueError("Inherited evidence copy differs: " + str(copied))
        for name in ("development_report.json", "early_fit.json", "declaration.json", "candidate_strategies.json"):
            if sha(root / name) != sha(original / name):
                raise ValueError("Continuation changed numerical evidence: " + name)
        if sha(Path(amendment["initial_source_bundle"])) != amendment["initial_source_bundle_sha256"]:
            raise ValueError("Initial source archive changed")
        inherited_verification = {"initial_accounts": amendment["inherited_accounts"],
            "initial_model_calls": amendment["inherited_model_calls"],
            "raw_initial_files_verified": len(amendment["inherited_source_files"]),
            "initial_rejection_preserved": True, "development_recomputed": False}
    accounts = [verify_account(path.parent) for branch in ("development", "diagnostic")
                for path in sorted((root / branch).rglob("manifest.json"))]
    failed = [read(path) for branch in ("development", "diagnostic")
              for path in sorted((root / branch).rglob("failure.json"))]
    starts = list((root / "account_starts").glob("*.json"))
    if len(starts) > cfg["budget"]["max_accounts"] or len(accounts) + len(failed) != len(starts):
        raise ValueError("Account budget/terminal denominator mismatch")
    calls = []
    for path in sorted((root / "model_calls").glob("*/started.json")):
        saved = read(path.parent / "verified_receipt.json")
        receipt = verify_saved_completion(path.parent / "call")
        if not receipt.get("runtime_identity", {}).get("verified"):
            raise ValueError("Unverified provider identity")
        for key in ("model", "effort", "response", "usage"):
            if receipt[key] != saved[key]:
                raise ValueError("Saved model receipt mismatch: " + key)
        frozen_prompt = path.parent / "frozen_prompt.txt"
        if frozen_prompt.read_text(encoding="utf-8") != (path.parent / "call" / "prompt.txt").read_text(encoding="utf-8"):
            raise ValueError("Provider context is not frozen input")
        if frozen_prompt.stat().st_size > cfg["budget"]["max_context_bytes"]:
            raise ValueError("Provider context budget exceeded")
        calls.append({"phase": path.parent.name, "model": receipt["model"], "effort": receipt["effort"],
            "prompt_bytes": frozen_prompt.stat().st_size, "verified_receipt_sha256": sha(path.parent / "verified_receipt.json")})
    if len(calls) > cfg["budget"]["max_model_calls"]:
        raise ValueError("Model call budget exceeded")
    tree = ET.parse(junit).getroot()
    suites = [tree] if tree.tag == "testsuite" else list(tree.iter("testsuite"))
    tests = {key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "errors", "failures", "skipped")}
    if tests["errors"] or tests["failures"]:
        raise ValueError("Regression tests did not pass")
    selected = read(root / "selection.json")
    if selected["method"] != "reject":
        chosen = read(root / "selected_strategy.json")
        if digest(chosen["strategy"]) != chosen["strategy_sha256"]:
            raise ValueError("Selected specification mismatch")
        if chosen["strategy"] != read(root / "development_report.json")["final_strategy_specs"][chosen["method"]]:
            raise ValueError("Chosen specification differs from original fitted candidate")
    return {"version": "v9_release_verification_v1", "study_root": str(root),
        "software_regression": tests, "junit_sha256": sha(junit),
        "source_pinned_files": len(source_pins), "source_pins_sha256": sha(root / "source_pins.json"),
        "completed_accounts": len(accounts), "failed_accounts": failed, "account_starts": len(starts),
        "account_verifications": accounts, "model_calls": calls, "usage": study.usage(),
        "context_repair_verification": inherited_verification,
        "selection": selected, "review": closed["review"], "engineering_and_artifact_verification": "passed",
        "financial_success_proven": False, "independent_holdout": False, "v8_superiority_proven": False,
        "limitations": ["Numerical reconciliation does not certify historical source vintages or real fills",
                        "No new model calls or market accounts executed during verification"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--additional-junit", type=Path)
    args = parser.parse_args()
    result = verify(args.root.resolve(), args.junit.resolve())
    if args.additional_junit:
        tree = ET.parse(args.additional_junit).getroot()
        suites = [tree] if tree.tag == "testsuite" else list(tree.iter("testsuite"))
        values = {key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "errors", "failures", "skipped")}
        if values["errors"] or values["failures"]:
            raise ValueError("Additional regression tests failed")
        result["context_repair_regression"] = {**values, "junit_sha256": sha(args.additional_junit),
            "overlaps_initial_suite": True, "counts_not_added_naively": True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serial(result), encoding="utf-8")
    print(serial({k: result[k] for k in ("software_regression", "completed_accounts", "engineering_and_artifact_verification", "usage")}))


if __name__ == "__main__":
    main()
