"""Read-only local Codex identity and usage evidence, without provider calls."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from quanta_agents.research_kernel.store import write_json


def capture_team(root_thread_id, destination, *, sessions_root=None, day="2026/09/12"):
    base = Path(sessions_root or Path.home() / ".codex" / "sessions") / day
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    team = []
    for path in base.glob("*.jsonl"):
        with path.open(encoding="utf-8") as stream:
            try:
                first = json.loads(next(stream))
            except (ValueError, StopIteration):
                continue
        meta = first.get("payload", {})
        source = meta.get("source")
        spawn = source.get("subagent", {}).get("thread_spawn", {}) if isinstance(source, dict) else {}
        if meta.get("id") != root_thread_id and spawn.get("parent_thread_id") != root_thread_id:
            continue
        raw = path.read_bytes()
        contexts, usage_events, final_texts = [], [], []
        for line in raw.decode("utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue  # Current session may end in one incomplete write.
            payload = row.get("payload", {})
            if row.get("type") == "turn_context":
                contexts.append({"utc": row.get("timestamp"), "model": payload.get("model"),
                                 "effort": payload.get("effort") or payload.get("reasoning_effort")})
            if row.get("type") == "event_msg" and payload.get("type") == "token_count" and payload.get("info"):
                usage_events.append(payload["info"])
            if row.get("type") == "response_item" and payload.get("type") == "message" and payload.get("role") == "assistant" and payload.get("phase") == "final_answer":
                final_texts.append(payload.get("content"))
        digest = hashlib.sha256(raw).hexdigest()
        snapshot = target / (meta["id"] + "_" + digest[:12] + ".jsonl")
        if not snapshot.exists():
            snapshot.write_bytes(raw)
        last_usage = usage_events[-1].get("total_token_usage") if usage_events else None
        expected_effort = "ultra" if meta["id"] == root_thread_id else "xhigh"
        team.append({"thread_id": meta["id"], "agent_path": spawn.get("agent_path", "/root"),
                     "local_source": str(path), "snapshot": str(snapshot), "sha256": digest,
                     "contexts": contexts, "expected_model": "gpt-6-astra", "expected_effort": expected_effort,
                     "local_configuration_verified": bool(contexts) and all(c["model"] == "gpt-6-astra" and c["effort"] == expected_effort for c in contexts),
                     "provider_compute_attestation": False, "usage": last_usage,
                     "usage_event_count": len(usage_events), "final_messages": final_texts})
    report = {"captured_utc": datetime.now(timezone.utc).isoformat(), "root_thread_id": root_thread_id,
              "team": sorted(team, key=lambda x: x["agent_path"]), "currency_cost": None,
              "currency_cost_status": "unknown_no_billing_receipt",
              "token_status": "latest_cumulative_local_receipts; active turns may be incomplete",
              "reasoning_tokens": "subset of output; never add twice", "extra_paid_gateway_calls": 0}
    write_json(target / "team_receipts.json", report)
    return report
