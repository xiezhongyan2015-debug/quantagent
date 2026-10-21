"""Network-only test. Does not authenticate, upload files, or trade."""
import argparse
import concurrent.futures
import datetime as dt
import json
from pathlib import Path
import socket


def probe(host, port, timeout=6):
    result = {"host": host, "port": port}
    try:
        with socket.create_connection((host, port), timeout=timeout) as connection:
            result["tcp"] = "open"
            if port == 22:
                connection.settimeout(timeout)
                banner = connection.recv(256).decode("ascii", errors="replace").strip()
                result["banner"] = banner
                result["ssh_handshake"] = banner.startswith("SSH-")
    except OSError as error:
        result.update(tcp="unavailable", reason=str(error))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.0.2.10")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(probe, args.host, port) for port in (22, 3389)]
        ports = [future.result() for future in futures]
    report = {"at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
              "ports": ports, "authentication_tested": False}
    content = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(content + "\n", encoding="utf-8")
    print(content)


if __name__ == "__main__":
    main()
