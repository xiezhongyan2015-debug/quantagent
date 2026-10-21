"""Fixed eight-stock state/action source probe; anonymous, one page, no retry."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import socket
import sys
import time

REPO=Path(__file__).resolve().parents[1]
OUT=REPO/'experiment_traces/meta_framework_v3/l2_state_actions_001'
SOURCE=REPO/'experiment_traces/meta_corporate_action_source_probe_v14'
SDK=SOURCE/'sdk_local'
FIELDS='date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,isST'


def now():return datetime.now(timezone.utc).isoformat()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def save(name,v):
    with (OUT/name).open('x',encoding='utf-8') as f:
        json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')


def freeze():
    assert not OUT.exists()
    parent=read(REPO/'experiment_traces/meta_framework_v3/l2_execution_evidence_001/plan.json')
    codes=[x[-2:].lower()+'.'+x[:6] for x in parent['codes']]
    queries=[{'method':'login','params':{}}]
    queries += [{'method':'query_history_k_data_plus','params':{'code':c,'fields':FIELDS,'start_date':'2026-01-06','end_date':'2026-01-08','frequency':'d','adjustflag':'3'}} for c in codes]
    queries += [{'method':'query_dividend_data','params':{'code':c,'year':'2026','yearType':'operate'}} for c in codes]
    queries += [{'method':'logout','params':{}}]
    pins={str(p.relative_to(SDK)):sha(p) for p in SDK.rglob('*.py')}
    OUT.mkdir()
    plan={'created_at':now(),'deadline_epoch':time.time()+1200,'codes':codes,'dates':parent['dates'],
        'parent_quote_plan_sha256':parent['plan_sha256'],'queries':queries,'max_sends':18,'max_connections':1,
        'socket_timeout_seconds':10,'request_wall_seconds':30,'max_response_bytes_per_query':2*1024**2,
        'max_total_response_bytes':16*1024**2,'automatic_retries':0,'pagination_calls':0,
        'network_endpoint':['public-api.baostock.com',10030],'sdk_version':'0.9.3','sdk_pins':pins,
        'official_document_sha256':sha(SOURCE/'official_03.decoded.txt'),
        'script_sha256':sha(Path(__file__)),'account_type':'SDK public anonymous default; no private API key',
        'new_model_calls':0,'sealed_2024_2025_opened':False,'strategy_searches':0,'portfolio_pnl_calculation':False,
        'interpretation':['History rows establish provider day-level reported state, not intraday permission or receipt time',
            '2026 operate is ex-date year, not report fiscal year','Empty dividend results remain unknown; no universal no-event claim',
            'Queries expose full 2026 action dates to controller source audit only; do not send future announcements to a prior-time strategy'],
        'stops':['Any SDK source changes','Any nonzero API result or exception stops data requests','Budget or deadline','No response retries or automatic pagination']}
    plan['plan_sha256']=hashlib.sha256(json.dumps(plan,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    save('plan.json',plan)
    return {'plan_sha256':plan['plan_sha256'],'maximum_queries':len(queries)}


def probe():
    plan=read(OUT/'plan.json')
    assert time.time()<plan['deadline_epoch'] and sha(Path(__file__))==plan['script_sha256']
    assert not (OUT/'started.json').exists(), 'Inspect saved intent; never automatically restart'
    assert {str(p.relative_to(SDK)):sha(p) for p in SDK.rglob('*.py')}==plan['sdk_pins']
    sys.path.insert(0,str(SDK))
    import baostock as bs
    from baostock.common import context, contants
    assert not hasattr(context,'apiKey')
    assert [contants.BAOSTOCK_SERVER_IP,contants.BAOSTOCK_SERVER_PORT]==plan['network_endpoint']
    save('started.json',{'started_at':now(),'plan_sha256':plan['plan_sha256']})
    state={'connections':0,'sends':0,'received_total':0,'received_query':0,'wire_ordinal':0,'phase':None,'query_index':None,'request_end':None,'request_sent':False}
    records=[]

    def wire(kind,body=None,**extra):
        state['wire_ordinal']+=1
        entry={'ordinal':state['wire_ordinal'],'query_index':state['query_index'],'method':state['phase'],'kind':kind,'observed_at':now(),**extra}
        if body is not None:
            name=f'wire_{state["wire_ordinal"]:03d}_{kind}.bin'
            with (OUT/name).open('xb') as f:f.write(body)
            entry.update(file=name,bytes=len(body),sha256=hashlib.sha256(body).hexdigest())
        with (OUT/'wire_events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(entry,ensure_ascii=False)+'\n')

    Original=socket.socket
    class Captured(Original):
        def connect(self,address):
            state['connections']+=1
            assert state['connections']==1 and list(address)==plan['network_endpoint']
            self.settimeout(plan['socket_timeout_seconds']); wire('connect',address=list(address))
            return super().connect(address)
        def send(self,data,*args,**kwargs):
            assert not state['request_sent'] and time.time()<min(plan['deadline_epoch'],state['request_end'])
            expected=plan['queries'][state['query_index']]
            assert state['phase']==expected['method'] and state['phase'].encode() in data
            assert all(str(v).encode() in data for v in expected['params'].values())
            state['request_sent']=True;state['sends']+=1;assert state['sends']<=plan['max_sends']
            wire('request',bytes(data));sent=super().send(data,*args,**kwargs)
            assert sent==len(data), 'Partial send retained, no retry'
            return sent
        def recv(self,size,*args,**kwargs):
            remaining=min(plan['deadline_epoch'],state['request_end'])-time.time()
            assert remaining>0
            self.settimeout(min(plan['socket_timeout_seconds'],remaining))
            body=super().recv(min(size,8192),*args,**kwargs);wire('response',body)
            state['received_query']+=len(body);state['received_total']+=len(body)
            assert body and state['received_query']<=plan['max_response_bytes_per_query']
            assert state['received_total']<=plan['max_total_response_bytes']
            return body
    socket.socket=Captured
    try:
        for index,item in enumerate(plan['queries']):
            assert time.time()<plan['deadline_epoch']
            state.update(phase=item['method'],query_index=index,received_query=0,request_end=time.time()+plan['request_wall_seconds'],request_sent=False)
            receipt={'ordinal':index,'method':item['method'],'params':item['params'],'started_at':now()}
            save(f'{index:02d}_request.json',receipt)
            try:
                result=getattr(bs,item['method'])(**item['params'])
                receipt.update(finished_at=now(),error_code=result.error_code,error_msg=result.error_msg,
                    fields=result.fields,data=result.data,returned_code=result.code,returned_year=result.year,
                    returned_yearType=result.yearType,returned_start_date=result.start_date,returned_end_date=result.end_date,
                    returned_adjustflag=result.adjustflag,cur_page_num=result.cur_page_num,per_page_count=result.per_page_count)
                assert len(result.data)<=200, 'Unexpected row count; no next page or data admission'
                if item['method']=='query_history_k_data_plus' and result.error_code=='0':
                    assert result.fields==FIELDS.split(',') and len(result.data)<=3
                    assert result.code==item['params']['code'] and result.adjustflag=='3'
                if item['method']=='query_dividend_data' and result.error_code=='0':
                    assert result.code==item['params']['code'] and result.year=='2026' and result.yearType=='operate'
            except Exception as exc:
                receipt.update(finished_at=now(),exception={'type':type(exc).__name__,'message':str(exc)})
            save(f'{index:02d}_result.json',receipt);records.append(receipt)
            print(json.dumps({'ordinal':index,'method':item['method'],'code':item['params'].get('code'),'error_code':receipt.get('error_code'),'rows':len(receipt.get('data',[])),'exception':receipt.get('exception')},ensure_ascii=True),flush=True)
            if receipt.get('error_code')!='0' or receipt.get('exception'):break
    finally:
        saved_socket=getattr(context,'default_socket',None)
        if saved_socket is not None:saved_socket.close()
        socket.socket=Original
        save('summary.json',{'finished_at':now(),'plan_sha256':plan['plan_sha256'],'calls':records,
            'completed_all_queries':len(records)==len(plan['queries']) and all(x.get('error_code')=='0' and not x.get('exception') for x in records),
            'state':state,'no_event_claim':False,'actual_fee_contract_obtained':False,'execution_valid':False,'formal_target_success':False,'new_model_calls':0})
    return {'calls':len(records),'bytes_received':state['received_total'],'sends':state['sends']}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['freeze','probe']);a=p.parse_args()
    print(json.dumps(freeze() if a.action=='freeze' else probe(),ensure_ascii=True))
