"""Prepare existing research inputs and submit the approved first-week jobs once."""
from pathlib import Path
import os, json, subprocess, urllib.request, zipfile
from datetime import datetime, timezone
from .attribution import ROOT, OUT, sha

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    receipt = OUT/'launch.json'
    if receipt.exists():
        print('Existing launch retained:', receipt.read_text()); return
    source = OUT/'source_snapshot'; source.mkdir(exist_ok=True)
    with zipfile.ZipFile(ROOT/'plan_start_evidence_20261008.zip') as z:
        manifest = json.loads(z.read('manifest.json'))
        for name in manifest:
            if not (name.startswith('outputs/gru_market_attention_20261007/') or name=='official_csi500_price_2025_2026.csv'):
                continue
            target = source/name
            assert target.resolve().is_relative_to(source.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(name))
            assert sha(target)==manifest[name]
    dest=OUT/'benchmark_sources'; dest.mkdir(exist_ok=True)
    for code, start in [('000905','20180101'),('H00905','20241231')]:
        existing = dest/(code+'.json')
        evidence = dest/(code+'_receipt.json')
        if existing.exists() and evidence.exists():
            prior = json.loads(evidence.read_text())
            if prior.get('status') == 'retrieved' and sha(existing) == prior['sha256']:
                print('VERIFIED EXISTING OFFICIAL SNAPSHOT', code, flush=True)
                continue
        url=f'https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode={code}&startDate={start}&endDate=20260923'
        req=urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.csindex.com.cn/'})
        with urllib.request.urlopen(req, timeout=25) as response:
            raw=response.read(8_000_001); assert len(raw)<=8_000_000
            (dest/(code+'.json')).write_bytes(raw)
            (dest/(code+'_receipt.json')).write_text(json.dumps({'status':'retrieved','url':url,'sha256':sha(dest/(code+'.json')),'retrieved_at':datetime.now(timezone.utc).isoformat(),'bytes':len(raw)}))
    python='/opt/network/anaconda3/envs/pytorch/bin/python'
    subprocess.run([python,'-u','-m','gru_beta_neutral_20261008.prepare'],check=True,cwd=ROOT)
    subprocess.run([python,'-u','-m','gru_beta_neutral_20261008.residual_train','test'],check=True,cwd=ROOT)
    jobs=[]
    def submit(name, args):
        output=subprocess.check_output(['sbatch','--parsable', *args],text=True,cwd=ROOT).strip()
        job=int(output.split(';')[0]); jobs.append(dict(name=name,job_id=job))
        receipt.write_text(json.dumps({'status':'submitting','jobs':jobs,'at_utc':datetime.now(timezone.utc).isoformat()},indent=2))
        print('SUBMITTED',name,job,flush=True)
    submit('beta_constraints_56_replays',['gru_beta_neutral_20261008/replay_constraint.sbatch'])
    for seed in [11,22,33,44,55]:
        submit(f'residual_control_seed{seed}',['gru_beta_neutral_20261008/train_residual.sbatch',str(seed)])
    receipt.write_text(json.dumps({'status':'submitted','jobs':jobs,'at_utc':datetime.now(timezone.utc).isoformat(),
                                  'test_status':'passed','code_sha256':{p.name:sha(p) for p in (ROOT/'gru_beta_neutral_20261008').iterdir() if p.is_file()},
                                  'training_actual_completion':False,'constraint_actual_completion':False},indent=2))

if __name__=='__main__': main()
