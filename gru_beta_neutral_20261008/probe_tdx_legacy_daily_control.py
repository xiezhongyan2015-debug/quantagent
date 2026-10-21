"""Distinguish empty daily and minute payloads on one known public peer."""
import hashlib
import json
import socket
import struct
from datetime import datetime, timezone
from pathlib import Path
from public_tdx_minute_probe import SETUP, request
from probe_documented_tdx_modern_peers import packet

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/tdx_legacy_daily_control_20261009'


def main():
    OUT.mkdir(exist_ok=True)
    assert not (OUT / 'public_setup_0.response.bin').exists()
    records = []
    with socket.create_connection(('180.153.18.170', 7709), timeout=8) as conn:
        conn.settimeout(8)
        for i, setup in enumerate(SETUP):
            _, trace = request(conn, setup, OUT, 'public_setup_' + str(i))
            records.append(trace)
        for code, market, category in [('000001', 0, 9), ('600004', 1, 9), ('600004', 1, 8)]:
            body, trace = request(conn, packet(code, market, category, 0, 10), OUT,
                                  code + '_cat' + str(category) + '_count10')
            declared, = struct.unpack_from('<H', body)
            records.append({**trace, 'code': code, 'market': market, 'category': category,
                            'declared_count': declared, 'requested_count': 10,
                            'body_hex_when_no_record_payload': body.hex() if len(body) == 2 else None,
                            'has_record_payload': len(body) > 2})
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'peer': '180.153.18.170',
              'records': records, 'source_code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'daily_used_as_control_only': True, 'personal_credentials_used': False,
              'peer_identity_authenticated': False, 'candidate_changed': False, 'production_ready': False}
    (OUT / 'receipt.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
