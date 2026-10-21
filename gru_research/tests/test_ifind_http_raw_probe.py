"""Local fixtures only. No real token read, auth/market request or research data."""
import contextlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gru_research import ifind_http_raw_probe as probe

FIXTURE_SECRET = "fixture-only-access-token-for-local-tests"
ENV_NAME = "FIXTURE_ONLY_EXPLICIT_TOKEN"


def envelope(tables=None):
    return json.dumps({"errorcode": 0, "errmsg": "", "tables": {"opaque": "not_a_documented_minute_schema"} if tables is None else tables,
        "datatype": {}, "inputParams": {}, "perf": 1, "dataVol": 1}).encode()


class ProbeTests(unittest.TestCase):
    def collect_wire_fixture(self, wire, folder, stream_class=io.BytesIO):
        """Exercise real HTTPResponse framing, replacing only the connection."""
        class Socket:
            def makefile(self, mode):
                if mode != "rb":
                    raise AssertionError("unexpected socket fixture mode")
                return stream_class(wire)
        response = http.client.HTTPResponse(Socket(), method="POST")
        response.begin()
        class Connection:
            def __init__(self):
                self.calls = 0
                self.closed = False
            def request(self, *args, **kwargs):
                self.calls += 1
            def getresponse(self):
                return response
            def close(self):
                self.closed = True
                response.close()
        connection = Connection()
        with patch.object(probe.os, "environ", {ENV_NAME: FIXTURE_SECRET}), \
             patch.object(probe, "verify_official_provenance", return_value={"fixture_documentation": True}), \
             patch.object(probe.http.client, "HTTPSConnection", return_value=connection) as factory:
            result = probe.run_probe(execute=True, token_env=ENV_NAME, output=folder, dates=(probe.DATES[0],))
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(connection.calls, 1)
        self.assertTrue(connection.closed)
        return result["records"][0]

    def collect_fixture(self, response, folder, **kwargs):
        # Replace the entire mapping without enumerating any real environment.
        with patch.object(probe.os, "environ", {ENV_NAME: FIXTURE_SECRET}), \
             patch.object(probe, "verify_official_provenance", return_value={"fixture_documentation": True}), \
             patch.object(probe, "request_raw", side_effect=response if isinstance(response, Exception) else None,
                          return_value=response) as transport:
            result = probe.run_probe(execute=True, token_env=ENV_NAME, output=folder, **kwargs)
        return result, transport

    def test_default_dry_run_never_reads_secret_network_or_writes_output(self):
        class NoEnvironmentRead:
            # argparse/gettext consults ordinary locale/terminal variables. No
            # token lookup or enumeration is permitted; no real values returned.
            allowed_runtime_metadata = {"LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG", "COLUMNS", "LINES"}
            def get(self, key):
                if key not in self.allowed_runtime_metadata:
                    raise AssertionError("dry-run must not read a secret")
                return None
            def __getitem__(self, key):
                if key not in self.allowed_runtime_metadata:
                    raise AssertionError("dry-run must not read a secret")
                raise KeyError(key)
        with tempfile.TemporaryDirectory() as td, patch.object(probe.os, "environ", NoEnvironmentRead()), \
             patch.object(probe, "request_raw", side_effect=AssertionError("network forbidden")), \
             patch.object(probe, "read_access_token", side_effect=AssertionError("secret reader forbidden")), \
             patch.object(probe, "verify_official_provenance", side_effect=AssertionError("no execution preflight")):
            destination = Path(td) / "not_created"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(probe.main(["--token-env", ENV_NAME, "--output", str(destination)]), 0)
            result = json.loads(stdout.getvalue())
            self.assertFalse(destination.exists())
            self.assertEqual(result["market_network_calls"], 0)
            self.assertFalse(result["secret_read"])
            self.assertFalse(result["response_files_written"])

    def test_fixed_request_budget_and_all_explicit_raw_parameters(self):
        plan = probe.build_plan()
        self.assertEqual(plan["request_count"], 5)
        self.assertEqual([x["date"] for x in plan["requests"]], list(probe.DATES))
        for item in plan["requests"]:
            self.assertEqual(item["body"], {"codes": "600004.SH", "indicators": "open,high,low,close,volume,amount",
                "starttime": item["date"] + " 09:25:00", "endtime": item["date"] + " 15:00:00",
                "functionpara": {"Interval": "1", "Fill": "Original", "CPS": "no", "Timeformat": "BeiJingTime"}})
        for bad in [probe.DATES * 2, (probe.DATES[0],) * 2, ("2026-10-08",), ()]:
            with self.assertRaises(probe.ProbeRefused):
                probe.build_plan(bad)

    def test_execute_requires_explicit_environment_and_bounded_timeout(self):
        with patch.object(probe.os, "environ", {}), patch.object(probe, "request_raw") as network, \
             patch.object(probe, "verify_official_provenance", return_value={}):
            for kw in [{"token_env": None, "output": "unused"}, {"token_env": ENV_NAME, "output": "unused"},
                       {"token_env": ENV_NAME, "output": "unused", "timeout": 21}, {"token_env": ENV_NAME}]:
                with self.assertRaises(probe.ProbeRefused):
                    probe.run_probe(execute=True, **kw)
            network.assert_not_called()

    def test_five_safe_raw_fixtures_are_exact_bytes_but_never_normalized(self):
        raw = envelope()
        response = probe.RawResponse(200, [("Content-Type", "application/json"), ("Set-Cookie", "not persisted")], raw, probe.utc_now())
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "fixtures"
            result, transport = self.collect_fixture(response, out)
            self.assertEqual(transport.call_count, 5)
            self.assertEqual(result["requests_attempted"], 5)
            for day in probe.DATES:
                self.assertEqual((out / (day + ".response.bin")).read_bytes(), raw)
                record = json.loads((out / (day + ".receipt.json")).read_text())
                self.assertEqual(record["response_bytes_sha256"], probe.digest(raw))
                self.assertEqual(record["status"], "RESPONSE_SCHEMA_UNVERIFIED")
                self.assertEqual(record["safe_response_headers"], {"content-type": "application/json"})
                self.assertFalse(record["normalization_performed"])
            with self.assertRaises(probe.ProbeRefused):
                self.collect_fixture(response, out)
            self.assertFalse(result["model_or_trading_admission"])

    def test_failure_bodies_and_exception_text_never_become_raw_files(self):
        cases = [probe.RawResponse(401, [], b"benign HTTP failure", probe.utc_now()),
                 probe.RawResponse(200, [], b'{"errorcode":-1,"errmsg":"not logged"}', probe.utc_now()),
                 probe.RawResponse(200, [], b"not JSON", probe.utc_now()),
                 probe.RawResponse(200, [], envelope(), probe.utc_now(), full_body_received=False),
                 RuntimeError("transport accidentally echoed " + FIXTURE_SECRET)]
        for i, response in enumerate(cases):
            with self.subTest(i=i), tempfile.TemporaryDirectory() as td:
                out = Path(td) / "fixtures"
                result, transport = self.collect_fixture(response, out)
                self.assertEqual(transport.call_count, 1)
                self.assertEqual(result["requests_attempted"], 1)
                self.assertFalse(list(out.glob("*.response.bin")))
                evidence = b"".join(f.read_bytes() for f in out.iterdir())
                self.assertNotIn(FIXTURE_SECRET.encode(), evidence)
                self.assertNotIn(b"not logged", evidence)

    def test_secret_echo_direct_escaped_and_header_never_persisted(self):
        escaped = "".join("\\u%04x" % ord(c) for c in FIXTURE_SECRET)
        cases = [b'{"errorcode":0,"errmsg":"' + FIXTURE_SECRET.encode() + b'"}',
                 ('{"errorcode":0,"errmsg":"' + escaped + '"}').encode(),
                 b'{"access_token":"another-credential-that-is-not-the-configured-token"}']
        for raw in cases:
            with tempfile.TemporaryDirectory() as td:
                response = probe.RawResponse(200, [("X-Request-ID", FIXTURE_SECRET), ("Authorization", FIXTURE_SECRET)], raw, probe.utc_now())
                out = Path(td) / "fixtures"
                result, transport = self.collect_fixture(response, out)
                record = result["records"][0]
                self.assertEqual(transport.call_count, 1)
                self.assertFalse(record["raw_response_saved"])
                self.assertFalse(list(out.glob("*.response.bin")))
                self.assertEqual(record["response_bytes_sha256"], probe.digest(raw))
                self.assertEqual(record["status"], "SECRET_OR_CREDENTIAL_ECHO_HASH_ONLY_BODY_NOT_SAVED")
                self.assertEqual(record["safe_response_headers"], {})
                self.assertNotIn(FIXTURE_SECRET.encode(), b"".join(f.read_bytes() for f in out.iterdir()))

    def test_empty_and_unknown_schema_are_never_empty_success(self):
        for raw in [envelope([]), envelope("opaque"), b'{}', b'{"errorcode":0,"tables":[]}', b'{"data":[]}']:
            diagnostic = probe.inspect_envelope(raw)
            self.assertIn("UNVERIFIED", diagnostic["status"])
            self.assertFalse(diagnostic["continue_budget"])
        for raw in [b'{"errorcode":0,"errorcode":1}', b'{"errorcode":NaN}']:
            self.assertEqual(probe.inspect_envelope(raw)["status"], "INVALID_JSON_RESPONSE")

    def test_https_transport_exact_host_path_header_tls_and_no_retry(self):
        class Connection:
            def __init__(self):
                self.calls = []
                self.closed = False
            def request(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                raise TimeoutError("fixture only")
            def close(self):
                self.closed = True
        connection = Connection()
        with patch.object(probe.http.client, "HTTPSConnection", return_value=connection) as factory:
            with self.assertRaises(TimeoutError):
                probe.request_raw(probe.request_body(probe.DATES[0]), FIXTURE_SECRET, 20)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(factory.call_args.args, ("quantapi.51ifind.com",))
        self.assertEqual(factory.call_args.kwargs["timeout"], 20)
        tls = factory.call_args.kwargs["context"]
        self.assertTrue(tls.check_hostname)
        self.assertEqual(tls.verify_mode, probe.ssl.CERT_REQUIRED)
        self.assertEqual(len(connection.calls), 1)
        self.assertEqual(connection.calls[0][0], ("POST", "/api/v1/high_frequency"))
        self.assertEqual(connection.calls[0][1]["headers"]["access_token"], FIXTURE_SECRET)
        self.assertNotIn("Authorization", connection.calls[0][1]["headers"])
        self.assertTrue(connection.closed)
        bad = probe.request_body(probe.DATES[0])
        bad["codes"] = "000001.SZ"
        with patch.object(probe.http.client, "HTTPSConnection") as forbidden:
            for body, timeout in [(bad, 20), (probe.request_body(probe.DATES[0]), 21)]:
                with self.assertRaises(probe.ProbeRefused):
                    probe.request_raw(body, FIXTURE_SECRET, timeout)
            forbidden.assert_not_called()

    def test_real_HTTPResponse_content_length_truncation_and_complete_framing(self):
        raw = envelope()
        cases = [
            (b"Content-Length: " + str(len(raw)).encode() + b"\r\n", raw, True),
            # The retained prefix is fully valid JSON; six absent tail spaces
            # must still fail HTTP integrity and never be persisted as original.
            (b"Content-Length: " + str(len(raw) + 6).encode() + b"\r\n", raw, False),
            (b"Content-Length: -1\r\n", raw, False),
            (b"Content-Length: " + str(len(raw)).encode() + b"\r\nContent-Length: " + str(len(raw)).encode() + b"\r\n", raw, False),
            (b"Content-Length: " + str(len(raw)).encode() + b"\r\nTransfer-Encoding: chunked\r\n", raw, False),
            (b"Connection: close\r\n", raw, True),
        ]
        for headers, payload, complete in cases:
            with self.subTest(headers=headers), tempfile.TemporaryDirectory() as td:
                out = Path(td) / "wire_fixture"
                record = self.collect_wire_fixture(b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n" + payload, out)
                self.assertEqual(record["full_response_received"], complete)
                self.assertEqual(record["raw_response_saved"], complete)
                if complete:
                    self.assertEqual((out / (probe.DATES[0] + ".response.bin")).read_bytes(), payload)
                    self.assertEqual(record["transport_framing_diagnostic"]["status"], "HTTP_MESSAGE_COMPLETE_VERIFIED")
                else:
                    self.assertFalse(list(out.glob("*.response.bin")))
                    self.assertEqual(record["status"], "INCOMPLETE_OR_OVER_BUDGET_RESPONSE_HASH_ONLY")
                self.assertFalse(record["transport_framing_diagnostic"]["end_to_end_hard_deadline_enforced"])
        with tempfile.TemporaryDirectory() as td, patch.object(probe.time, "monotonic", side_effect=[10.0, 31.0]):
            out = Path(td) / "wire_fixture_elapsed_budget"
            record = self.collect_wire_fixture(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(raw)).encode() + b"\r\n\r\n" + raw, out)
            self.assertFalse(record["full_response_received"])
            self.assertFalse(record["raw_response_saved"])
            self.assertFalse(record["transport_framing_diagnostic"]["post_return_elapsed_budget_satisfied"])

    def test_real_HTTPResponse_chunked_terminator_trailers_and_close_EOF(self):
        raw = envelope()
        size = format(len(raw), "x").encode()
        complete_chunk = size + b"\r\n" + raw + b"\r\n0\r\n\r\n"
        cases = [(complete_chunk, True),
                 (size + b"\r\n" + raw + b"\r\n0\r\nX-Trace: fixture\r\n\r\n", True),
                 (size + b"\r\n" + raw + b"\r\n0\r\n", False),
                 (size + b"\r\n" + raw + b"\r\n", False),
                 (size + b"\r\n" + raw + b"xx0\r\n\r\n", False),
                 (format(len(raw) + 6, "x").encode() + b"\r\n" + raw, False)]
        for payload, complete in cases:
            with self.subTest(complete=complete), tempfile.TemporaryDirectory() as td:
                out = Path(td) / "wire_fixture"
                wire = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + payload
                record = self.collect_wire_fixture(wire, out)
                self.assertEqual(record["full_response_received"], complete)
                self.assertEqual(record["raw_response_saved"], complete)
                if complete:
                    self.assertEqual((out / (probe.DATES[0] + ".response.bin")).read_bytes(), raw)
                    self.assertTrue(record["transport_framing_diagnostic"]["chunked_terminator_and_trailers_verified"])
                else:
                    self.assertFalse(list(out.glob("*.response.bin")))
        class ShortFirstBodyRead(io.BytesIO):
            def __init__(self, value):
                super().__init__(value)
                self.body_reads = 0
            def read(self, amount=-1):
                self.body_reads += 1
                return super().read(len(raw) if self.body_reads == 1 else amount)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "wire_fixture"
            record = self.collect_wire_fixture(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n" + raw + b"x", out, ShortFirstBodyRead)
            self.assertFalse(record["full_response_received"])
            self.assertFalse(record["raw_response_saved"])
            self.assertFalse(record["transport_framing_diagnostic"]["close_delimited_EOF_verified"])


if __name__ == "__main__":
    unittest.main()
