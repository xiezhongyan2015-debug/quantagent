"""Bounded query to three documented public peers; every byte retained.

Data from fixed-IP TCP is untrusted, not certified identity or donor admission.
The request serializer is checked against the pytdx author's published packet.
No dependency installation or downloaded executable code is used.
"""
import hashlib
import json
import socket
import struct
from datetime import datetime, timezone
from pathlib import Path
from public_tdx_minute_probe import SETUP, request, decode_bars

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/tdx_documented_modern_peers_20261009'
PEERS = [('119.97.185.59', 7709), ('124.70.133.119', 7709), ('116.205.183.150', 7709)]
SKILL = Path('/Users/leslie/.codex/skills/a-stock-data/SKILL.md')


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def packet(code, market, category, offset, count):
    assert len(code) == 6 and code.isdigit() and category in [8, 9]
    return struct.pack('<HIHHHH6sHHHHIIH', 0x10c, 0x01016408, 0x1c, 0x1c, 0x052d,
                       market, code.encode(), category, 1, offset, count, 0, 0, 0)


def main():
    OUT.mkdir(exist_ok=True)
    normative = '0c01086401011c001c002d0500003030303030310900010000000a0000000000000000000000'
    assert packet('000001', 0, 9, 0, 10).hex() == normative
    reference = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/tdx_protocol_research_20261009/reference_get_security_bars.py.txt'
    assert normative in reference.read_text()
    for host, port in PEERS:
        assert "('%s', %d)" % (host, port) in SKILL.read_text()
    results = []
    for host, port in PEERS:
        folder = OUT / host.replace('.', '_')
        folder.mkdir(exist_ok=True)
        assert not (folder / 'public_setup_0.response.bin').exists(), 'Preserve previous attempt before a repeated query'
        record = {'peer': host, 'port': port, 'peer_identity_authenticated': False, 'requests': []}
        conn = None
        try:
            conn = socket.create_connection((host, port), timeout=8)
            conn.settimeout(8)
            for i, setup in enumerate(SETUP):
                _, trace = request(conn, setup, folder, 'public_setup_' + str(i))
                record['requests'].append(trace)
            for code, market, category, offset, count in [('000001', 0, 9, 0, 1), ('600004', 1, 8, 0, 1), ('600004', 1, 8, 1600, 800)]:
                body, trace = request(conn, packet(code, market, category, offset, count), folder,
                                      code + '_cat%d_offset%d_count%d' % (category, offset, count))
                declared, = struct.unpack_from('<H', body)
                item = {**trace, 'symbol': code + ('.SH' if market else '.SZ'), 'category': category,
                        'offset': offset, 'requested_count': count, 'declared_count': declared,
                        'has_record_payload': len(body) > 2}
                if category == 8 and len(body) > 2:
                    rows = decode_bars(body)
                    dest = folder / (code + '_minute_offset%d_count%d.json' % (offset, count))
                    dest.write_text(json.dumps(rows, indent=2))
                    item.update({'decoded_rows': len(rows), 'decoded_path': str(dest), 'decoded_sha256': sha(dest),
                                 'earliest_timestamp': min((r['timestamp'] for r in rows), default=None),
                                 'latest_timestamp': max((r['timestamp'] for r in rows), default=None)})
                record['requests'].append(item)
            record['status'] = 'bounded_queries_complete'
        except Exception as exc:
            record.update({'status': 'public_probe_failed', 'error_type': type(exc).__name__, 'error': str(exc)[:200]})
        finally:
            if conn is not None:
                conn.close()
        results.append(record)
        print(json.dumps(record), flush=True)
        receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'results': results,
                   'source_code_sha256': sha(Path(__file__)), 'serializer_matches_author_published_packet': True,
                   'reference_text_sha256': sha(reference), 'documented_peer_skill_sha256': sha(SKILL),
                   'reviewed_local_transport_source_sha256': sha(ROOT / 'gru_beta_neutral_20261008/public_tdx_minute_probe.py'),
                   'personal_credentials_used': False, 'dependencies_installed': False,
                   'TLS_or_security_configuration_changed': False, 'fixed_IP_data_treated_as_untrusted': True,
                   'candidate_hours_admitted': 0, 'candidate_changed': False, 'production_ready': False}
        (OUT / 'probe_receipt.json').write_text(json.dumps(receipt, indent=2))
        if any(r.get('decoded_rows', 0) for r in record['requests']):
            break


if __name__ == '__main__':
    main()
