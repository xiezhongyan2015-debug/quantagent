"""Bounded, offline PDF text extraction using a pinned bundled source archive."""
import hashlib,io,json,re,sys,unicodedata
from pathlib import Path
ZIP=Path(__file__).with_suffix('.zip')
ZIP_SHA256='b1a72c3317c013465fe5b2a7c8ea0491cdfa02bb3cc323e3d7d9e6612192b81f'

def extract(content):
 if len(content)>2_000_000 or not content.startswith(b'%PDF'):raise ValueError('公告附件不是限额内PDF')
 if hashlib.sha256(ZIP.read_bytes()).hexdigest()!=ZIP_SHA256:raise ValueError('PDF解析组件校验失败')
 sys.path.insert(0,str(ZIP))
 from pypdf import PdfReader
 import pypdf.filters
 pypdf.filters.ZLIB_MAX_OUTPUT_LENGTH=8_000_000
 reader=PdfReader(io.BytesIO(content),strict=True)
 if reader.is_encrypted or not 1<=len(reader.pages)<=12:raise ValueError('公告PDF加密或页数超限')
 texts=[]
 for page in reader.pages:
  texts.append(page.extract_text() or '')
  if sum(map(len,texts))>300_000:raise ValueError('公告提取文本超限')
 text=unicodedata.normalize('NFKC','\n'.join(texts));compact=re.sub(r'\s+','',text)
 date=r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})'
 pattern=r'股权登记日(?:最后交易日)?除权\(息\)日现金红利发放日A股'+date+r'(?:[-—])?'+date+date
 tables=[]
 for m in re.finditer(pattern,compact):
  tables.extend([['股份类别','股权登记日','除权(息)日','现金红利发放日'],['A股',*m.groups()]])
 return dict(text=text,tables=tables)

if __name__=='__main__':
 try:
  result=extract(sys.stdin.buffer.read(2_000_001));sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode('utf8'))
 except Exception:
  sys.stdout.buffer.write(json.dumps(dict(error='完整公告PDF无法可靠解析；未推断分红日期或比例'),ensure_ascii=False).encode('utf8'));raise SystemExit(2)
