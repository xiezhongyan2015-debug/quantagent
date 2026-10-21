import tempfile, unittest
from pathlib import Path
from unpaged_recovery import recover, RecoveryError

FIELDS=['ts_code','end_date','bz_code','value']
PARAMS={'ts_code':'TEST.SZ','type':'P','start_date':'20110101','end_date':'20261231'}

class RecoveryTests(unittest.TestCase):
    def test_capped_responses_split_and_resume_without_network(self):
        rows=[['TEST.SZ',f'{y}1231','P',j] for y in range(2011,2027) for j in range(20)]
        calls=[]
        def request(p):
            calls.append(p)
            selected=[r for r in rows if p['start_date']<=r[1]<=p['end_date']]
            return {'code':0,'data':{'fields':FIELDS,'items':selected[:100]}}
        with tempfile.TemporaryDirectory() as temp:
            got,artifacts,info=recover('fina_mainbz',PARAMS,FIELDS,request,Path(temp),'test')
            self.assertEqual(got,rows)
            self.assertGreater(len(calls),1)
            self.assertTrue(all(x['rows']<100 for x in info['leaves']))
            again,_,_=recover('fina_mainbz',PARAMS,FIELDS,lambda p:self.fail('network used on resume'),Path(temp),'test')
            self.assertEqual(again,rows)

    def test_ignored_date_filter_is_rejected(self):
        rows=[['TEST.SZ','20261231','P',j] for j in range(100)]
        request=lambda p:{'code':0,'data':{'fields':FIELDS,'items':rows}}
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RecoveryError,'report-date filter'):
                recover('fina_mainbz',PARAMS,FIELDS,request,Path(temp),'test')

    def test_single_date_at_cap_is_not_silently_complete(self):
        rows=[['TEST.SZ','20261231','P',j] for j in range(100)]
        def request(p):
            return {'code':0,'data':{'fields':FIELDS,'items':rows if p['start_date']<='20261231'<=p['end_date'] else []}}
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RecoveryError,'Single report date'):
                recover('fina_mainbz',PARAMS,FIELDS,request,Path(temp),'test')

    def test_name_history_needs_one_response(self):
        calls=[]
        def request(p):
            calls.append(p)
            return {'code':0,'data':{'fields':['ts_code','name'],'items':[['TEST.SZ','old'],['TEST.SZ','new']]}}
        with tempfile.TemporaryDirectory() as temp:
            rows,_,info=recover('namechange',{'ts_code':'TEST.SZ'},['ts_code','name'],request,Path(temp),'test')
            self.assertEqual(len(calls),1)
            self.assertEqual(len(rows),2)
            self.assertFalse(info['terminal_empty_page'])

if __name__=='__main__':
    unittest.main()
