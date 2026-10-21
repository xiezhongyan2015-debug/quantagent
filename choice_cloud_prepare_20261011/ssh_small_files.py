"""Transfer reviewed small preparation files over the existing authenticated SSH channel."""
import argparse,base64,hashlib,json,re,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent
ARGS=['ssh','-S','/tmp/choice-ssh-leslie-20261011/control','-F',str(ROOT/'ssh_config'),'-o','BatchMode=yes','choice-executor']
def run_ps(script,timeout=30):
    script="[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);$ProgressPreference='SilentlyContinue';$ErrorActionPreference='Stop';"+script
    encoded=base64.b64encode(script.encode('utf-16-le')).decode()
    command='"C:\\ChoiceBridge\\runtime\\choice-venv\\Scripts\\python.exe" -X utf8 "C:\\ChoiceBridge\\staging\\bounded_ps_runner.py" '+str(min(60,max(1,int(timeout))))+' '+encoded
    if len(command)>8000: raise ValueError('Windows command too long')
    result=subprocess.run(ARGS+[command],capture_output=True,timeout=timeout)
    if result.returncode: raise RuntimeError('Remote preparation failed with exit '+str(result.returncode))
    return result.stdout.decode('utf8').lstrip('\ufeff').strip()
def upload(source,destination):
    if not re.fullmatch(r'C:\\ChoiceBridge\\(?:[A-Za-z0-9_.-]+\\)*[A-Za-z0-9_.-]+',destination):
        raise ValueError('Destination outside preparation directory')
    data=Path(source).read_bytes()
    if len(data)>65536:raise ValueError('Small files only')
    for start in range(0,len(data),1800):
        mode='Create' if start==0 else 'Append'
        encoded=base64.b64encode(data[start:start+1800]).decode()
        run_ps(f"$b=[Convert]::FromBase64String('{encoded}');$f=[IO.File]::Open('{destination}',[IO.FileMode]::{mode});try{{$f.Write($b,0,$b.Length)}}finally{{$f.Dispose()}}")
    sha=hashlib.sha256(data).hexdigest()
    result=run_ps(f"$h=(Get-FileHash -LiteralPath '{destination}' -Algorithm SHA256).Hash.ToLowerInvariant();if($h -ne '{sha}'){{throw 'Transfer hash mismatch'}};Write-Output $h")
    if result!=sha:raise ValueError('Transfer receipt unexpected')
    return {'file':Path(source).name,'bytes':len(data),'sha256':sha,'destination':destination,'hash_match':True,'orders_submitted':0}
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('source');ap.add_argument('destination');args=ap.parse_args()
    print(json.dumps(upload(args.source,args.destination),indent=2))
