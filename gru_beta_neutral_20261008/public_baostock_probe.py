"""Bounded anonymous public-data probe, implemented from inspected vendor protocol.

No SDK installation or personal credentials. Preserve wire bytes and decoded
responses; this module never admits a coarse bar as a one-minute observation.
"""
import fcntl
import hashlib
import json
import socket
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/baostock'
SEP = '\x01'
END = b'<![CDATA[]]>\n'


class PublicClient:
    def __init__(self, output=OUT, max_response_bytes=32 * 1024 * 1024):
        if not isinstance(max_response_bytes, int) or not 8192 <= max_response_bytes <= 32 * 1024 * 1024:
            raise ValueError('Invalid bounded public response cap')
        self.max_response_bytes = max_response_bytes
        self.output = output
        output.mkdir(parents=True, exist_ok=True)
        self.socket = socket.create_connection(('public-api.baostock.com', 10030), timeout=12)
        self.socket.settimeout(20)
        self.records = []

    def exchange(self, message_type, body, name):
        header = f'00.9.30{SEP}{message_type}{SEP}{len(body.encode()):010d}'
        packet = (header + body).encode()
        self.socket.sendall(packet + SEP.encode() + str(zlib.crc32(packet)).encode() + b'\n')
        wire = bytearray()
        while not wire.endswith(END):
            chunk = self.socket.recv(min(8192, self.max_response_bytes + 1 - len(wire)))
            if not chunk:
                raise ConnectionError('Public source closed before response terminator')
            wire.extend(chunk)
            if len(wire) > self.max_response_bytes:
                raise ValueError('Response exceeded probe byte cap')
        raw = bytes(wire)
        path = self.output / (name + '.bin')
        path.write_bytes(raw)
        h = raw[:21].decode().split(SEP)
        if h[1] == '96':
            compressed = raw[21:21+int(h[2])]
            dec = zlib.decompressobj()
            decoded = dec.decompress(compressed, self.max_response_bytes + 1)
            if len(decoded) > self.max_response_bytes:
                raise ValueError('Compressed response exceeded decoded cap')
            if not dec.eof or dec.unconsumed_tail:
                raise ValueError('Compressed response exceeded decoded cap')
            text = decoded.decode()
        else:
            text = raw[21:].decode()
        parts = text.split(SEP)
        result = {'name': name, 'error_code': parts[0], 'error_msg': parts[1],
                  'wire_path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
                  'bytes': len(raw), 'fetched_utc': datetime.now(timezone.utc).isoformat()}
        if len(parts) >= 13 and parts[0] == '0' and message_type == '95':
            result.update(page=parts[4], per_page=parts[5], data=json.loads(parts[6]) if parts[6] else [],
                          code=parts[7], fields=parts[8].split(','), start=parts[9],
                          end=parts[10], frequency=parts[11], adjustflag=parts[12].split('\n')[0])
        if len(parts) >= 11 and parts[0] == '0' and message_type == '15':
            result.update(page=parts[4], per_page=parts[5], data=json.loads(parts[6]) if parts[6] else [],
                          code=parts[7], start=parts[8], end=parts[9],
                          fields=parts[10].split('\n')[0].split(','))
        (self.output / (name + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2))
        self.records.append({k:v for k,v in result.items() if k != 'data'})
        return result

    def login(self):
        r = self.exchange('00', SEP.join(['login','anonymous','123456','0']), 'anonymous_session')
        if r['error_code'] != '0':
            raise RuntimeError(f"Anonymous public session refused: {r['error_code']} {r['error_msg']}")

    def query(self, code, start, end, frequency, page=1):
        fields = ('date,time,code,open,high,low,close,volume,amount,adjustflag'
                  if frequency in ['1','5'] else
                  'date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST')
        if frequency not in ['1','5','d'] or not code.startswith(('sh.','sz.')):
            raise ValueError('Probe outside public market-data whitelist')
        body = SEP.join(['query_history_k_data_plus','anonymous',str(page),'2000',code,fields,start,end,frequency,'3'])
        name = f'{code}_{start}_{end}_{frequency}_p{page}'
        return self.exchange('95', body, name)

    def close(self):
        try:
            self.exchange('02', SEP.join(['logout','anonymous',datetime.now().strftime('%Y%m%d%H%M%S')]), 'anonymous_logout')
        finally:
            self.socket.close()

    def factors(self, code, start='1990-01-01', end='2026-09-23', page=1):
        """Observed corporate-action factors; no interpolation of market prices."""
        if not code.startswith(('sh.', 'sz.')) or len(code) != 9:
            raise ValueError('Factor query outside public stock-code whitelist')
        body = SEP.join(['query_adjust_factor','anonymous',str(page),'2000',code,start,end])
        return self.exchange('15',body,f'{code}_{start}_{end}_factors_p{page}')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    lock = (ROOT/'acquired_data/data_round2/baostock_session.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    client = None
    receipt = {'endpoint':'public-api.baostock.com:10030', 'personal_credentials_used':False,
               'status':'probing', 'one_minute_admitted':False, 'records':[]}
    try:
        client = PublicClient()
        client.login()
        specs = [('sh.600754','2019-06-06','2019-06-06','5'),
                 ('sh.600754','2019-06-06','2019-06-06','d'),
                 ('sz.000034','2024-12-06','2024-12-06','5'),
                 ('sh.600004','2026-09-23','2026-09-23','5'),
                 ('sh.600004','2026-09-23','2026-09-23','d')]
        for code,start,end,frequency in specs:
            r = client.query(code,start,end,frequency)
            print(json.dumps({k:v for k,v in r.items() if k not in ['data','fields']},ensure_ascii=False),flush=True)
            print(json.dumps({'data_shape':str(type(r.get('data'))), 'first_data':str(r.get('data'))[:220]},ensure_ascii=False),flush=True)
            time.sleep(1)
        receipt['status'] = 'responses_received_pending_semantics_validation'
    except Exception as e:
        receipt.update(status='probe_failed', error=f'{type(e).__name__}: {e}')
        print(json.dumps(receipt,ensure_ascii=False),flush=True)
    finally:
        if client:
            try: client.close()
            except Exception as e: receipt['logout_error'] = str(e)
            receipt['records'] = client.records
        receipt['finished_utc'] = datetime.now(timezone.utc).isoformat()
        (OUT/'probe_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
