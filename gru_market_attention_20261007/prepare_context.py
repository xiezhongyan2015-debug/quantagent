"""Point-in-time supplemental inputs. Does not change original dataset."""
from pathlib import Path
import hashlib,json
import numpy as np,pandas as pd
P=Path(__file__).resolve().parent;ROOT=P.parent;D=ROOT/'gru_research/artifacts/dataset'
def sha(p):
 with open(p,'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def market_context(ohlcv,members,complete):
 close=np.asarray(ohlcv[:,:,3],float);volume=np.asarray(ohlcv[:,:,4],float)
 ret=np.full_like(close,np.nan);ret[1:]=np.log(close[1:]/close[:-1])
 valid=members & complete & np.isfinite(ret);valid[1:] &= complete[:-1]
 m=np.nanmean(np.where(valid,ret,np.nan),axis=1)
 breadth=np.sum(valid&(ret>0),axis=1)/np.maximum(valid.sum(axis=1),1)
 dispersion=np.nanstd(np.where(valid,ret,np.nan),axis=1)
 meanvol=np.nanmean(np.where(members&complete,volume,np.nan),axis=1)
 past=np.full(len(m),np.nan)
 for slot in range(4):past[slot::4]=pd.Series(meanvol[slot::4]).shift(1).rolling(20,min_periods=5).mean().to_numpy()
 ratio=np.log(np.maximum(meanvol,1)/np.maximum(past,1))
 vol=pd.Series(m).rolling(20,min_periods=5).std().to_numpy()
 mom=pd.Series(m).rolling(4,min_periods=4).sum().to_numpy()
 # Fixed units, no full-history fitted normalization.
 return np.stack([m*100,(breadth-.5)*2,ratio,dispersion*100,vol*100,mom*100],axis=1).astype('float32')
def main():
 load=lambda n:np.load(D/(n+'.npy'),mmap_mode='r')
 times=load('times');dates=(times+8*3600*10**9).astype('datetime64[ns]').astype('datetime64[D]');members=load('members');complete=load('hour_complete');ohlcv=load('hour_ohlcv')
 market=market_context(ohlcv,members,complete)
 cut=5234
 # Prefix reconstruction tests all rolling calculations, independent of future rows.
 assert np.allclose(market[:cut],market_context(ohlcv[:cut],members[:cut],complete[:cut]),equal_nan=True)
 src=ROOT/'中证500_历年成分股_基本面_2010-2026_iFinD_PIT/weekly_market_and_industry.parquet'
 df=pd.read_parquet(src);assert not df.duplicated(['asof_date','thscode']).any()
 df['date']=pd.to_datetime(df.asof_date).values.astype('datetime64[D]')
 vocab=sorted(df.loc[df.date<'2024-01-01','sw_l1_provider_code'].dropna().unique());mapping={v:i+1 for i,v in enumerate(vocab)}
 industry=np.zeros(members.shape,dtype='int16');known_asof=np.full(members.shape,np.datetime64('NaT','D'))
 groups={k:g.sort_values('date') for k,g in df.groupby('thscode')}
 for u,s in enumerate(json.loads((D/'symbols.json').read_text())):
  if s not in groups:continue
  g=groups[s];gd=g.date.to_numpy().astype('datetime64[D]');ix=np.searchsorted(gd,dates,side='left')-1;ok=ix>=0
  codes=g.sw_l1_provider_code.map(mapping).fillna(0).to_numpy(dtype='int16');industry[ok,u]=codes[ix[ok]];known_asof[ok,u]=gd[ix[ok]]
 assert np.all(known_asof[~np.isnat(known_asof)]<np.broadcast_to(dates[:,None],members.shape)[~np.isnat(known_asof)])
 np.savez_compressed(P/'context.npz',market=market,industry=industry,times=times)
 meta=json.loads((D/'meta.json').read_text())
 manifest={'dataset_fingerprint':hashlib.sha256(json.dumps(meta,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'context_sha256':sha(P/'context.npz'),'source_sha256':sha(src),'vocabulary_fit_before':'2024-01-01','industry_vocabulary':mapping,'n_industries':len(mapping)+1,'industry_availability':'latest weekly snapshot strictly before signal calendar date; unseen codes -> 0','known_industry_fraction':float((industry[members]>0).mean()),'market_names':['member_mean_logreturn_x100','member_breadth_centered','log_same_slot_volume_ratio_past20days','member_return_dispersion_x100','past20hour_market_vol_x100','past4hour_market_return_x100'],'market_prefix_invariance':True,'source_taxonomy_status':'historical provider S-codes; not externally certified stable SW taxonomy','original_dataset_status':'diagnostic use; previously observed whole-day-quality latent causal defect had zero historical triggers; no end-to-end certification'}
 (P/'context_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2));print(json.dumps(manifest,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
