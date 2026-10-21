"""Read the downloaded CSI BIFF8 file without installing research dependencies.
Strictly for this captured public document, not a general spreadsheet engine.
"""
import struct as S,json,hashlib,datetime,re
from pathlib import Path
p=Path(__file__).resolve().parent;blob=(p/'000905cons_official.xls').read_bytes()
assert blob[:8]==bytes.fromhex('d0cf11e0a1b11ae1')
sector=1<<S.unpack_from('<H',blob,30)[0]
def block(i):return blob[(i+1)*sector:(i+2)*sector]
fatids=list(S.unpack_from('<109I',blob,76));assert S.unpack_from('<I',blob,72)[0]==0,'extended FAT unsupported'
fat=[]
for i in fatids:
 if i<0xfffffffa:fat.extend(S.unpack('<'+str(sector//4)+'I',block(i)))
def chain(i):
 out=bytearray();seen=set()
 while i<0xfffffffa:
  assert i not in seen and i<len(fat);seen.add(i);out.extend(block(i));i=fat[i]
 return bytes(out)
dir=chain(S.unpack_from('<I',blob,48)[0]);work=None
for i in range(0,len(dir),128):
 e=dir[i:i+128];n=S.unpack_from('<H',e,64)[0];name=e[:max(0,n-2)].decode('utf-16le')
 if name in ('Workbook','Book'):
  size=S.unpack_from('<Q',e,120)[0];assert size>=4096
  work=chain(S.unpack_from('<I',e,116)[0])[:size]
assert work
records=[];i=0
while i+4<=len(work):
 code,size=S.unpack_from('<HH',work,i);data=work[i+4:i+4+size];assert len(data)==size;records.append((code,data));i+=4+size
# SST and continuation contain strings; continuation's encoding flag applies only in character content.
ix=next(i for i,x in enumerate(records) if x[0]==0xfc);segments=[records[ix][1]]
for c,b in records[ix+1:]:
 if c!=0x3c:break
 segments.append(b)
class Reader:
 def __init__(self):self.i=0;self.off=0
 def read(self,n):
  out=b''
  while n:
   if self.off==len(segments[self.i]):self.i+=1;self.off=0
   k=min(n,len(segments[self.i])-self.off);out+=segments[self.i][self.off:self.off+k];self.off+=k;n-=k
  return out
 def chars(self,n,wide):
  value=''
  while n:
   if self.off==len(segments[self.i]):self.i+=1;self.off=0;wide=bool(self.read(1)[0]&1)
   k=min(n,(len(segments[self.i])-self.off)//(2 if wide else 1));assert k>0
   value+=self.read(k*(2 if wide else 1)).decode('utf-16le' if wide else 'latin1');n-=k
  return value
r=Reader();total,unique=S.unpack('<II',r.read(8));strings=[]
for _ in range(unique):
 count,flags=S.unpack('<HB',r.read(3));runs=S.unpack('<H',r.read(2))[0] if flags&8 else 0;ext=S.unpack('<I',r.read(4))[0] if flags&4 else 0
 strings.append(r.chars(count,bool(flags&1)));r.read(runs*4+ext)
cells={}
for code,b in records:
 if code==0xfd:
  row,col,xf,index=S.unpack('<HHHI',b);assert (row,col) not in cells;cells[row,col]=strings[index]
 elif code==0x203:
  row,col,xf,val=S.unpack('<HHHd',b);cells[row,col]=str(int(val)) if val.is_integer() else str(val)
headers={re.sub(r'\s+','',v):c for (row,c),v in cells.items() if row==0};required=['日期Date','指数代码IndexCode','成份券代码ConstituentCode','成份券名称ConstituentName','交易所Exchange'];assert all(k in headers for k in required),headers
rows=[]
for n in sorted({row for row,col in cells if row>0}):
 row={k:cells[n,headers[k]] for k in required};assert row['指数代码IndexCode'].zfill(6)=='000905'
 exchange={'上海证券交易所':'SH','深圳证券交易所':'SZ'}[row['交易所Exchange']];code=row['成份券代码ConstituentCode'].zfill(6);assert re.fullmatch(r'[036]\d{5}',code)
 rows.append({'code':exchange+code,'name':row['成份券名称ConstituentName'],'date':row['日期Date']})
assert len(rows)==500 and len({x['code'] for x in rows})==500 and len({x['date'] for x in rows})==1
value=rows[0]['date'];day=datetime.datetime.strptime(value,'%Y%m%d' if len(value)==8 else '%Y-%m-%d').date().isoformat();assert day<=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date().isoformat()
record={'index':'csi500','index_code':'000905','snapshot_date':day,'fetched_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source_url':'https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/000905cons.xls','source_file_sha256':hashlib.sha256(blob).hexdigest(),'members':[{k:v for k,v in x.items() if k!='date'} for x in rows]}
q=p/'full_executor/research/choice_pcf_automation/daily_public_pcf/csi500_members.json';q.write_text(json.dumps(record,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in record.items() if k!='members'},ensure_ascii=False));print('count',len(rows))
