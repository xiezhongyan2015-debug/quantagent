"""Bounded small-file transfer over existing authenticated SSH. Maximum 64 KiB."""
import hashlib,json,re,subprocess,sys,time
from pathlib import Path
from ssh_small_files import ARGS

def upload_bounded(source,destination):
 if not re.fullmatch(r'C:\\ChoiceBridge\\(?:[A-Za-z0-9_.-]+\\)*[A-Za-z0-9_.-]+',destination):raise ValueError('Destination outside preparation directory')
 data=Path(source).read_bytes()
 if not 0<len(data)<=65536:raise ValueError('Small files only')
 sha=hashlib.sha256(data).hexdigest();path=destination.replace('\\','/')
 code="import sys,hashlib;from pathlib import Path;d=sys.stdin.buffer.read(65537);assert len(d)<=65536;assert hashlib.sha256(d).hexdigest()=='"+sha+"';Path('"+path+"').write_bytes(d);print(hashlib.sha256(d).hexdigest())"
 cmd='"C:\\ChoiceBridge\\runtime\\choice-venv\\Scripts\\python.exe" -c "'+code+'"'
 t=time.monotonic();r=subprocess.run(ARGS+[cmd],input=data,capture_output=True,timeout=30)
 if r.returncode or r.stdout.decode().strip()!=sha:raise RuntimeError('Bounded transfer verification failed')
 return dict(file=Path(source).name,bytes=len(data),sha256=sha,hash_match=True,seconds=round(time.monotonic()-t,2),orders_submitted=0)
if __name__=='__main__':print(json.dumps(upload_bounded(sys.argv[1],sys.argv[2])))
