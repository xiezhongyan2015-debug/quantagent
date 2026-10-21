"""Verified strong-model decisions over the compact software action interface."""
from __future__ import annotations

import json
from pathlib import Path
import time
from uuid import uuid4

from .protocol import ACTION_SCHEMA, build_context
from quanta_agents.research_kernel.store import exclusive_lock, serial, write_json


def _admit(kernel, call_id, receipt):
    if receipt.get("model") != "gpt-6-astra" or receipt.get("effort") != "xhigh":
        raise ValueError("Research model must remain gpt-6-astra/xhigh")
    if receipt.get("runtime_identity", {}).get("verified") is not True:
        raise ValueError("Completed response retained; local model/effort identity has not been verified")
    with kernel.store.transaction() as db:
        db.execute("UPDATE model_calls SET status='verified',response=?,usage=?,error=NULL WHERE id=?",
                   (serial(receipt["response"]), serial(receipt.get("usage", {})), call_id))
    try:
        result = kernel.apply_action(receipt["response"], action_id=call_id)
        status = "applied"
    except (ValueError, TypeError, KeyError) as exc:
        # Invalid model actions produce bounded machine feedback, not rewritten
        # fake model responses. The next decision can repair or request a gap.
        result = {"status": "action_rejected", "error": str(exc)[:1500]}
        status = "action_rejected"
        with kernel.store.transaction() as db:
            kernel.store.event(db, "action_rejected", result)
    with kernel.store.transaction() as db:
        db.execute("UPDATE model_calls SET status=? WHERE id=?", (status, call_id))
        kernel.store.event(db, "model_decision", {"call_id": call_id, "status": status,
                                                "usage": receipt.get("usage", {}), "result": result})
        if (kernel.status()["model_calls"] >= kernel.config["budget"]["max_model_calls"] and not kernel.pending()):
            kernel.store.set_meta(db, "stopped", True)
            kernel.store.set_meta(db, "model_stopped", True)
            kernel.store.event(db, "research_budget_closed", {
                "research_review_complete": not kernel._unreviewed() and all(r["review"] for r in kernel.status()["validation_jobs"]),
                "financial_success_claim": False})
    return {"call_id": call_id, "status": status, "model": receipt.get("model"),
            "effort": receipt.get("effort"), "runtime_identity": receipt.get("runtime_identity"),
            "usage": receipt.get("usage", {}), "result": result}


def recover_model_call(kernel, call_id):
    """Offline admission only; never pay to reproduce a saved completion."""
    from quanta_agents.meta_v6.gateway import capture_saved_session, verify_saved_completion
    rows = kernel.store.rows("SELECT * FROM model_calls WHERE id=?", (call_id,))
    if not rows:
        raise ValueError("Unknown model call")
    folder = Path(rows[0]["directory"])
    receipt = verify_saved_completion(folder)
    if not receipt.get("runtime_identity", {}).get("verified"):
        capture_saved_session(folder, receipt)
        receipt = verify_saved_completion(folder)
    return _admit(kernel, call_id, receipt)


def model_step(kernel, *, timeout_seconds=900, gateway=None, on_event=None):
    from quanta_agents.meta_v6.gateway import CodexGateway
    with exclusive_lock(kernel.root / "model.lock"):
        if kernel.store.meta("stopped"):
            return {"status": "stopped"}
        if kernel.pending():
            return {"status": "pending_numerical_stages", "count": kernel.pending()}
        unresolved = kernel.store.rows("SELECT * FROM model_calls WHERE status IN ('started','needs_recovery','verified') ORDER BY created LIMIT 1")
        if unresolved:
            # Failure here deliberately prevents an unnoticed duplicate paid call.
            return recover_model_call(kernel, unresolved[0]["id"])
        if kernel.status()["model_calls"] >= kernel.config["budget"]["max_model_calls"]:
            with kernel.store.transaction() as db:
                pending = kernel.pending()
                if not kernel.store.meta("model_stopped", False):
                    kernel.store.set_meta(db, "model_stopped", True)
                    kernel.store.event(db, "model_budget_exhausted", {"research_review_complete": not kernel._unreviewed() and all(r["review"] for r in kernel.status()["validation_jobs"]), "reason": "No additional research model call is authorized by this frozen study budget",
                                                                     "already_registered_runs_may_finish": True})
                if not pending:
                    kernel.store.set_meta(db, "stopped", True)
            return {"status": "model_budget_exhausted"}
        context = build_context(kernel, after_revision=kernel.store.meta("last_model_revision", 0))
        call_id = "model_" + uuid4().hex
        folder = kernel.root / "model_calls" / call_id
        folder.mkdir(parents=True)
        write_json(folder / "context_manifest.json", {"utf8_bytes": context["utf8_bytes"],
                   "revision": context["revision"], "model": "gpt-6-astra", "effort": "xhigh",
                   "output_limit_bytes": kernel.config["budget"]["max_response_bytes"],
                   "output_limit_enforcement": "post-generation admission limit; not a provider token cap"})
        with kernel.store.transaction() as db:
            db.execute("INSERT INTO model_calls VALUES (?,?,?,?,?,?,?)",
                       (call_id, "started", str(folder), None, None, None, time.time()))
        # The pinned gateway saves prompt, request, original events and receipt.
        try:
            receipt = (gateway or CodexGateway(timeout_seconds=timeout_seconds)).run(
                prompt=context["prompt"], schema=ACTION_SCHEMA, workdir=folder,
                on_event=on_event or (lambda event: None),
                cancelled=lambda: (kernel.root / "cancel.request").exists() or kernel.store.meta("stopped", False))
            result = _admit(kernel, call_id, receipt)
            with kernel.store.connect() as db:
                kernel.store.set_meta(db, "last_model_revision", context["revision"])
            return result
        except Exception as exc:
            with kernel.store.transaction() as db:
                db.execute("UPDATE model_calls SET status='needs_recovery',error=? WHERE id=?", (str(exc)[:1500], call_id))
                kernel.store.event(db, "model_needs_recovery", {"call_id": call_id, "error": str(exc)[:1500]})
            raise
