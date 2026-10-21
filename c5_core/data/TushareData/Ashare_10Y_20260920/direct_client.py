"""Tushare-compatible HTTPS client pinned to a Windows physical interface.

No proxy fallback, no credential persistence, certificate validation enabled.
"""
import gzip
import http.client
import ipaddress
import json
import secrets
import socket
import ssl
import struct
import threading
import time


class ApiHTTPError(RuntimeError):
    def __init__(self, status, retry_after, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.retry_after = retry_after


class CallCancelled(RuntimeError):
    pass


def bound_socket(kind, local_ip, interface_index, timeout=15):
    s = socket.socket(socket.AF_INET, kind)
    try:
        s.settimeout(timeout)
        # Windows IP_UNICAST_IF takes a network-byte-order interface index.
        s.setsockopt(socket.IPPROTO_IP, 31, socket.htonl(interface_index))
        # getsockopt returns this option in host order (unlike setsockopt).
        if s.getsockopt(socket.IPPROTO_IP, 31) != interface_index:
            raise RuntimeError("Physical-interface binding verification failed")
        s.bind((local_ip, 0))
        return s
    except BaseException:
        s.close()
        raise


def resolve_direct(host, local_ip, interface_index, servers):
    attempts = []
    for server in servers:
        try:
            ident = secrets.randbelow(65536)
            question = b"".join(bytes([len(x)]) + x.encode("ascii") for x in host.split("."))
            query = struct.pack("!HHHHHH", ident, 0x0100, 1, 0, 0, 0) + question + b"\0" + struct.pack("!HH", 1, 1)
            with bound_socket(socket.SOCK_DGRAM, local_ip, interface_index, 5) as sock:
                sock.connect((server, 53))
                sock.send(query)
                data = sock.recv(65535)
            got_id, flags, qd, an, _, _ = struct.unpack("!HHHHHH", data[:12])
            if got_id != ident or flags & 0x020F:
                raise RuntimeError("Invalid, truncated, or unsuccessful DNS response")

            def skip_name(pos):
                while data[pos]:
                    if data[pos] & 0xC0 == 0xC0:
                        return pos + 2
                    pos += 1 + data[pos]
                return pos + 1

            pos = 12
            for _ in range(qd):
                pos = skip_name(pos) + 4
            ips = []
            for _ in range(an):
                pos = skip_name(pos)
                typ, cls, ttl, length = struct.unpack("!HHIH", data[pos:pos + 10])
                pos += 10
                if typ == 1 and cls == 1 and length == 4:
                    ip = socket.inet_ntoa(data[pos:pos + 4])
                    if not ipaddress.ip_address(ip).is_global:
                        raise RuntimeError("Rejected non-public or Fake-IP DNS result")
                    ips.append(ip)
                pos += length
            attempts.append({"server": server, "ips": ips})
            if ips:
                return list(dict.fromkeys(ips)), attempts
        except Exception as exc:
            attempts.append({"server": server, "error": str(exc)})
    raise RuntimeError("Direct DNS failed: " + json.dumps(attempts))


class DirectHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, ip, local_ip, interface_index):
        super().__init__(host, timeout=60, context=ssl.create_default_context())
        self.remote_ip = ip
        self.local_ip = local_ip
        self.interface_index = interface_index
        self.route = None

    def connect(self):
        raw = bound_socket(socket.SOCK_STREAM, self.local_ip, self.interface_index, 15)
        try:
            raw.connect((self.remote_ip, 443))
            raw.settimeout(60)
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
            self.route = {"source_ip": self.sock.getsockname()[0],
                          "destination_ip": self.sock.getpeername()[0],
                          "destination_port": self.sock.getpeername()[1],
                          "interface_index": self.sock.getsockopt(socket.IPPROTO_IP, 31),
                          "tls": self.sock.version(), "certificate_verified": True,
                          "http_proxy": False, "proxy_fallback": False}
            if self.route["source_ip"] != self.local_ip or self.route["interface_index"] != self.interface_index:
                raise RuntimeError("Direct route verification failed")
        except BaseException:
            raw.close()
            raise


class DirectClient:
    def __init__(self, token, config, max_rps=None):
        self._token = token
        self.config = config
        self.ips, self.dns_attempts = resolve_direct(config["host"], config["local_ip"], config["interface_index"], config["dns_servers"])
        self._local = threading.local()
        self._lock = threading.Lock()
        self._next_request = 0.0
        self._interval = 1 / max_rps if max_rps else 0.0
        self._not_before = 0.0

    def cooldown(self, seconds):
        """Honor server backoff globally; this is not a fixed account rate cap."""
        with self._lock:
            self._not_before = max(self._not_before, time.monotonic() + seconds)

    def close_thread_connection(self):
        conn = getattr(self._local, "connection", None)
        if conn:
            conn.close()
        self._local.connection = None

    def call(self, api, params, fields, cancel_event=None):
        if cancel_event is not None and cancel_event.is_set():
            raise CallCancelled("Batch backoff requested before network call")
        # Optional legacy benchmark pacing. Production passes None (no RPS cap).
        with self._lock:
            scheduled = max(time.monotonic(), self._next_request)
            self._next_request = scheduled + self._interval
        wait = scheduled - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise CallCancelled("Batch backoff requested during cooldown")
            with self._lock:
                backoff = self._not_before - time.monotonic()
            if backoff <= 0:
                break
            time.sleep(min(backoff, 1.0))
        conn = getattr(self._local, "connection", None)
        if conn is None:
            conn = DirectHTTPSConnection(self.config["host"], self.ips[0], self.config["local_ip"], self.config["interface_index"])
            self._local.connection = conn
        payload = json.dumps({"api_name": api, "token": self._token, "params": params, "fields": ",".join(fields)}, separators=(",", ":")).encode()
        started = time.monotonic()
        started_unix = time.time()
        try:
            conn.request("POST", "/", body=payload,
                         headers={"Content-Type": "application/json", "Accept-Encoding": "gzip", "User-Agent": "TushareResearchDirect/1.0"})
            response = conn.getresponse()
            raw = response.read()
            elapsed = time.monotonic() - started
            body = gzip.decompress(raw) if response.getheader("Content-Encoding") == "gzip" else raw
            # Only response data is persisted. Remove unexpected token echoes.
            body = body.replace(self._token.encode(), b"[REDACTED]")
            try:
                obj = json.loads(body)
            except (ValueError, UnicodeError):
                raise ApiHTTPError(response.status, response.getheader("Retry-After"), body[:250].decode("utf-8", errors="replace"))
            if response.status >= 400:
                raise ApiHTTPError(response.status, response.getheader("Retry-After"), str(obj.get("msg") or obj.get("message") or "API request rejected"))
            return obj, {"http_status": response.status, "network_seconds": elapsed,
                         "request_started_unix": started_unix, "response_finished_unix": time.time(),
                         "wire_bytes": len(raw), "decoded_bytes": len(body), "route": conn.route,
                         "rate_limit_header": response.getheader("Retry-After")}
        except BaseException:
            self.close_thread_connection()
            raise
