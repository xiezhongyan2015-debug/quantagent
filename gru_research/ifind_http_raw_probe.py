"""Isolated source-only iFinD HTTP minute calibration probe, dry-run by default.

Run ``python -m gru_research.ifind_http_raw_probe`` to display five fixed requests.
No token, network, response file, SDK, or account configuration is used in dry-run.
Actual collection requires --execute, --token-env NAME and a NEW --output directory.
The only credential lookup is that explicitly named environment variable. This
module never logs in, refreshes tokens, retries, follows redirects, or trades.

HTTP guide v2.1, physical pages 3, 8, 9, 12, 13 documents the header, request and
envelope fields. It does NOT specify a high_frequency table/time JSON example.
All actual minute semantics therefore remain pending; no bars are normalized.
The timeout is a socket-operation limit; returned responses also face an elapsed
budget check. This is not an end-to-end hard deadline, including DNS resolution.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import html
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import time
from urllib.parse import quote, quote_plus, unquote

ENDPOINT = "https://quantapi.51ifind.com/api/v1/high_frequency"
HOST = "quantapi.51ifind.com"
ENDPOINT_PATH = "/api/v1/high_frequency"
SYMBOL = "600004.SH"
DATES = ("2026-09-23", "2026-09-24", "2026-09-28", "2026-09-29", "2026-09-30")
INDICATORS = "open,high,low,close,volume,amount"
MAX_REQUESTS = 5
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TIMEOUT_SECONDS = 20.0
DOCUMENTED_FIELDS = ("errorcode", "errmsg", "tables", "datatype", "inputParams", "perf", "dataVol")
SAFE_RESPONSE_HEADERS = ("content-type", "content-length", "date", "x-request-id")
DOC_DIR = "trading_data/ifind_official_linux_availability_20261002"
DOC_BINDINGS = {
    "source_availability_receipt_v1.json": "f7fe16829173ed5ca9f594832296319a1eddf86cce180a941fc41e2ac79cb7f7",
    "http_documentation_supplement_receipt_v1.json": "961d863ccc562d33d7058876ae8bc18dcfa170f2586e6d93f43c9bf88c3320b9",
    "official_http_user_guide_original.bin": "4fea576a71871de274e2dae554f8c8d23469ef6050d79f2114a30bea3c029682",
    "official_http_user_guide_pages.json": "588760c5c07c9b4de908a86d91cb82a795ab71c4ea93044e8dc2a6f4e65074d4",
}
UNKNOWN_SEMANTICS = {
    "actual_HTTP_market_access_and_account_quota": "UNKNOWN",
    "high_frequency_nested_table_time_array_schema": "UNKNOWN_NOT_SPECIFIED_BY_HTTP_GUIDE",
    "volume_unit": "UNKNOWN",
    "amount_currency_unit": "UNKNOWN",
    "bar_timestamp_left_or_right_end": "UNKNOWN",
    "begin_end_and_requested_date_inclusion": "UNKNOWN",
    "auction_rows_and_counts": "UNKNOWN",
    "240_regular_minutes_completeness": "UNKNOWN",
    "frozen_80hour_QFQ_factor_bridge": "UNKNOWN",
    "all_500_dated_members_and_complete_warm_window": "UNKNOWN",
    "new_daily_market_and_PIT_inputs": "UNKNOWN",
}


class ProbeRefused(Exception):
    """Static reason codes only; no request/secret/response exception text."""

    def __init__(self, reason_code):
        self.reason_code = reason_code
        super().__init__(reason_code)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def provenance_metadata():
    return {
        "official_HTTP_guide_version": "2.1",
        "official_HTTP_guide_version_date": "2025-08-25",
        "bound_local_official_sources": {DOC_DIR + "/" + k: v for k, v in DOC_BINDINGS.items()},
        "physical_PDF_pages": {"access_token_header": 3, "endpoint_and_body": 8,
            "six_indicators": [8, 9], "Interval": 12, "Fill_CPS_Timeformat_and_envelope": 13},
        "HTTP_default_Fill": "Original",
        "older_SDK_default_Fill": "Previous (separate SDK manual; not used here)",
        "HTTP_nested_response_example_available": False,
        "source_hashes_checked_in_this_call": False,
    }


def verify_official_provenance():
    """Read only these explicit public-document files, never credential paths."""
    base = Path(__file__).resolve().parent
    for name, expected in DOC_BINDINGS.items():
        try:
            data = (base / DOC_DIR / name).read_bytes()
        except OSError:
            raise ProbeRefused("DOCUMENTATION_SOURCE_MISSING") from None
        if digest(data) != expected:
            raise ProbeRefused("DOCUMENTATION_SOURCE_HASH_CHANGED")
    result = provenance_metadata()
    result["source_hashes_checked_in_this_call"] = True
    return result


def request_body(day):
    if day not in DATES:
        raise ProbeRefused("DATE_OUTSIDE_FIXED_CALIBRATION_BUDGET")
    return {"codes": SYMBOL, "indicators": INDICATORS,
        "starttime": day + " 09:25:00", "endtime": day + " 15:00:00",
        "functionpara": {"Interval": "1", "Fill": "Original", "CPS": "no", "Timeformat": "BeiJingTime"}}


def build_plan(dates=DATES):
    dates = tuple(dates)
    if not dates or len(dates) > MAX_REQUESTS or len(set(dates)) != len(dates) or any(d not in DATES for d in dates):
        raise ProbeRefused("INVALID_FIXED_CALIBRATION_BUDGET")
    return {"status": "DRY_RUN_CALIBRATION_PENDING_NO_SECRET_OR_NETWORK",
        "source_only": True, "endpoint": ENDPOINT, "method": "POST",
        "request_count": len(dates), "maximum_requests": MAX_REQUESTS,
        "requests": [{"date": day, "body": request_body(day)} for day in dates],
        "request_timeout_seconds_maximum": MAX_TIMEOUT_SECONDS,
        "timeout_scope": "socket operation timeout plus post-return elapsed budget rejection; not an end-to-end hard deadline",
        "request_authentication_header_name_only": "access_token",
        "documentation_provenance": provenance_metadata(),
        "still_unknown": dict(UNKNOWN_SEMANTICS),
        "secret_read": False, "market_network_calls": 0,
        "response_files_written": False, "normalization_performed": False,
        "timestamps_shifted": False, "auction_rows_removed": False,
        "model_or_trading_admission": False, "full_PLAN_complete": False}


def read_access_token(environment_name):
    # No default name, enumeration, dotenv, account config, login or refresh.
    if not isinstance(environment_name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", environment_name):
        raise ProbeRefused("EXPLICIT_SECRET_ENVIRONMENT_NAME_REQUIRED")
    value = os.environ.get(environment_name)
    if not isinstance(value, str) or not value:
        raise ProbeRefused("SPECIFIED_ACCESS_TOKEN_ENVIRONMENT_VALUE_MISSING")
    if any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ProbeRefused("ACCESS_TOKEN_NOT_SAFE_FOR_HTTP_HEADER")
    return value


def contains_secret(data, secret):
    """Reject direct, JSON-escaped, URL/entity/base64 echoes before persistence."""
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
    variants = {secret, json.dumps(secret, ensure_ascii=True)[1:-1], quote(secret, safe=""),
                quote_plus(secret, safe=""), html.escape(secret), base64.b64encode(secret.encode()).decode()}
    if any(v and v in text for v in variants):
        return True
    decoded = html.unescape(unquote(text))
    if secret in decoded:
        return True
    # A JSON string may escape every ASCII character, not just quotes/slashes.
    try:
        obj = json.loads(text)
    except (ValueError, TypeError, RecursionError):
        return False
    def visit(x):
        if isinstance(x, str):
            return secret in x or secret in html.unescape(unquote(x))
        if isinstance(x, dict):
            return any(visit(k) or visit(v) for k, v in x.items())
        if isinstance(x, list):
            return any(visit(v) for v in x)
        return False
    try:
        return visit(obj)
    except RecursionError:
        return True


def credential_field_present(data):
    """Do not persist a response that carries other credential-labelled fields."""
    try:
        obj = json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        return False
    protected = {"accesstoken", "refreshtoken", "authorization", "password", "clientsecret", "apikey"}
    def visit(x):
        if isinstance(x, dict):
            return any(re.sub(r"[^a-z]", "", k.lower()) in protected or visit(v) for k, v in x.items())
        if isinstance(x, list):
            return any(visit(v) for v in x)
        return False
    try:
        return visit(obj)
    except RecursionError:
        return True


def inspect_envelope(data):
    """Never manufacture a time/table schema or an empty-response success."""
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError("nonstandard JSON constant")
    try:
        obj = json.loads(data.decode("utf-8"), object_pairs_hook=unique_pairs, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        return {"status": "INVALID_JSON_RESPONSE", "failure": True, "continue_budget": False}
    if not isinstance(obj, dict) or type(obj.get("errorcode")) is not int:
        return {"status": "RESPONSE_SCHEMA_UNVERIFIED", "failure": False, "continue_budget": False,
                "documented_errorcode_integer_present": False}
    if obj["errorcode"] != 0:
        return {"status": "API_FAILURE_HASH_ONLY", "failure": True, "continue_budget": False,
                "errorcode": obj["errorcode"]}
    missing = [k for k in DOCUMENTED_FIELDS if k not in obj]
    if missing:
        return {"status": "RESPONSE_SCHEMA_UNVERIFIED", "failure": False, "continue_budget": False,
                "documented_fields_missing": missing, "errorcode": 0}
    # The guide gives no nested JSON example or schema. Only note emptiness,
    # never infer thscode/time/row arrays, units, regular bars or successful data.
    structured = isinstance(obj["tables"], (dict, list))
    empty = obj["tables"] in (None, [], {})
    return {"status": "EMPTY_RESPONSE_SCHEMA_UNVERIFIED" if empty else "RESPONSE_SCHEMA_UNVERIFIED",
            "failure": False, "continue_budget": structured and not empty, "errorcode": 0,
            "documented_envelope_fields_present": True,
            "nested_schema_verified": False, "normalized_rows": None}


@dataclass
class RawResponse:
    status: int
    headers: list = field(repr=False)
    body: bytes = field(repr=False)
    received_utc: str
    full_body_received: bool = True
    framing_diagnostic: dict | None = None


class ChunkedFramingAudit:
    """Check the actual wire framing consumed by HTTPResponse's chunk parser.

    HTTPResponse tolerates EOF in trailers and discards chunk CRLF without
    validating it. Neither tolerance certifies a complete original response.
    This wrapper observes only the parser's read/readline calls; no wire bytes or
    trailer values are logged. Decoded partial body bytes are retained in memory
    only for fail-closed hashing when the standard parser raises.
    """
    def __init__(self, stream):
        self.stream = stream
        self.state = "size"
        self.remaining = 0
        self.invalid = False
        self.complete = False
        self.trailer_count = 0
        self.partial_body = []

    def read(self, amount=-1):
        data = self.stream.read(amount)
        if self.state == "data":
            self.partial_body.append(data)
            self.remaining -= len(data)
            if self.remaining < 0:
                self.invalid = True
            if self.remaining == 0:
                self.state = "chunk_crlf"
        elif self.state == "chunk_crlf":
            if amount != 2 or data != b"\r\n":
                self.invalid = True
            self.state = "size"
        else:
            self.invalid = True
        return data

    def readline(self, limit=-1):
        line = self.stream.readline(limit)
        if self.state == "size":
            if not re.fullmatch(rb"[0-9a-fA-F]+(?:;[^\r\n]*)?\r\n", line):
                self.invalid = True
            try:
                size = int(line.split(b";", 1)[0].strip(), 16)
            except ValueError:
                self.invalid = True
                size = 0
            self.remaining = size
            self.state = "data" if size else "trailers"
        elif self.state == "trailers":
            if line == b"\r\n":
                self.complete = True
                self.state = "complete"
            else:
                self.trailer_count += 1
                if not line.endswith(b"\r\n") or b":" not in line or self.trailer_count > 100:
                    self.invalid = True
                if self.trailer_count > 100:
                    raise http.client.HTTPException("bounded trailer count exceeded")
        else:
            self.invalid = True
        return line

    def close(self):
        return self.stream.close()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def read_framed_body(response):
    """Bound decoded body bytes and conservatively verify the HTTP message end."""
    headers = response.getheaders()
    lengths = [v.strip() for k, v in headers if k.lower() == "content-length"]
    encodings = [v.strip().lower() for k, v in headers if k.lower() == "transfer-encoding"]
    info = {"status": "HTTP_FRAMING_UNVERIFIED", "body_read_attempted": False,
            "content_length_verified": False, "chunked_terminator_and_trailers_verified": False,
            "close_delimited_EOF_verified": False}
    invalid_length = len(lengths) > 1 or (lengths and not re.fullmatch(r"[0-9]+", lengths[0]))
    if invalid_length or len(encodings) > 1 or (encodings and encodings != ["chunked"]) or (lengths and encodings):
        info["status"] = "AMBIGUOUS_OR_INVALID_HTTP_FRAMING_HEADERS"
        return b"", False, info
    declared = int(lengths[0]) if lengths else None
    chunked = getattr(response, "chunked", None)
    initial_length = getattr(response, "length", None)
    audit = None
    if encodings:
        if chunked is not True or getattr(response, "fp", None) is None:
            info["status"] = "CHUNKED_PARSER_STATE_UNVERIFIED"
            return b"", False, info
        audit = ChunkedFramingAudit(response.fp)
        response.fp = audit
    elif declared is not None:
        if chunked is not False or initial_length != declared:
            info["status"] = "CONTENT_LENGTH_PARSER_STATE_UNVERIFIED"
            return b"", False, info
    elif chunked is not False or initial_length is not None or getattr(response, "will_close", None) is not True:
        info["status"] = "CLOSE_DELIMITED_PARSER_STATE_UNVERIFIED"
        return b"", False, info
    raw = b""
    info["body_read_attempted"] = True
    try:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            info["status"] = "BODY_BYTE_CAP_EXCEEDED"
            return raw, False, info
        if audit is not None:
            complete = audit.complete and not audit.invalid and response.isclosed()
            info["chunked_terminator_and_trailers_verified"] = complete
        elif declared is not None:
            complete = len(raw) == declared and response.length == 0 and response.isclosed()
            info["content_length_verified"] = complete
            info["declared_content_length"] = declared
            info["remaining_content_length"] = response.length
        else:
            # A short positive bounded read alone does not prove EOF. Require
            # another read returning no bytes and HTTPResponse closing its fp.
            tail = response.read(1)
            complete = tail == b"" and response.isclosed()
            raw += tail
            info["close_delimited_EOF_verified"] = complete
        info["status"] = "HTTP_MESSAGE_COMPLETE_VERIFIED" if complete else "HTTP_MESSAGE_INCOMPLETE_OR_END_UNVERIFIED"
        return raw, complete, info
    except Exception as exc:
        # Never log exception text. Preserve only body bytes actually retained.
        if audit is not None:
            raw = b"".join(audit.partial_body)[:MAX_RESPONSE_BYTES + 1]
        elif isinstance(exc, http.client.IncompleteRead) and isinstance(exc.partial, bytes):
            raw += exc.partial
        info["status"] = "HTTP_BODY_OR_FRAMING_READ_FAILED"
        return raw, False, info


def request_raw(body, secret, timeout):
    """One fixed HTTPS request, verified TLS, direct connection, no redirect/retry."""
    if body not in [request_body(day) for day in DATES] or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise ProbeRefused("TRANSPORT_OUTSIDE_FIXED_REQUEST_OR_TIMEOUT_BUDGET")
    wire = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    connection = http.client.HTTPSConnection(HOST, timeout=timeout, context=ssl.create_default_context())
    started = time.monotonic()
    try:
        connection.request("POST", ENDPOINT_PATH, body=wire,
            headers={"Content-Type": "application/json", "access_token": secret, "Accept-Encoding": "identity"})
        response = connection.getresponse()
        raw, framed_complete, framing = read_framed_body(response)
        elapsed = time.monotonic() - started
        framing.update(socket_operation_timeout_seconds=timeout,
            post_return_elapsed_seconds=elapsed,
            post_return_elapsed_budget_satisfied=elapsed <= timeout,
            end_to_end_hard_deadline_enforced=False)
        complete = framed_complete and len(raw) <= MAX_RESPONSE_BYTES and elapsed <= timeout
        return RawResponse(response.status, response.getheaders(), raw, utc_now(), complete, framing)
    finally:
        connection.close()


def safe_headers(headers, secret):
    out, omitted = {}, []
    for name, value in headers:
        name = name.lower()
        if name not in SAFE_RESPONSE_HEADERS:
            continue
        if contains_secret(value, secret):
            omitted.append(name)
        else:
            out[name] = value
    return out, sorted(set(omitted))


def write_safe_json(path, value, secret):
    wire = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode() + b"\n"
    if contains_secret(wire, secret):
        raise ProbeRefused("SECRET_COLLISION_REFUSED_BEFORE_METADATA_PERSISTENCE")
    with Path(path).open("xb") as stream:
        stream.write(wire)


def run_probe(*, execute=False, token_env=None, output=None, dates=DATES, timeout=MAX_TIMEOUT_SECONDS):
    plan = build_plan(dates)
    if not execute:
        return plan
    if output is None or not isinstance(timeout, (int, float)) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise ProbeRefused("NEW_OUTPUT_AND_TIMEOUT_AT_MOST_20_SECONDS_REQUIRED")
    provenance = verify_official_provenance()
    secret = read_access_token(token_env)
    if contains_secret(str(output), secret) or contains_secret(json.dumps(plan), secret):
        raise ProbeRefused("SECRET_COLLISION_IN_SAFE_REQUEST_OR_OUTPUT_METADATA")
    destination = Path(output)
    try:
        destination.mkdir(parents=True, exist_ok=False)
    except OSError:
        raise ProbeRefused("NEW_OUTPUT_DIRECTORY_REQUIRED") from None
    records = []
    for item in plan["requests"]:
        started = utc_now()
        record = {"date": item["date"], "request_body": item["body"],
            "request_body_sha256": digest(json.dumps(item["body"], ensure_ascii=False, separators=(",", ":")).encode()),
            "endpoint": ENDPOINT, "request_started_utc": started, "timeout_seconds": timeout,
            "timeout_scope": "socket operation timeout plus post-return elapsed budget rejection; not an end-to-end hard deadline",
            "raw_response_saved": False, "raw_response_path": None,
            "source_received_utc": None, "normalization_performed": False,
            "timestamps_shifted": False, "auction_rows_removed": False}
        continue_budget = False
        try:
            response = request_raw(item["body"], secret, timeout)
        except Exception:
            # Exception text can contain credentials/request bytes; never copy it.
            record.update(status="TRANSPORT_FAILURE_NO_RESPONSE_BYTES", http_status=None,
                failure_observed_utc=utc_now(), response_bytes_sha256=None,
                response_byte_count=None, full_response_received=False)
        else:
            headers, omitted = safe_headers(response.headers, secret)
            record.update(http_status=response.status, source_received_utc=response.received_utc,
                safe_response_headers=headers, safe_header_secret_echo_omitted=omitted,
                response_bytes_sha256=digest(response.body), response_byte_count=len(response.body),
                response_byte_count_scope="body bytes actually retained, including incomplete prefix; not a declared full response length",
                transport_framing_diagnostic=response.framing_diagnostic,
                full_response_received=response.full_body_received)
            if contains_secret(response.body, secret) or credential_field_present(response.body):
                record.update(status="SECRET_OR_CREDENTIAL_ECHO_HASH_ONLY_BODY_NOT_SAVED")
            elif not response.full_body_received:
                record.update(status="INCOMPLETE_OR_OVER_BUDGET_RESPONSE_HASH_ONLY")
            elif not 200 <= response.status < 300:
                record.update(status="HTTP_FAILURE_HASH_ONLY_BODY_NOT_SAVED")
            else:
                envelope = inspect_envelope(response.body)
                record["envelope_diagnostic"] = envelope
                record["status"] = envelope["status"]
                if not envelope["failure"]:
                    raw_path = destination / (item["date"] + ".response.bin")
                    with raw_path.open("xb") as stream:
                        stream.write(response.body)
                    record.update(raw_response_saved=True, raw_response_path=raw_path.name)
                    continue_budget = envelope["continue_budget"]
        write_safe_json(destination / (item["date"] + ".receipt.json"), record, secret)
        records.append(record)
        if not continue_budget:
            break
    summary = {"status": "RAW_CALIBRATION_PENDING_INDEPENDENT_SOURCE_REVIEW",
        "source_only": True, "created_utc": utc_now(), "endpoint": ENDPOINT,
        "maximum_requests": MAX_REQUESTS, "requests_attempted": len(records),
        "requested_dates": [x["date"] for x in plan["requests"]],
        "unattempted_dates": [x["date"] for x in plan["requests"][len(records):]],
        "documentation_provenance": provenance, "records": records,
        "still_unknown": dict(UNKNOWN_SEMANTICS), "normalized_market_files_created": False,
        "model_or_trading_admission": False, "full_PLAN_complete": False}
    write_safe_json(destination / "calibration_receipt.json", summary, secret)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--token-env", help="Explicit environment variable NAME only; never put a token value here")
    parser.add_argument("--output", type=Path, help="New isolated raw evidence directory; ignored in dry-run")
    parser.add_argument("--date", action="append", choices=DATES, help="Optional subset of fixed five calibration dates")
    parser.add_argument("--timeout", type=float, default=MAX_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    try:
        result = run_probe(execute=args.execute, token_env=args.token_env, output=args.output,
            dates=tuple(args.date) if args.date else DATES, timeout=args.timeout)
    except ProbeRefused as exc:
        print(json.dumps({"status": "PROBE_REFUSED", "reason_code": exc.reason_code}))
        return 2
    except Exception:
        print(json.dumps({"status": "PROBE_FAILED_CLOSED", "reason_code": "INTERNAL_OR_IO_FAILURE_NO_EXCEPTION_TEXT_LOGGED"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
