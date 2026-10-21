from wind_excel_client import WindExcelClient
import sys, time
n=int(sys.argv[1])
c=WindExcelClient()
try:
    codes=[f'{i:06d}.SZ' for i in range(1,n+1)]
    fields=['s_info_name','s_ipo_listeddate','s_info_delistdate','s_info_stockclass','s_info_exchmarket']
    formula=f'=WSS("{",".join(codes)}","{",".join(fields)}")'
    print('n',n,'formula_len',len(formula),flush=True)
    t=time.time(); d=c.query_wss(codes,fields,timeout=120)
    print('elapsed',round(time.time()-t,2),'shape',d.shape,flush=True)
    print(d.head(3).to_string(),flush=True)
finally:
    c.close()
