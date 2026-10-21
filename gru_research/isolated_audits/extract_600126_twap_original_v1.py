"""One authorized bounded original gzip/tar target-only pass; no pool cleaning.

Stop on fixed original 20250210/600126.parquet. Do not parse any other Parquet.
No original sources or providers are modified; no automatic second pass.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "gru_research/artifacts"
NSPACE = ROOT / "gru_research/trading_data/raw_twap_600126_20250210_source_probe_20261002"
TARGET = "A股分钟k线数据/20250210/600126.parquet"
COMPRESSED_CAP = 4527158977
SECONDS_CAP = 1800
PAYLOAD_CAP = 2*1024**2
FINGERPRINT_SHA = "947f5b769eecb7a7d3521482455a59e1e3f7390bdc4e275912d7f13f939cd50a"
HOUR_SHA = "43d5567b73f74ff31febaa71ede3ef56a04ccaa6105f98571cbe4af3f8b35d39"


def require(condition, why):
    if not condition:
        raise ValueError(why)


def identity(p, cap=8*1024**2):
    p = Path(p);require(p.is_file() and p.stat().st_size<=cap,"missing/oversize small identity: "+str(p))
    b = p.read_bytes()
    return {"path":str(p),"bytes":len(b),"sha256":hashlib.sha256(b).hexdigest()}


def stat(p):
    s = p.stat()
    return {"path":str(p),"bytes":s.st_size,"mtime_ns":s.st_mtime_ns,"whole_SHA_recomputed":False}


def write_json(p, value):
    with p.open("x") as stream:
        stream.write(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n")


class Progress:
    def __init__(self, output):
        self.started = time.monotonic();self.last = self.started
        self.compressed = 0;self.decompressed_returned = 0
        self.members = 0;self.last_member = None
        self.output = output;self.stream = (output/"actual_progress_v1.jsonl").open("x")

    def check(self, force=False):
        elapsed = time.monotonic()-self.started
        require(elapsed<=SECONDS_CAP,"1800-second elapsed budget exceeded; no automatic rescan")
        require(self.compressed<=COMPRESSED_CAP,"compressed read budget exceeded")
        if force or time.monotonic()-self.last>=10:
            item = {"observed_utc":datetime.now(timezone.utc).isoformat(),"elapsed_seconds":round(elapsed,3),
                    "actual_compressed_bytes_read":self.compressed,"actual_decompressed_bytes_returned_to_tar":self.decompressed_returned,
                    "tar_members_examined":self.members,"last_header_name":self.last_member,"target_found":False}
            self.stream.write(json.dumps(item,ensure_ascii=False)+"\n");self.stream.flush()
            print(json.dumps(item,ensure_ascii=False),flush=True);self.last = time.monotonic()


class SplitRaw(io.RawIOBase):
    def __init__(self, paths, progress):
        self.paths,self.progress = paths,progress
        self.index = 0;self.handle = paths[0].open("rb")
        self.per_part = [0]*len(paths)

    def readable(self):
        return True

    def readinto(self, buffer):
        self.progress.check()
        require(len(buffer)<=128*1024,"compressed read buffer exceeded128KiB")
        while True:
            remaining = COMPRESSED_CAP-self.progress.compressed
            require(remaining>0,"compressed whole-source cap reached without target")
            count = self.handle.readinto(memoryview(buffer)[:min(len(buffer),remaining)])
            if count:
                self.per_part[self.index] += count;self.progress.compressed += count
                return count
            if self.index==len(self.paths)-1:
                return 0
            self.handle.close();self.index += 1;self.handle = self.paths[self.index].open("rb")

    def close(self):
        self.handle.close();super().close()


class TarReader:
    def __init__(self, zipped, progress):
        self.zipped,self.progress = zipped,progress

    def read(self, size):
        self.progress.check()
        require(0<=size<=128*1024,"unbounded/oversize tar read forbidden")
        data = self.zipped.read(size)
        self.progress.decompressed_returned += len(data)
        return data


def validate(payload, output, hour_path):
    sys.path.insert(0,str(ROOT/"gru_research/.deps"))
    import numpy as np
    import pyarrow.parquet as pq
    table = pq.read_table(io.BytesIO(payload),use_threads=False)
    names = ["股票代码","datetime","datetime_nano","open","high","low","close","volume","qfq_ratio","qfq_open","qfq_high","qfq_low","qfq_close"]
    require(set(names)<=set(table.column_names) and len(table)==240,"original stockday schema/240rows incomplete")
    values = {name:table[name].to_numpy(zero_copy_only=False) for name in names}
    require(np.all(np.asarray(values["股票代码"]).astype(str)=="600126"),"actual original symbol differs")
    ns = np.asarray(values["datetime_nano"],dtype=np.int64)
    local = np.asarray(values["datetime"]).astype("datetime64[ns]").astype(np.int64)
    require(np.array_equal(local-8*3600*10**9,ns),"source dual clock mismatch")
    midnight = int(datetime.fromisoformat("2025-02-10T00:00:00+08:00").timestamp())*10**9
    offsets = np.r_[np.arange(570,690),np.arange(780,900)]
    require(np.array_equal(ns,midnight+offsets*60*10**9),"exact original physical240left timestamps/order differ")
    raw = np.column_stack([values[n] for n in ("open","high","low","close")])
    adj = np.column_stack([values[n] for n in ("qfq_open","qfq_high","qfq_low","qfq_close")])
    factor = np.asarray(values["qfq_ratio"],dtype=np.float64);volume = np.asarray(values["volume"])
    require(np.isfinite(raw).all() and (raw>0).all() and np.isfinite(adj).all() and (adj>0).all(),"raw/adjusted prices invalid")
    require(np.issubdtype(volume.dtype,np.integer) and (volume>=0).all(),"original minute volume invalid/noninteger")
    require((raw[:,1]>=raw[:,[0,2,3]].max(1)).all() and (raw[:,2]<=raw[:,[0,1,3]].min(1)).all(),"actual OHLC ordering invalid")
    require(np.isfinite(factor).all() and (factor>0).all() and np.allclose(factor,factor[0],rtol=1e-7,atol=1e-10),"intraday factor consistency invalid")
    require(np.isclose(adj,raw*factor[:,None],rtol=1e-7,atol=1e-6).all(),"raw/qfq relation inconsistent")
    selected = np.flatnonzero((offsets>=571)&(offsets<576))
    require(len(selected)==5 and np.array_equal(offsets[selected],np.arange(571,576)),"exact091..window incomplete")
    opens = raw[selected,0];window_volume = int(volume[selected].sum())
    require(window_volume>0,"actual5window no volume")
    rows = []
    for pos in selected:
        rows.append({"original_row_ordinal":int(pos),"datetime":str(values["datetime"][pos]),"datetime_nano":int(ns[pos]),
                     "raw_open":float(raw[pos,0]),"raw_open_binary_hex":float(raw[pos,0]).hex(),
                     "raw_high":float(raw[pos,1]),"raw_low":float(raw[pos,2]),"raw_close":float(raw[pos,3]),
                     "volume":int(volume[pos]),"qfq_ratio":float(factor[pos]),"qfq_open":float(adj[pos,0])})
    csv_path = output/"actual_0931_0935_five_original_rows.csv"
    with csv_path.open("x",newline="") as stream:
        writer = csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    old_rows = pq.read_table(hour_path,filters=[("symbol","=","600126.SH"),("slot","=",0)]).to_pylist()
    require(len(old_rows)==1,"frozen target hourly row unavailable/ambiguous")
    old = old_rows[0]
    mean = float(np.mean(opens,dtype=np.float64))
    decimal_mean = sum(Decimal(str(float(x))) for x in opens)/Decimal(5)
    require(old["window_complete"] is True and old["window_tradable"] is True and old["minute_count"]==60,
            "sealed target window quality differs")
    require(old["window_start_time"]==midnight+571*60*10**9 and old["window_end_time"]==midnight+576*60*10**9,"sealed target clock differs")
    require(float(old["window_raw_price"])==mean and old["window_volume"]==window_volume,
            "exact original5mean/volume differs from sealedhour")
    source_report = {"schema_version":1,"status":"PASS_ACTUAL_ORIGINAL240_AND_FIVE_RAW_SOURCE_ROWS_WITH_SEALED_DOUBLE_MEAN_IDENTITY",
                     "source_schema":str(table.schema),"physical_rows":240,"all_exact_left_regular_minutes":True,
                     "source_clock":"datetime localShanghai; datetime_nano UTCns; left/start-minute labels",
                     "minute_row_complete_at":"left+1minute; not independently observed feed arrival",
                     "execution_window_start_local":"2025-02-10T09:31:00+08:00","execution_window_end_local":"2025-02-10T09:36:00+08:00",
                     "actual_five_original_rows":rows,"five_raw_opens_float64":opens.tolist(),"five_open_mean_float64":mean,
                     "five_open_mean_float64_hex":mean.hex(),"source_floats_shortest_roundtrip_decimal_mean_diagnostic":str(decimal_mean),
                     "diagnostic_decimal_not_provider_or_price_rewrite":True,"five_window_volume_shares":window_volume,
                     "volume_unit_evidence":"Inherited actual minute_vs_daily crosscheck from original audited source, not a new provider assumption",
                     "sealed_hourly_slot0":old,"sealed_raw_mean_binary_over_raw_high":mean-float(old["raw_high"]),
                     "all_raw_prices_positive_valid_OHLC":True,"qfq_relation_valid_with_original_tolerance":True,
                     "five_minute_CSV":identity(csv_path),"no_price_provider_or_ledger_rule_modified":True,
                     "no_observed_depth_or_fill_claim":True,"full_PLAN_complete":False}
    write_json(output/"original_five_minute_validation_receipt_v1.json",source_report)
    return source_report


def run():
    sys.dont_write_bytecode = True
    NSPACE.mkdir(exist_ok=True)
    marker = NSPACE/"EXECUTION_STARTED_v1.json"
    require(not marker.exists(),"single-pass execution marker exists; refuse automatic retry")
    write_json(marker,{"schema_version":1,"started_utc":datetime.now(timezone.utc).isoformat(),"fixed_target":TARGET,
                       "authorized_single_source_pass":True,"compressed_cap":COMPRESSED_CAP,"seconds_cap":SECONDS_CAP,"payload_cap":PAYLOAD_CAP})
    output = NSPACE/"attempt1";output.mkdir(exist_ok=False)
    progress = Progress(output)
    result = {"schema_version":1,"status":"RUNNING_TARGET_ONLY_SOURCE_PASS","fixed_target":TARGET,"source_prefix_pass_count":1,
              "started_utc":datetime.now(timezone.utc).isoformat(),"execution_marker":identity(marker),
              "read_budgets":{"compressed_bytes":COMPRESSED_CAP,"elapsed_seconds":SECONDS_CAP,"payload_bytes":PAYLOAD_CAP,
                              "compressed_buffer_bytes":65536,"tar_stream_buffer_bytes":65536},
              "other_Parquets_parsed":0,"whole_source_SHA_recomputed":False,"raw_source_or_provider_modified":False,
              "network_Torch_models_scores_IC_training_normalization_or_ledger_called":False,"full_PLAN_complete":False}
    source_before = {};small_before = {};raw = None;error = None
    try:
        fp_path = ART/"minute_source_fingerprints.json";require(identity(fp_path)["sha256"]==FINGERPRINT_SHA,"original source fingerprint receipt changed")
        fp = json.loads(fp_path.read_bytes());paths = [ROOT/v["name"] for v in fp["parts"]]
        require(len(paths)==5 and sum(v["size_bytes"] for v in fp["parts"])==COMPRESSED_CAP,"original five-part source budget differs")
        for path,declared in zip(paths,fp["parts"],strict=True):
            current = stat(path);require(current["bytes"]==declared["size_bytes"],"source part size differs")
            source_before[str(path)] = {**current,"inherited_whole_SHA":declared["sha256"]}
        hour_path = ART/"minute_hourly_raw/2025-02-10.parquet"
        small = {fp_path:FINGERPRINT_SHA,hour_path:HOUR_SHA,
                 ART/"minute_volume_crosscheck.json":"4d2f03c09498c1028acaf30bb95d9be95ac96ac810e3cfbaedf254dcce70c795"}
        for path,digest in small.items():
            small_before[str(path)] = identity(path);require(small_before[str(path)]["sha256"]==digest,"old small source/quote identity changed")
        raw = SplitRaw(paths,progress);progress.check(force=True)
        found = None
        with io.BufferedReader(raw,buffer_size=65536) as compressed, gzip.GzipFile(fileobj=compressed,mode="rb") as zipped:
            counted = TarReader(zipped,progress)
            with tarfile.open(fileobj=counted,mode="r|",bufsize=65536) as archive:
                for member in archive:
                    progress.check();progress.members += 1;progress.last_member = member.name
                    # TarFile otherwise retains all priorTarInfo objects, causing
                    # unbounded memory on a million-file archive. No link lookup
                    # or later member lookup is used in this target-only pass.
                    archive.members.clear()
                    if member.name!=TARGET:
                        continue
                    require(member.isfile() and 0<member.size<=PAYLOAD_CAP,"fixed target not regular/invalid payload size")
                    handle = archive.extractfile(member);require(handle is not None,"target payload unreadable")
                    payload = handle.read(member.size+1);require(len(payload)==member.size,"partial original target payload")
                    target_path = output/"original_20250210_600126.parquet"
                    with target_path.open("xb") as stream:stream.write(payload)
                    found = {"actual_header_name":member.name,"type":member.type.decode("ascii","replace"),"bytes":member.size,
                             "tar_header_offset":member.offset,"tar_data_offset":member.offset_data,"payload":identity(target_path)}
                    break
        require(found is not None,"fixed source member not found before budget/end; no automatic second pass")
        result["actual_target"] = found
        result["validation"] = validate(payload,output,hour_path)
        result["status"] = "PASS_SINGLE_ORIGINAL_TARGET_EXTRACTED_AND_ACTUAL_FIVE_RAW_ROWS_VALIDATED"
    except Exception as exc:
        error = exc;result["status"] = "FAIL_OR_REJECT_SINGLE_SOURCE_PASS_NO_AUTOMATIC_RESCAN"
        result["failure"] = {"type":type(exc).__name__,"reason":str(exc)}
        with (output/"actual_failure_traceback_v1.txt").open("x") as stream:stream.write(traceback.format_exc())
    finally:
        if raw is not None:
            raw.close();result["actual_compressed_bytes_by_part"] = raw.per_part
        result["actual_compressed_bytes_read"] = progress.compressed
        result["actual_decompressed_bytes_returned_to_tar"] = progress.decompressed_returned
        result["actual_tar_members_examined"] = progress.members
        result["last_header_observed"] = progress.last_member
        result["actual_elapsed_seconds"] = time.monotonic()-progress.started
        result["gzip_EOF_CRC_or_whole_archive_validation_repeated"] = False
        result["read_scope_note"] = "One source prefix only until fixed payload, no other Parquet decoded. Counts include bounded compressed/tar buffering readahead; uncompressed count is bytes returned to tar reader."
        try:
            source_after = {p:{**stat(Path(p)),"inherited_whole_SHA":v["inherited_whole_SHA"]} for p,v in source_before.items()}
            small_after = {p:identity(p) for p in small_before}
            require(source_after==source_before and small_after==small_before,"original sourceSTAT or smallSHA guard changed")
            result.update({"source_parts_inherited_SHA_and_actual_STAT_before":source_before,
                           "source_parts_inherited_SHA_and_actual_STAT_after":source_after,
                           "small_SHA_before":small_before,"small_SHA_after":small_after,"original_guards_unchanged":True})
        except Exception as exc:
            error = error or exc;result["status"] = "FAIL_OR_REJECT_ORIGINAL_SOURCE_GUARD";result["guard_failure"] = str(exc)
        progress.stream.close();result["actual_progress_log"] = identity(output/"actual_progress_v1.jsonl")
        result["finished_utc"] = datetime.now(timezone.utc).isoformat();result["source"] = identity(__file__)
        write_json(output/"single_original_target_extraction_receipt_v1.json",result)
    print(json.dumps({"status":result["status"],"receipt":identity(output/"single_original_target_extraction_receipt_v1.json"),
                      "actual_compressed_bytes":progress.compressed,"elapsed_seconds":result["actual_elapsed_seconds"],"full_PLAN_complete":False},ensure_ascii=False),flush=True)
    if error:
        raise RuntimeError("single source pass failed; preserve all evidence and do not automatically retry") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__);parser.add_argument("--execute",action="store_true");args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"status":"PREPARED_NOT_EXECUTED","target":TARGET,"budget_seconds":SECONDS_CAP}));return
    run()


if __name__=="__main__":
    main()
