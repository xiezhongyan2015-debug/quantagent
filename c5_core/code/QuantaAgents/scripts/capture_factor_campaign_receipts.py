"""Read-only native team accounting, separated from research gateway calls."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json


def capture(root, scope_start="2026-09-13T04:38:34.299Z"):
    destination = Path(root) / "team_receipts"
    destination.mkdir(parents=True, exist_ok=True)
    root_id = "01a0962a-4eef-7752-9b83-0fffd8ffaffd"
    allowed = {"/root", "/root/numeric_evaluation", "/root/generation_control_v10", "/root/independent_audit_v10"}
    sessions = Path.home() / ".codex/sessions"
    team = []
    for path in sessions.glob("2026/09/*/*.jsonl"):
        with path.open(encoding="utf-8") as stream:
            try:
                meta = json.loads(next(stream)).get("payload", {})
            except (ValueError, StopIteration):
                continue
        source = meta.get("source")
        spawn = source.get("subagent", {}).get("thread_spawn", {}) if isinstance(source, dict) else {}
        if meta.get("id") != root_id and spawn.get("parent_thread_id") != root_id:
            continue
        name = spawn.get("agent_path", "/root")
        if name not in allowed:
            continue
        raw = path.read_bytes()
        before = after = None
        contexts = []
        preceding_context = None
        created = meta.get("timestamp", "")
        for line in raw.decode("utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            payload = record.get("payload", {})
            timestamp = record.get("timestamp", "")
            if record.get("type") == "turn_context":
                context = {"utc": timestamp, "model": payload.get("model"),
                           "effort": payload.get("effort", payload.get("reasoning_effort"))}
                if timestamp >= scope_start:
                    contexts.append(context)
                else:
                    preceding_context = context
            if record.get("type") == "event_msg" and payload.get("type") == "token_count" and payload.get("info"):
                usage = payload["info"].get("total_token_usage")
                if usage is not None:
                    if timestamp < scope_start:
                        before = usage
                    else:
                        after = usage
        if after is None:
            continue
        if preceding_context is not None:
            contexts.insert(0, {**preceding_context, "active_at_scope_start": True})
        if before is None and created >= scope_start:
            before = {k: 0 for k in after}
        delta = {k: after[k] - before.get(k, 0) for k in after} if before is not None else None
        digest = hashlib.sha256(raw).hexdigest()
        snapshot = destination / (meta["id"] + "_" + digest[:12] + ".jsonl")
        if not snapshot.exists():
            snapshot.write_bytes(raw)
        expected = "ultra" if name == "/root" else "xhigh"
        team.append({"agent_path": name, "thread_id": meta["id"], "scope_start_utc": scope_start,
                     "latest_cumulative_usage": after, "baseline_usage_before_request": before,
                     "usage_delta": delta, "source": str(path), "snapshot": str(snapshot), "sha256": digest,
                     "contexts_this_scope": contexts,
                     "local_configuration_verified": bool(contexts) and all(c["model"] == "gpt-6-astra" and c["effort"] == expected for c in contexts),
                     "expected_model": "gpt-6-astra", "expected_effort": expected,
                     "provider_compute_attestation": False})
    keys = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
    report = {"captured_utc": datetime.now(timezone.utc).isoformat(), "scope_start_utc": scope_start,
              "scope": "current user-requested V10A implementation and follow-on team work; excludes prior V9A totals",
              "team": team, "known_usage_delta": {k: sum((r["usage_delta"] or {}).get(k, 0) for r in team) for k in keys},
              "unknown_baseline_sessions": [r["thread_id"] for r in team if r["usage_delta"] is None],
              "research_gateway_calls_counted_here": False,
              "subset_rule": "cached input is part of input; reasoning output is part of output; never add subsets twice",
              "active_session_receipts_may_lag": True, "currency_cost": None, "currency_cost_status": "unknown_no_billing_receipt"}
    target = destination / "latest.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="F:/V10A_Factor_Research")
    args = parser.parse_args()
    result = capture(args.root)
    print(json.dumps({"known_usage_delta": result["known_usage_delta"],
                      "team": [{k: r[k] for k in ("agent_path", "thread_id", "local_configuration_verified")} for r in result["team"]],
                      "unknown_baseline_sessions": result["unknown_baseline_sessions"]}, ensure_ascii=True))
