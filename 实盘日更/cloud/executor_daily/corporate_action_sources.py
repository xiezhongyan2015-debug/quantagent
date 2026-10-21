"""Public, bounded HTTPS corporate-action evidence. No account credentials.

Eastmoney is a discovery index, not the authority for automatic booking.
Each held-security implementation must agree with an issuer announcement
reproduced by Sina. Rates in RPT_SHAREBONUS_DET are PER TEN shares.
"""
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import time
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse,parse_qs
import requests

HOSTS={'datacenter-web.eastmoney.com','vip.stock.finance.sina.com.cn','file.finance.sina.com.cn'}
class SourceError(Exception):pass

def decimal(value):
 try:d=Decimal(str(value))
 except Exception:raise SourceError('公告分红比例不是有效数字') from None
 if not d.is_finite() or d<0:raise SourceError('公告分红比例为负数或非有限值')
 return d

def date(value):
 s=re.sub(r'(\d{4})年(\d{1,2})月(\d{1,2})日',lambda m:f'{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}',str(value))
 m=re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})',s)
 if not m:raise SourceError('公告缺少明确日期')
 try:return dt.date(int(m[1]),int(m[2]),int(m[3])).isoformat()
 except ValueError:raise SourceError('公告日期无效') from None

class Page(HTMLParser):
 def __init__(self):
  super().__init__(convert_charrefs=True);self.parts=[];self.links=[];self.tables=[];self._a=None;self._row=None;self._cell=None;self._skip=0
 def handle_starttag(self,tag,attrs):
  if tag in ('script','style'):self._skip+=1
  if self._skip:return
  if tag=='a':self._a=[dict(attrs).get('href',''),'']
  if tag=='tr':self._row=[]
  if tag in ('td','th'):self._cell=[]
  if tag in ('p','br','tr','td','th'):self.parts.append(' ')
 def handle_endtag(self,tag):
  if tag in ('script','style'):self._skip=max(0,self._skip-1);return
  if self._skip:return
  if tag=='a' and self._a:self.links.append(tuple(self._a));self._a=None
  if tag in ('td','th') and self._cell is not None:
   if self._row is not None:self._row.append(''.join(self._cell).strip())
   self._cell=None
  if tag=='tr' and self._row is not None:self.tables.append(self._row);self._row=None
 def handle_data(self,data):
  if self._skip:return
  self.parts.append(data)
  if self._a:self._a[1]+=data
  if self._cell is not None:self._cell.append(data)
 @property
 def text(self):return re.sub(r'\s+','', ''.join(self.parts)).replace('，',',')

def get(session,url,params=None):
 if urlparse(url).scheme!='https' or urlparse(url).hostname not in HOSTS:raise SourceError('公告来源域名不在允许列表')
 try:
  with session.get(url,params=params,timeout=(5,15),allow_redirects=False,stream=True) as r:
   if r.status_code!=200:raise SourceError(f'公开公告服务HTTP {r.status_code}')
   chunks=[];size=0
   for chunk in r.iter_content(65536):
    size+=len(chunk)
    if size>2_000_000:raise SourceError('公告响应超过大小上限')
    chunks.append(chunk)
   content=b''.join(chunks)
   folder=getattr(session,'ca_evidence_dir',None)
   if folder is not None:
    folder.mkdir(parents=True,exist_ok=True);p=folder/(hashlib.sha256(content).hexdigest()+'.bin')
    if p.exists() and p.read_bytes()!=content:raise SourceError('公告证据文件与哈希不符')
    if not p.exists():p.write_bytes(content)
   return content
 except requests.RequestException:raise SourceError('公开公告服务连接失败，未使用未经核验的数据') from None

def announcement_terms(content,event):
 page=Page()
 if content.startswith(b'%PDF'):
  try:
   result=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('corporate_action_pdf.py'))],input=content,capture_output=True,timeout=15)
   data=json.loads(result.stdout.decode('utf8'))
   if result.returncode or 'error' in data:raise ValueError()
   page.parts=[data['text']];page.tables=data['tables']
  except (OSError,ValueError,KeyError,subprocess.TimeoutExpired):raise SourceError('完整公告PDF无法可靠解析；未推断分红日期或比例') from None
 else:page.feed(content.decode('gb18030'))
 text=page.text
 if event['code'][2:] not in text:raise SourceError('公告证券代码不一致')
 all_dates={date(m.group()) for m in re.finditer(r'\d{4}(?:年\d{1,2}月\d{1,2}日|[-/]\d{1,2}[-/]\d{1,2})',text)}
 if not {event['record_date'],event['ex_date']}<=all_dates:raise SourceError('公告与登记日/除权日不一致')
 # Demand the actual gross per-ten/per-share statement, not a guessed factor.
 cash10=re.findall(r'每10股(?:派(?:发)?(?:人民币)?(?:现金(?:股利|红利))?|分配现金红利)([\d.]+)元',text)
 cash1=re.findall(r'每股(?:派发)?(?:现金红利|现金股利)(?:人民币)?([\d.]+)元',text)
 gross={decimal(x)/10 for x in cash10}|{decimal(x) for x in cash1}
 cash=decimal(event['cash_per_share'])
 if cash and cash not in gross:raise SourceError('公告与结构化每股现金分红不一致')
 transfers={decimal(x)/10 for x in re.findall(r'每10股转增([\d.]+)股',text)}
 bonuses={decimal(x)/10 for x in re.findall(r'每10股送(?:红股)?([\d.]+)股',text)}
 for field,parsed in [('transfer_per_share',transfers),('bonus_per_share',bonuses)]:
  expected=decimal(event[field])
  if expected and expected not in parsed or not expected and any(parsed):raise SourceError('公告与结构化送转股比例不一致')
 pay=None
 for row in page.tables:
  if any('现金红利发放日' in x for x in row):
   head=row;index=page.tables.index(row)
   for data in page.tables[index+1:index+4]:
    if len(data)==len(head) and data and ('A' in data[0] or 'Ａ' in data[0]):
     mapping=dict(zip(head,data))
     try:
      if date(next(v for k,v in mapping.items() if '登记日' in k))!=event['record_date'] or date(next(v for k,v in mapping.items() if '除权' in k))!=event['ex_date']:raise SourceError('公告日期表与数据源不一致')
      pay=date(next(v for k,v in mapping.items() if '现金红利发放日' in k))
     except StopIteration:pass
 if cash and pay is None:
  m=re.search(r'现金红利将于(\d{4}(?:年\d{1,2}月\d{1,2}日|[-/]\d{1,2}[-/]\d{1,2}))',text)
  if m:pay=date(m[1])
 if cash and pay is None:raise SourceError('公告缺少明确现金派息日，禁止按除权日猜测')
 diluted=[]
 for m in re.finditer(r'(?:收盘价|收盘价格|每股现金分红)[-－]([\d.]+)(?:元|。)',text):
  x=decimal(m[1])
  if 0<x<cash:diluted.append(str(x))
 # Only a disclosed per-share diluted reference, never an arbitrary rounding gap.
 return dict(pay_date=pay or event['ex_date'],diluted_cash_per_share=sorted(set(diluted)),announcement_sha256=hashlib.sha256(content).hexdigest())

def verify_announcement(session,event):
 code=event['code'][2:];root='https://vip.stock.finance.sina.com.cn'
 listing=get(session,root+f'/corp/go.php/vCB_AllBulletin/stockid/{code}.phtml',dict(ftype=0))
 page=Page();page.feed(listing.decode('gb18030'))
 links=[]
 for href,title in page.links:
  if 'vCB_AllBulletinDetail.php' in href and '实施' in title and any(w in title for w in ('权益分派','分红派息','利润分配')) and '提示' not in title:
   u=urljoin(root,href)
   if u not in links:links.append(u)
 errors=[]
 for u in links[:8]:
  content=get(session,u);selected_url=u
  try:terms=announcement_terms(content,event)
  except SourceError as e:
   errors.append(str(e));detail=Page();detail.feed(content.decode('gb18030'))
   # Sina occasionally exposes only the second PDF page as HTML. Follow only
   # its explicit original attachment with the very same announcement ID.
   announcement_id=parse_qs(urlparse(u).query).get('id',[''])[0];terms=None
   attachments=[]
   for href,title in detail.links:
    candidate=urljoin(root,href);parts=urlparse(candidate)
    if parts.hostname=='file.finance.sina.com.cn' and parts.path.rsplit('/',1)[-1].lower()==announcement_id+'.pdf' and any(x in title for x in ('原文','下载')):
     candidate=parts._replace(scheme='https').geturl()
     if candidate not in attachments:attachments.append(candidate)
   for candidate in attachments[:1]:
    pdf=get(session,candidate)
    try:terms=announcement_terms(pdf,event)
    except SourceError as error:errors.append(str(error));continue
    selected_url=candidate;break
   if terms is None:continue
  return dict(event,**terms,announcement_url=selected_url,listing_sha256=hashlib.sha256(listing).hexdigest(),evidence_verified=True)
 raise SourceError(f'{code} 未找到日期和比例一致的实施公告'+('：'+errors[-1] if errors else ''))

def fetch_events(day,codes,now=None,record_from=None,evidence_dir=None):
 now=now or dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
 day=dt.date.fromisoformat(day);start=(day-dt.timedelta(days=30)).isoformat();end=(day+dt.timedelta(days=7)).isoformat()
 if len(codes)>1800 or any(not re.fullmatch(r'(SH|SZ)\d{6}',x) for x in codes):raise SourceError('持仓证券列表无效')
 rows=[];hashes=[];count=None;pages=None
 with requests.Session() as session:
  session.ca_evidence_dir=Path(evidence_dir) if evidence_dir is not None else None
  session.trust_env=False;session.headers.update({'User-Agent':'Mozilla/5.0','Referer':'https://data.eastmoney.com/'})
  for number in range(1,13):
   params=dict(reportName='RPT_SHAREBONUS_DET',columns='ALL',filter=f"(EX_DIVIDEND_DATE>='{start}')(EX_DIVIDEND_DATE<='{end}')",pageSize=500,pageNumber=number,sortColumns='EX_DIVIDEND_DATE,SECURITY_CODE',sortTypes='1,1',source='WEB',client='WEB')
   raw=get(session,'https://datacenter-web.eastmoney.com/api/data/v1/get',params);hashes.append(hashlib.sha256(raw).hexdigest())
   try:d=json.loads(raw);result=d['result'];part=result['data'];p=int(result['pages']);n=int(result['count'])
   except (ValueError,TypeError,KeyError):raise SourceError('分红数据源格式异常或无法确认完整性') from None
   if d.get('success') is not True or d.get('code')!=0 or not isinstance(part,list) or not 1<=p<=12 or not 0<=n<=6000:raise SourceError('分红数据源业务错误或超过核验上限')
   if count is not None and (count!=n or pages!=p):raise SourceError('分红数据源分页期间发生变化')
   count,pages=n,p;rows.extend(part)
   if number==pages:break
   time.sleep(1)
  if len(rows)!=count:raise SourceError('分红数据源分页不完整')
  events=[];seen=set()
  for row in rows:
   code=row.get('SECUCODE','');norm=('SH' if code.endswith('.SH') else 'SZ' if code.endswith('.SZ') else '')+str(row.get('SECURITY_CODE',''))
   if norm not in codes:continue
   if row.get('ASSIGN_PROGRESS')!='实施分配':continue
   event=dict(code=norm,name=row.get('SECURITY_NAME_ABBR',''),record_date=date(row['EQUITY_RECORD_DATE']),ex_date=date(row['EX_DIVIDEND_DATE']),cash_per_share=str(decimal(row.get('PRETAX_BONUS_RMB') or 0)/10),transfer_per_share=str(decimal(row.get('IT_RATIO') or 0)/10),bonus_per_share=str(decimal(row.get('BONUS_RATIO') or 0)/10),notice_date=date(row['NOTICE_DATE']),discovery_sha256=hashlib.sha256(json.dumps(row,sort_keys=True,ensure_ascii=False).encode()).hexdigest())
   if event['notice_date']>now.date().isoformat() or event['record_date']>=event['ex_date']:raise SourceError('公告披露日期或登记/除权顺序异常')
   if record_from and event['record_date']<record_from:continue
   key=(norm,event['record_date'],event['ex_date'])
   if key in seen:raise SourceError('存在重复或修订的实施方案，需人工审核')
   seen.add(key)
   # Older events are included only to recognize deferred cash, never replayed.
   time.sleep(1);events.append(verify_announcement(session,event))
 return dict(schema=1,as_of=now.isoformat(),day=day.isoformat(),scope=sorted(codes),events=events,discovery_response_sha256=hashes,discovery_rows=count,complete=True)
