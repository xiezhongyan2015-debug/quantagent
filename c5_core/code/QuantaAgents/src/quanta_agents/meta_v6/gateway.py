"""V6 runtime capture with explicit Windows text-stdin newline verification.

The original session bytes, saved prompt, response and V3 verifier are never
edited. A runtime input may differ from the saved logical input only by the
uniform LF -> CRLF conversion performed by Windows text stdin. All original
thread/turn/model/effort/provider/output/tool-policy checks still apply.
This is local configuration evidence, never provider-side compute attestation.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import UUID

from quanta_agents.meta_v3 import codex_session_gateway as legacy

base = legacy.base
MODEL, EFFORT = legacy.MODEL, legacy.EFFORT
GatewayError = legacy.GatewayError
SESSION = legacy.SESSION
CAPTURE_VERSION = "v6_codex_session_windows_text_stdin_v1"
_strict_json = legacy._strict_json


def verify_runtime_session(raw, receipt, prompt):
    """Bind unchanged raw session input to exact text or one uniform CRLF map."""
    rows = [_strict_json(line) for line in raw.decode("utf-8").splitlines()]
    users = [row.get("payload") for row in rows if type(row) is dict
             and row.get("type") == "response_item" and type(row.get("payload")) is dict
             and row["payload"].get("type") == "message" and row["payload"].get("role") == "user"]
    texts = []
    for message in users:
        content = message.get("content")
        if type(content) is not list or not all(type(item) is dict and type(item.get("text")) is str for item in content):
            raise GatewayError("Invalid runtime message content")
        texts.append("".join(item["text"] for item in content))
    observed, mode = prompt, "exact"
    if texts.count(prompt) != 1:
        # Deliberately narrower than splitlines/strip/universal whitespace:
        # no lone CR, mixed CRLF/LF, spaces or Unicode normalization allowed.
        if "\r" in prompt or "\n" not in prompt:
            raise GatewayError("Runtime prompt does not bind to the invocation")
        transported = prompt.replace("\n", "\r\n")
        if texts.count(transported) != 1:
            raise GatewayError("Runtime prompt differs beyond uniform Windows LF-to-CRLF transport")
        observed, mode = transported, "windows_text_stdin_lf_to_crlf"
    # Pass the independently bound observed input to the unchanged strict
    # verifier. It checks every other message and exact final output itself.
    identity = legacy.verify_runtime_session(raw, receipt, observed)
    identity["prompt_transport"] = {
        "version": CAPTURE_VERSION, "mode": mode,
        "policy": "exact text or uniform LF-to-CRLF; no other normalization",
        "logical_prompt_utf8_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "observed_prompt_utf8_sha256": hashlib.sha256(observed.encode("utf-8")).hexdigest(),
        "logical_lf_count": prompt.count("\n"), "observed_crlf_count": observed.count("\r\n"),
        "original_session_sha256": hashlib.sha256(raw).hexdigest(),
        "original_session_bytes_preserved": True,
        "verifier_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    return identity


def capture_saved_session(workdir, receipt):
    """Offline capture of the exact saved thread, without invoking Codex."""
    folder = Path(workdir).resolve()
    destination = folder / SESSION
    if destination.exists():
        return
    thread_id = receipt["identity_verification"]["provider_ids"]["thread_id"]
    if not isinstance(thread_id, str) or str(UUID(thread_id)) != thread_id:
        raise GatewayError("Missing canonical thread ID for saved session capture")
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    matches = list((codex_home / "sessions").glob(f"*/*/*/*-{thread_id}.jsonl"))
    if len(matches) != 1:
        raise GatewayError("Exactly one persisted runtime session is required")
    if matches[0].stat().st_size > 32 * 1024 * 1024:
        raise GatewayError("Runtime session exceeds capture limit")
    raw = matches[0].read_bytes()
    prompt = (folder / "prompt.txt").read_text(encoding="utf-8")
    verify_runtime_session(raw, receipt, prompt)
    with destination.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def verify_saved_completion(workdir, *, expected_artifact_sha256=None, **kwargs):
    expected = dict(expected_artifact_sha256 or {})
    session_hash = expected.pop(SESSION, None)
    receipt = base.verify_saved_completion(workdir, expected_artifact_sha256=expected, **kwargs)
    receipt["runtime_identity"] = {"verified": False, "level": "not_captured",
                                   "provider_request_binding_verified": False}
    path = Path(workdir) / SESSION
    if not path.is_file():
        if session_hash is not None:
            raise GatewayError("Previously captured runtime session is missing")
        return receipt
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if session_hash is not None and session_hash != actual:
        raise GatewayError("Runtime session artifact hash mismatch")
    receipt["runtime_identity"] = verify_runtime_session(
        raw, receipt, (Path(workdir) / "prompt.txt").read_text(encoding="utf-8"))
    receipt["artifact_sha256"][SESSION] = actual
    receipt["verification"] += " Original local runtime identity verified; exact prompt or explicitly recorded Windows text-stdin newline mapping."
    return receipt


class CodexGateway(legacy.CodexGateway):
    def run(self, **kwargs):
        # Inherit the original locked command and base execution; substitute
        # only this versioned capture verifier, not any inference transport.
        receipt = base.CodexGateway.run(self, **kwargs)
        try:
            capture_saved_session(kwargs["workdir"], receipt)
        except (ValueError, OSError, KeyError, GatewayError) as exc:
            kwargs["on_event"]({"kind": "warning", "text": str(exc),
                                "data": {"completed_response_preserved": True}})
        return verify_saved_completion(kwargs["workdir"])
