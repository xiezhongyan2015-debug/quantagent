"""Read-only competition account snapshot; four GET endpoints, no submission code.
Uses the current Administrator Choice session for an operator-selected account.
Credentials remain in memory and never enter outputs or files.
"""
import argparse,base64,ctypes,datetime as dt,hashlib,json,sqlite3,sys,time,os,uuid
from ctypes import wintypes
from decimal import Decimal
from pathlib import Path
import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
# Use the executor's verified session refresh and identity binding.
EXECUTOR = Path('C:/ChoiceBridge/executor/research/choice_pcf_automation')
sys.path.insert(0, str(EXECUTOR))
from choice_auth import ChoiceAuthenticator, install_tokens
from cloud_account import ACCOUNT, verify_deployment
LIVE = Path("C:/ChoiceBridge/live")
BASE = "https://choicelabapi.eastmoney.com//emlab-api/"
def session_tokens(names=('JToken',)):
    if not names or set(names) - {'JToken','utoken','ctoken','UID'}:
        raise ValueError('Session field outside explicit authorization')
    profile = Path('C:/Eastmoney/Choice/EMWeb/Cache')
    prefs = json.loads((profile / 'LocalPrefs.json').read_text(encoding='utf-8'))
    wrapped = base64.b64decode(prefs['os_crypt']['encrypted_key'])
    if not wrapped.startswith(b'DPAPI'):
        raise ValueError('Unsupported session key format')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(wrapped[5:])).from_buffer_copy(wrapped[5:])
    source = Blob(len(buffer), buffer)
    target = Blob()
    unprotect = ctypes.windll.crypt32.CryptUnprotectData
    unprotect.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    unprotect.restype = wintypes.BOOL
    if not unprotect(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)):
        raise ValueError('Current-user session decryption unavailable')
    try:
        key = ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(target.data)
    result = {}
    with sqlite3.connect((profile / 'Network/Cookies').as_uri()+'?mode=ro', uri=True, timeout=1) as db:
        for name in names:
            rows = db.execute("SELECT value, encrypted_value FROM cookies WHERE host_key=? AND name=?",
                              ('choicelab.eastmoney.com', name)).fetchall()
            if len(rows) != 1:
                raise ValueError('Expected one current Choice session field')
            value, encrypted = rows[0]
            if not value:
                if encrypted[:3] != b'v10':
                    raise ValueError('Unsupported cookie format')
                plain = AESGCM(key).decrypt(encrypted[3:15], encrypted[15:], None)
                host_digest = hashlib.sha256(b'choicelab.eastmoney.com').digest()
                if plain.startswith(host_digest):
                    plain = plain[len(host_digest):]
                value = plain.decode('utf-8')
            if not value:
                raise ValueError('Empty authorized session field')
            result[name] = value
    return result

class GetOnlySession(requests.Session):
    KEYS = {
        'AguTrade/SpoBalInfo': {'accId'},
        'AguTrade/SpoHold': {'accId', 'ssid', 'dvid', 'appver', 'fToken', 'uToken'},
        'AguTrade/SpoOrders': {'accId', 'dataRange', 'pageNo', 'pageSize'},
        'AguTrade/SpoDeal': {'accId', 'dataRange', 'pageNo', 'pageSize'},
    }
    def __init__(self, account):
        super().__init__()
        self.account = account
        self.audit = []

    def request(self, method, url, **kwargs):
        if set(kwargs) - {'params','timeout','allow_redirects'}:
            raise RuntimeError('Unexpected request options blocked before network')
        endpoint = url[len(BASE):] if url.startswith(BASE) else ''
        params = kwargs.get('params', {})
        if (method.upper() != 'GET' or endpoint not in self.KEYS or
                set(params) != self.KEYS[endpoint] or params['accId'] != self.account or
                kwargs.get('allow_redirects') is not False or kwargs.get('json') is not None or
                kwargs.get('data') is not None):
            raise RuntimeError('Request blocked before network')
        start = time.perf_counter()
        response = super().request(method, url, **kwargs)
        self.audit.append({'endpoint':endpoint,'method':'GET','http_status':response.status_code,
                           'elapsed_ms':round((time.perf_counter()-start)*1000,3)})
        return response

def checked_data(session, endpoint, params):
    response=session.get(BASE+endpoint,params=params,timeout=15,allow_redirects=False)
    if response.status_code != 200: raise ValueError('Read query HTTP failure')
    obj=response.json()
    if not isinstance(obj,dict) or str(obj.get('Code'))!='0' or 'Data' not in obj:
        raise ValueError('Read query business failure')
    return obj['Data']

def checked_rows(session, endpoint, account):
    params={'accId':account}
    if endpoint.endswith('SpoHold'):
        params.update(ssid=1,dvid=2,appver=3,fToken='ok',uToken='test.hfstrade')
    else: params.update(dataRange=1,pageNo='0',pageSize='1000')
    data=checked_data(session,endpoint,params)
    if not isinstance(data,dict) or not isinstance(data.get('data'),list):
        raise ValueError('Unexpected account list schema')
    rows=data['data']
    if len(rows)>=1000 or not all(isinstance(r,dict) for r in rows):
        raise ValueError('Account list incomplete')
    for name in ('total','totalCount','count'):
        if name in data and Decimal(str(data[name]))>len(rows):
            raise ValueError('Account list truncated')
    return rows

def safe_decimal(value):
    d=Decimal(str(value))
    if not d.is_finite() or d<0: raise ValueError('Invalid balance or quantity')
    return format(d,'f')

def today():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()

def validate_daily_output(out):
    if out.parent.resolve()!=LIVE.resolve() or out.name!=f'captain_snapshot_{today()}.json':
        raise ValueError('Refresh is restricted to the current bound live daily snapshot')

def write_output(out,output,refresh=False):
    if not refresh:
        with out.open('x',encoding='utf8') as file:json.dump(output,file,ensure_ascii=False,indent=2)
        return
    validate_daily_output(out)
    if output['account_sha256']!=hashlib.sha256(ACCOUNT.encode()).hexdigest():
        raise ValueError('New snapshot identity differs')
    if out.exists():
        previous=out.read_bytes();old=json.loads(previous)
        day=dt.datetime.fromisoformat(old['checked_at']).astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
        if old['account_sha256']!=output['account_sha256'] or day!=today():
            raise ValueError('Existing snapshot is not the same account/day')
        folder=LIVE/'account_snapshot_archive'/today();folder.mkdir(parents=True,exist_ok=True)
        path=folder/(hashlib.sha256(previous).hexdigest()+'.json')
        if not path.exists():
            with path.open('xb') as file:file.write(previous)
    temporary=out.with_name(out.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf8') as file:json.dump(output,file,ensure_ascii=False,indent=2)
        os.replace(temporary,out)
    finally:temporary.unlink(missing_ok=True)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--account',required=True);ap.add_argument('--out',required=True)
    ap.add_argument("--refresh",action="store_true",help="Refresh only today's bound live daily snapshot, preserving previous evidence")
    args=ap.parse_args()
    if args.refresh: validate_daily_output(Path(args.out))
    if not args.account.isdecimal() or len(args.account)>64: raise ValueError('Account identifier invalid')
    if args.account != ACCOUNT: raise ValueError('Account differs from verified identity')
    verify_deployment()
    auth=ChoiceAuthenticator()
    session=GetOnlySession(args.account)
    session.trust_env=False
    try:
        tokens=auth.login()
        install_tokens(tokens,session,args.account)
        tokens.clear()
        balance=checked_data(session,'AguTrade/SpoBalInfo',{'accId':args.account})
        if not isinstance(balance,dict): raise ValueError('Unexpected balance schema')
        holds=checked_rows(session,'AguTrade/SpoHold',args.account)
        orders=checked_rows(session,'AguTrade/SpoOrders',args.account)
        deals=checked_rows(session,'AguTrade/SpoDeal',args.account)
        positions=[];codes=set()
        for row in holds:
            code=str(row['secCode'])
            if len(code)!=6 or not code.isdecimal() or code in codes:
                raise ValueError('Unexpected or duplicate security code')
            codes.add(code)
            shares=Decimal(safe_decimal(row['count']))
            if shares!=shares.to_integral_value(): raise ValueError('Noninteger position')
            available=Decimal(safe_decimal(row['availCount']))
            if available!=available.to_integral_value() or available>shares:
                raise ValueError('Invalid available shares')
            value=Decimal(safe_decimal(row['value']))
            positions.append({'code':code,'shares':int(shares),'available_shares':int(available),
                              'market_value':format(value,'f'),
                              'implied_mark_price':format(value/shares,'f') if shares else None})
        selected=[]
        for row in orders:
            selected.append({k:row.get(k) for k in ('secCode','orderCount','orderPrice','tradeCount','status','orderDate','orderTime','drt')})
            selected[-1]['order_id_tail']=str(row.get('orderId',''))[-4:]
        output={'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'account_tail':args.account[-4:],'account_sha256':hashlib.sha256(args.account.encode()).hexdigest(),
            'balance':{k:safe_decimal(balance[k]) for k in ('totalAssets','availBalance','frozenMoney','totalPosValue')},
            'positions':positions,'orders':selected,'deals_count':len(deals),
            'unfinished_order_count':sum(str(r.get('status')) not in ('4','7','8','9') for r in orders),
            'holding_fields':sorted(holds[0]) if holds else [],'deal_fields':sorted(deals[0]) if deals else [],
            'network_audit':session.audit,'new_orders_submitted':0,'cancellations_submitted':0,
            'automatic_trading_started':False,'sellable_field_observed_and_range_checked':True,
            'sellable_contract_verified_by_order':False,'mark_prices_are_executable_quotes':False}
        out=Path(args.out)
        write_output(out,output,args.refresh)
        print(json.dumps(output,ensure_ascii=False,indent=2))
    finally: session.headers.clear();session.close();auth.close()

if __name__=='__main__':
    try: main()
    except Exception as error:
        print('Read-only verification failed:',type(error).__name__,file=sys.stderr)
        raise SystemExit(1)
