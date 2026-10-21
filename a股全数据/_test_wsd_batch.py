from wind_excel_client import WindExcelClient
import sys, time, datetime as dt
n=int(sys.argv[1]); mode=sys.argv[2]
c=WindExcelClient()
try:
    codes=[f'{i:06d}.SZ' for i in range(1,n+1)]
    t=time.time()
    if mode=='codes':
        d=c.query_wsd_codes(codes,'close',dt.date(2010,1,1),dt.date(2010,1,31),options='PriceAdj=B',timeout=120)
        print('mode',mode,'n',n,'shape',d.shape,'elapsed',round(time.time()-t,2),flush=True)
        print(d.head(2).to_string(),flush=True)
    else:
        d=c.query_wsd_fields('000001.SZ',['open','high','low','close','volume','amt'],dt.date(2010,1,1),dt.date(2010,1,31),options='PriceAdj=B',timeout=120)
        print('mode',mode,'shape',d.shape,'elapsed',round(time.time()-t,2),flush=True)
        print(d.head(3).to_string(),flush=True)
finally:
    c.close()
