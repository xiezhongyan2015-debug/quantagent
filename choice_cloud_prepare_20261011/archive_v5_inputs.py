"""Archive reviewed signal inputs; no trading imports, process or authorization changes."""
import hashlib,json,sys,zipfile
from pathlib import Path
p=Path('C:/ChoiceBridge/staging/c5_input_package_20261012.zip');assert hashlib.sha256(p.read_bytes()).hexdigest()==sys.argv[1]
names={'c5_state_20261009.parquet','target_original.json','precheck_original.json','sellout_original.csv','provenance.json'};out=Path('C:/ChoiceBridge/signals/v5_20261012');out.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(p) as z:
 assert set(z.namelist())==names and len(z.namelist())==len(names) and sum(i.file_size for i in z.infolist())<=200000
 proof=json.loads(z.read('provenance.json'));assert set(proof['input_hashes'])==names-{'provenance.json'}
 for n,h in proof['input_hashes'].items():assert hashlib.sha256(z.read(n)).hexdigest()==h
 for n in names:
  target=out/n;data=z.read(n)
  if target.exists():assert target.read_bytes()==data
  else:target.write_bytes(data)
print(json.dumps(dict(archived_files=len(names),file_hashes=proof['input_hashes'],directory=str(out),orders_submitted=0,trading_activated=False)))
