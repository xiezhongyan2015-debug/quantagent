"""Bounded, anonymous raw-minute source probe, isolated from GRU training.

Loads only the PyPI-hash-verified quote parser from the saved source archive.
No installer, trading client, best-IP search, heartbeat or automatic retry runs.
All received TCP bytes remain alongside the parsed response and failures.
"""
from pathlib import Path
import datetime
import hashlib
import json
import signal
import socket
import sys
import tarfile


BASE = Path(__file__).parent / "trading_data" / "future_minute_probe"
ARCHIVE_SHA = "91ccac0919cc28a9f8c4aee19d770fab06d38da9503148dfe78bdc200706afad"
SERVERS = [("119.97.185.59", 7709), ("124.70.133.119", 7709), ("116.205.183.150", 7709)]


def deadline(*_):
    raise TimeoutError("12-second source probe deadline")


def main():
    assert hashlib.sha256((BASE / "pytdx-1.72.tar.gz").read_bytes()).hexdigest() == ARCHIVE_SHA
    with tarfile.open(BASE / "pytdx-1.72.tar.gz") as archive:
        for member in archive.getmembers():
            if member.isfile() and member.name.startswith("pytdx-1.72/pytdx/") and member.name.endswith(".py"):
                path = BASE / "source_original" / Path(*Path(member.name).parts[1:])
                assert path.read_bytes() == archive.extractfile(member).read(), member.name
    sys.path.insert(0, str(BASE / "source_original"))
    from pytdx import base_socket_client as client_module
    from pytdx.parser.setup_commands import SetupCmd1, SetupCmd2, SetupCmd3
    from pytdx.parser.get_security_bars import GetSecurityBarsCmd

    class CaptureSocket(socket.socket):
        def __init__(self, *args):
            super().__init__(*args)
            # The SDK's named-super constructor refers to its mutable global
            # class name; a direct socket subclass avoids that recursion while
            # retaining the same traffic counters consumed by its raw parser.
            self.send_pkg_num = self.recv_pkg_num = 0
            self.send_pkg_bytes = self.recv_pkg_bytes = 0
            self.last_api_send_bytes = self.last_api_recv_bytes = 0
            self.first_pkg_send_time = None
            self.received = []
            self.sent = []

        def send(self, data, *args):
            sent = super().send(data, *args)
            self.sent.append(bytes(data[:sent]))
            return sent

        def recv(self, count, *args):
            data = super().recv(count, *args)
            self.received.append(data)
            return data

    client_module.TrafficStatSocket = CaptureSocket

    class QuoteProbe(client_module.BaseSocketClient):
        def setup(self):
            for command in (SetupCmd1, SetupCmd2, SetupCmd3):
                command(self.client).call_api()

        def bars(self, start, count):
            command = GetSecurityBarsCmd(self.client)
            command.setParams(8, 1, "600004", start, count)
            return command.call_api()

    signal.signal(signal.SIGALRM, deadline)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    records = []
    for index, (host, port) in enumerate(SERVERS):
        api = QuoteProbe(heartbeat=False, auto_retry=False, raise_exception=True)
        record = {"host": host, "port": port, "symbol": "600004.SH", "category": 8,
                  "sdk_archive_sha256": ARCHIVE_SHA, "anonymous_quote_protocol_only": True,
                  "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        parsed = []
        try:
            signal.setitimer(signal.ITIMER_REAL, 12)
            api.connect(host, port, time_out=3)
            validation = api.bars(0, 1)
            assert validation and validation[0]["open"] > 0, "TCP handshake alone is insufficient"
            for offset in (800, 0):
                page = api.bars(offset, 800)
                assert page and len(page) <= 800
                parsed.extend(page)
            record.update(status="REAL_MINUTE_RESPONSE_REQUIRES_SEMANTIC_AUDIT", rows=len(parsed),
                first=min(x["datetime"] for x in parsed), last=max(x["datetime"] for x in parsed))
        except Exception as exc:
            record.update(status="failed", error_type=type(exc).__name__, detail=str(exc))
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            captured_socket = api.client
            for kind in ("received", "sent"):
                chunks = getattr(captured_socket, kind, [])
                data = b"".join(chunks)
                path = BASE / f"probe_{stamp}_{index}_{kind}.bin"
                with path.open("xb") as stream:
                    stream.write(data)
                record[kind] = {"path": str(path), "bytes": len(data),
                    "chunk_lengths": [len(x) for x in chunks], "sha256": hashlib.sha256(data).hexdigest()}
            try:
                api.disconnect()
            except Exception:
                if captured_socket:
                    captured_socket.close()
        if parsed:
            path = BASE / f"probe_{stamp}_{index}_parsed.json"
            with path.open("x") as stream:
                json.dump(parsed, stream, indent=2, allow_nan=False)
            record["parsed_path"] = str(path)
            record["parsed_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        record["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        records.append(record)
        print(json.dumps(record), flush=True)
        if record["status"] != "failed":
            break
    report = {"records": records, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "not_used_as_training_or_forecast_input": True, "full_PLAN_complete": False}
    with (BASE / f"source_probe_{stamp}.json").open("x") as stream:
        json.dump(report, stream, indent=2)


if __name__ == "__main__":
    main()
