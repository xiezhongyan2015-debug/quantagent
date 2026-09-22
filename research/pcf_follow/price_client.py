"""Rate-limited public daily-price reader. No credentials or broker API."""
import datetime as dt
import threading
import time
import requests

def ms(date,end=False):
    d=dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
    if end:d+=dt.timedelta(days=1,milliseconds=-1)
    return int(d.timestamp()*1000)

class Client:
    def __init__(self,key,rps,base_url):
        if key is not None:raise ValueError('This research client only supports public access')
        self.spacing=1/rps;self.base=base_url;self.lock=threading.Lock();self.last=0
    def get(self,params):
        for attempt in range(4):
            with self.lock:
                time.sleep(max(0,self.spacing-(time.monotonic()-self.last)));self.last=time.monotonic()
            r=requests.get(self.base+'/v1/klines',params=params,timeout=45)
            if r.status_code==429:
                time.sleep(min(30,4*2**attempt));continue
            r.raise_for_status();obj=r.json()
            if obj.get('code')=='RATE_LIMITED':
                time.sleep(min(30,4*2**attempt));continue
            if 'data' not in obj:raise ValueError('Price provider returned no data')
            return obj
        raise RuntimeError('Public price rate limit; resume later')
