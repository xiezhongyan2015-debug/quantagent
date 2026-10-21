"""Keep historical membership as the fitting cohort for frozen CS formulas.

Nonmember observations are retained for causal time-series warmup. They never
participate in the cross-sectional fit, even if they join the index later.
"""
import ast,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'gru_research/.deps'))
import pyarrow
import numpy as np
import pandas as pd

sys.path.insert(0,str(ROOT/'quantagent/src'))
from quanta_agents.factor_lab_a.dsl import Compiler,Ops


class HistoricalCompiler(Compiler):
    def __init__(self,members):
        super().__init__(strict=False)
        self.members=members.astype(bool)

    def cohort(self,values):
        mask=self.members.reindex(index=values.index,columns=values.columns)
        if mask.isna().any().any():raise ValueError('Unknown historical cohort key')
        return mask

    def cs_rank(self,values):
        a=values.to_numpy(float);cohort=self.cohort(values).to_numpy(bool)
        out=np.full(a.shape,np.nan)
        for i,row in enumerate(a):
            ref=np.sort(row[cohort[i]&np.isfinite(row)])
            if not len(ref):continue
            valid=np.isfinite(row)
            left=np.searchsorted(ref,row[valid],side='left')
            right=np.searchsorted(ref,row[valid],side='right')
            # Member ties reproduce pandas average-rank / member count.
            # Outsiders receive a bounded position against that same cohort.
            out[i,valid]=np.clip((left+(right-left+1)/2)/len(ref),0,1)
        return pd.DataFrame(out,index=values.index,columns=values.columns)

    def cs_neutralize(self,y,x):
        if not isinstance(x,pd.DataFrame):x=pd.DataFrame(float(x),index=y.index,columns=y.columns)
        valid=np.isfinite(y)&np.isfinite(x)
        fit=valid&self.cohort(y)
        yy=y.where(fit);xx=x.where(fit)
        mean_y=yy.mean(axis=1);mean_x=xx.mean(axis=1)
        ym=yy.sub(mean_y,axis=0);xm=xx.sub(mean_x,axis=0)
        beta=(xm*ym).sum(axis=1)/(xm*xm).sum(axis=1)
        out=y.sub(mean_y,axis=0)-x.sub(mean_x,axis=0).mul(beta,axis=0)
        return out.where(valid).replace([np.inf,-np.inf],np.nan)

    def _eval(self,node,env):
        if isinstance(node,ast.Call) and node.func.id.startswith('cs_'):
            if node.func.id not in {'cs_rank','cs_neutralize'}:
                raise ValueError('Unreviewed cross-sectional operation: '+node.func.id)
            args=[self._eval(a,env) for a in node.args]
            return getattr(self,node.func.id)(*args)
        return super()._eval(node,env)


def validate():
    rng=np.random.default_rng(1908);index=pd.date_range('2019-01-01',periods=140);columns=list('abcdef')
    a=pd.DataFrame(rng.normal(size=(140,6)),index=index,columns=columns);a.iloc[3:8,1]=np.nan;a.iloc[10,2]=a.iloc[10,0]
    b=pd.DataFrame(rng.normal(size=(140,6)),index=index,columns=columns)
    all_members=pd.DataFrame(True,index=index,columns=columns);compiler=HistoricalCompiler(all_members)
    for fn,args in [('cs_rank',[a]),('cs_neutralize',[a,b])]:
        np.testing.assert_allclose(getattr(compiler,fn)(*args),getattr(Ops,fn)(*args),atol=1e-12,rtol=1e-12,equal_nan=True)
    members=all_members.copy();members.iloc[:,4:]=False;compiler=HistoricalCompiler(members)
    changed=a.copy();changed.iloc[:,4:]=1e12
    changed_b=b.copy();changed_b.iloc[:,4:]=-1e12
    for fn,args,altered in [('cs_rank',[a],[changed]),('cs_neutralize',[a,b],[changed,changed_b])]:
        x=getattr(compiler,fn)(*args);y=getattr(compiler,fn)(*altered)
        np.testing.assert_allclose(x.iloc[:,:4],y.iloc[:,:4],atol=1e-12,rtol=1e-12,equal_nan=True)
    expressions=['rolling_max(cs_rank(ret), 10)','cs_neutralize(rolling_min(low, 10) / close, close / lag(close, 20) - 1)']
    close=np.exp(a.cumsum().fillna(0)/100)+10
    fields={'close':close,'low':close*.98,'ret':a}
    first=HistoricalCompiler(members.iloc[:100])
    for expression in expressions:
        full=compiler.evaluate(expression,fields=fields)
        prefix=first.evaluate(expression,fields={k:v.iloc[:100] for k,v in fields.items()})
        np.testing.assert_allclose(full.iloc[:100],prefix,atol=1e-12,rtol=1e-12,equal_nan=True)
    return {'passed':True,'all_member_operations_match_original':True,
            'nonmember_extremes_do_not_change_member_results':True,'future_append_does_not_change_past':True,
            'preentry_values_use_historical_member_cohort_without_entering_fit':True}


if __name__=='__main__':print(json.dumps(validate()))
