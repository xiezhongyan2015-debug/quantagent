"""Bounded public TDX-protocol probe, retaining packets and no credentials.

Reference: rainx/pytdx parser and helper sources (downloaded as text only).
Fixed-IP TCP data is explicitly untrusted, never an authenticated provenance
source; no donor admission occurs in this probe.
"""
import hashlib
import json
import socket
import struct
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/gru_beta_neutral_20261008/public_source_probes/tdx_protocol_research_20261009'
PEERS = [('180.153.18.170',7709),('180.153.18.172',80),('202.108.253.139',80),('60.191.117.167',7709)]
SETUP = [bytes.fromhex(s) for s in [
    '0c0218930001030003000d0001',
    '0c0218940001030003000d0002',
    '0c031899000120002000db0fd5d0c9ccd6a4a8af0000008fc22540130000d500c9ccbdf0d7ea00000002']]


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle,'sha256').hexdigest()


def recv_exact(conn, size):
    assert 0 <= size <= 65535
    chunks = bytearray()
    while len(chunks) < size:
        part = conn.recv(size-len(chunks))
        if not part: raise EOFError('Public peer closed the connection')
        chunks.extend(part)
    return bytes(chunks)


def request(conn, packet, folder, name):
    conn.sendall(packet)
    head = recv_exact(conn,16)
    _,_,_,size,expanded = struct.unpack('<IIIHH',head)
    assert size > 0 and expanded > 0
    payload = recv_exact(conn,size)
    raw = head+payload
    dest = folder/(name+'.response.bin'); dest.write_bytes(raw)
    (folder/(name+'.request.bin')).write_bytes(packet)
    if size != expanded:
        decompressor = zlib.decompressobj()
        body = decompressor.decompress(payload,expanded+1)
        assert decompressor.eof and not decompressor.unconsumed_tail and len(body) == expanded
    else: body = payload
    return body, {'request_sha256':hashlib.sha256(packet).hexdigest(),'response_path':str(dest),
                  'response_sha256':sha(dest),'bytes':len(raw),'decoded_bytes':len(body)}


def signed_price(body, position):
    start = body[position];value = start & 63;negative = bool(start & 64);shift = 6
    while start & 128:
        position += 1
        start = body[position]; value += (start & 127) << shift; shift += 7
        assert shift <= 34
    return (-value if negative else value), position+1


def tdx_volume(value):
    exponent = value >> 24
    high = (value >> 16) & 255; middle = (value >> 8) & 255; low = value & 255
    result = 2.**(2*exponent-127)
    if high > 128:
        result += 2.**(2*exponent-134)*(128+2*(high & 127))
    else:
        result += 2.**(2*exponent-134)*high
    multiplier = 2 if high & 128 else 1
    return result+multiplier*(2.**(2*exponent-142)*middle+2.**(2*exponent-150)*low)


def decode_bars(body):
    count, = struct.unpack_from('<H',body,0)
    assert count <= 800
    if count and len(body) == 2:
        raise ValueError(f'Peer declared {count} records but supplied no record payload')
    position = 2; previous = 0; rows = []
    for _ in range(count):
        packed_day,clock = struct.unpack_from('<HH',body,position); position += 4
        year = (packed_day >> 11)+2004;month = (packed_day % 2048)//100;day = packed_day % 2048 % 100
        timestamp = datetime(year,month,day,clock//60,clock%60)
        differences = []
        for k in range(4):
            difference,position = signed_price(body,position);differences.append(difference)
        volume_raw,amount_raw = struct.unpack_from('<II',body,position); position += 8
        opened = previous+differences[0]
        close,high,low = [opened+v for v in differences[1:]]; previous = close
        volume,amount = tdx_volume(volume_raw),tdx_volume(amount_raw)
        rows.append({'timestamp':timestamp.strftime('%Y-%m-%d %H:%M:00'),'open':opened/1000,
                     'close':close/1000,'high':high/1000,'low':low/1000,
                     'raw_volume_encoded_uint32':volume_raw,'raw_amount_encoded_uint32':amount_raw,
                     'source_volume_lots':volume,'source_amount_cny':amount,
                     'volume_shares':0. if volume < 1e-20 else volume*100,
                     'amount_cny':0. if amount < 1e-20 else amount,
                     'encoded_denormal_volume_zero':volume < 1e-20})
    assert position == len(body), (position,len(body))
    return rows


def bars_request(symbol,offset,count=800):
    assert symbol.endswith(('.SH','.SZ')) and symbol[:6].isdigit() and len(symbol)==9
    assert 0 <= offset <= 65000 and 1 <= count <= 800
    return struct.pack('<HIHHHH6sHHHHIIH',0x10c,0x01016408,0x1c,0x1c,0x052d,
                       1 if symbol.endswith('.SH') else 0,symbol[:6].encode(),8,1,offset,count,0,0,0)


def main():
    OUT.mkdir(exist_ok=True)
    references = json.loads((OUT/'source_reference_receipt.json').read_text())
    for r in references: assert sha(ROOT/r['path']) == r['sha256'] and not r['downloaded_code_executed']
    results = []
    for host,port in PEERS:
        record = {'peer':host,'port':port,'peer_identity_authenticated':False,'tls_used':False,
                  'personal_credentials_used':False,'candidate_admission':False,'requests':[]}
        folder = OUT/host.replace('.','_');folder.mkdir(exist_ok=True)
        conn = None
        try:
            conn = socket.create_connection((host,port),timeout=8); conn.settimeout(8)
            for n,packet in enumerate(SETUP):
                _,trace = request(conn,packet,folder,'public_setup_'+str(n));record['requests'].append(trace)
            for symbol,offset,count in [('600004.SH',0,10),('600004.SH',1600,800),('688709.SH',1600,800)]:
                packet = bars_request(symbol,offset,count)
                body,trace = request(conn,packet,folder,symbol+'_m1_offset'+str(offset))
                record['requests'].append({**trace,'symbol':symbol,'offset':offset,'requested_count':count})
                rows = decode_bars(body);dest = folder/(symbol+'_m1_offset'+str(offset)+'.json');dest.write_text(json.dumps(rows,indent=2))
                dates = {}
                for row in rows: dates[row['timestamp'][:10]] = dates.get(row['timestamp'][:10],0)+1
                record['requests'][-1].update({'rows':len(rows),'stockday_counts':dates,
                                               'decoded_path':str(dest),'decoded_sha256':sha(dest)})
                time.sleep(.5)
            record['status'] = 'public_data_response_obtained'
        except Exception as exc:
            record.update({'status':'public_probe_failed','error_type':type(exc).__name__,'error':str(exc)[:200]})
        finally:
            if conn is not None: conn.close()
        results.append(record)
        print(json.dumps(record),flush=True)
        receipt = {'checked_utc':datetime.now(timezone.utc).isoformat(),'source_code_sha256':sha(Path(__file__)),
                   'source_reference_receipt_sha256':sha(OUT/'source_reference_receipt.json'),'results':results,
                   'third_party_downloaded_code_executed':False,'dependencies_installed':False,
                   'fixed_IP_TCP_data_treated_as_untrusted_only':True,'candidate_values_changed':False,'production_ready':False}
        (OUT/'public_probe_receipt.json').write_text(json.dumps(receipt,indent=2))


if __name__ == '__main__': main()
