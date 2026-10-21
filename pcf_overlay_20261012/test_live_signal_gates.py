"""Signal release tests using temporary files, without network or orders."""
import tempfile,types,unittest
from pathlib import Path
import numpy as np
import pandas as pd
import live_targets as l
class Gates(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
  self.codes=[f'{i:06d}.SZ' for i in range(1,81)];self.pcf=pd.DataFrame(dict(code=self.codes,w=[1/80]*80));self.c5=pd.DataFrame(dict(code=self.codes,date=['2026-10-09']*80,state=['core']*80,raw_score=[.5]*80));self.sig=pd.DataFrame(dict(code=self.codes,date=['2026-10-09']*80,z=[.1]*80))
 def build(self,mode='V5'):
  self.c5.to_csv(self.path/'c5.csv',index=False);self.sig.to_csv(self.path/'signal.csv',index=False);a=types.SimpleNamespace(c5=str(self.path/'c5.csv'),signal=str(self.path/'signal.csv'),signal_date='2026-10-09',c5_min_coverage=.8,signal_min_coverage=.8,k=.5,min_weight=.0005,c5_date=None,refill='prorata',industry_map=None,tilt_form='mult',tilt_lambda=None)
  return l.build(a,self.pcf,{},None,set(self.codes),set(self.codes),1e7,.949,mode)
 def test_valid_signal_passes(self):self.assertFalse(self.build()[2])
 def test_duplicate_code_cannot_inflate_coverage(self):
  self.c5=pd.concat([self.c5.iloc[:30],self.c5.iloc[:30],self.c5.iloc[:30]]);self.assertTrue(any('duplicate' in x for x in self.build()[2]))
 def test_old_signal_day_rejected(self):
  self.c5['date']='2026-09-28';self.assertTrue(any('no rows' in x for x in self.build()[2]))
 def test_missing_schema_reports_gate_failure(self):
  self.c5=self.c5.drop(columns=['date']);self.assertTrue(any('lacks columns' in x for x in self.build()[2]))
 def test_infinite_raw_score_rejected(self):
  self.c5.loc[0,'raw_score']=np.inf;self.assertTrue(any('infinity' in x for x in self.build()[2]))
 def test_nonnumeric_score_rejected(self):
  self.c5['raw_score']=self.c5.raw_score.astype(object);self.c5.loc[0,'raw_score']='bad';self.assertTrue(any('nonnumeric' in x for x in self.build()[2]))
 def test_active_state_requires_score(self):
  self.c5.loc[0,'raw_score']=np.nan;self.assertTrue(any('active' in x for x in self.build()[2]))
 def test_empty_scores_do_not_count_for_coverage(self):
  self.c5.loc[20:,'state']='';self.c5.loc[20:,'raw_score']=np.nan;w,i,f=self.build();self.assertEqual(i['c5_coverage_of_pcf_csi500'],.25);self.assertTrue(f)
 def test_excluded_without_raw_score_is_kept(self):
  self.c5.loc[0,['state','raw_score']]=['excluded',np.nan];w,i,f=self.build();self.assertIn(self.codes[0],w.index);self.assertFalse(f)
 def test_explicit_veto_removes_even_without_score(self):
  self.c5.loc[0,['state','raw_score']]=['vetoed',np.nan];w,i,f=self.build();self.assertNotIn(self.codes[0],w.index);self.assertFalse(f)
 def test_unknown_state_rejected(self):
  self.c5.loc[0,'state']='invented';self.assertTrue(any('unknown state' in x for x in self.build()[2]))
 def test_signal_duplicate_rejected(self):
  self.sig=pd.concat([self.sig,self.sig.iloc[:1]]);self.assertTrue(any('duplicate' in x for x in self.build('V5_tilt')[2]))
 def test_signal_inf_rejected(self):
  self.sig.loc[0,'z']=np.inf;self.assertTrue(any('non-finite' in x for x in self.build('V5_tilt')[2]))
 def test_signal_missing_z_rejected(self):
  self.sig=self.sig.drop(columns=['z']);self.assertTrue(any('lacks columns' in x for x in self.build('V5_tilt')[2]))
 def test_valid_tilt_passes(self):self.assertFalse(self.build('V5_tilt')[2])
if __name__=='__main__':unittest.main()
