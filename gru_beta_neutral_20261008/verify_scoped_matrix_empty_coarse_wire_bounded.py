"""Verify 11 actual empty responses; never interpret an empty M5 as a halt."""
import zlib
from . import bounded_tail_pipeline as p
from .acquire_scoped_remaining_native_matrix_columns_bounded import DEST

def main():
    path=DEST/'coarse_reference_diagnostic.json';d=p.json.loads(path.read_text());assert d['status']=='complete11_native_day_coarse_diagnostic_no_admission' and len(d['results'])==11
    seen=set();pins=[]
    for r in d['results']:
        assert (r['symbol'],r['date']) not in seen;seen.add((r['symbol'],r['date']));assert r['symbol']=='603866.SH' and not r['strict_coarse_pass_slots'] and r['reference_rows']==0
        wire=p.Path(r['wire_path']);raw=wire.read_bytes();assert p.hashlib.sha256(raw).hexdigest()==r['wire_sha256'];assert raw.endswith(b'<![CDATA[]]>\n') and len(raw)<1024*1024
        header=raw[:21].decode().split('\x01');assert header[1]=='96';decoder=zlib.decompressobj();text=decoder.decompress(raw[21:21+int(header[2])],1024*1024);assert decoder.eof and not decoder.unconsumed_tail
        parts=text.decode().split('\x01');assert parts[0]=='0' and parts[2]=='query_history_k_data_plus' and parts[7]=='sh.603866' and parts[9]==parts[10]==r['date'] and parts[11]=='5' and parts[12]=='3' and parts[6]==''
        q=p.json.loads(wire.with_suffix('.json').read_text());assert q['sha256']==r['wire_sha256'] and q['start']==q['end']==r['date'] and q['data']==[]
        pins.append(dict(path=str(wire),sha256=r['wire_sha256'],verified_identity=p.identity(wire)))
    assert sum(len(r['slots']) for r in d['results'])==44 and d['strict_coarse_pass_hours']==0 and d['hours_without_complete_references']==44
    output=DEST/'independent_coarse_wire_verification.json';assert not output.exists();p.atomic_json(output,dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_coarse_diagnostic_sha256=p.sha(path),actual_empty_stockday_responses_verified=11,original_missing_hours_still_without_complete_references=44,wire_pins=pins,empty_coarse_response_not_relabelled_suspension=True,candidate_hours_admitted=0,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))

if __name__=='__main__':main()
