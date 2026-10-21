"""Quick empirical check: annual Pearson-IC / RankIC of classic price-volume factors
on CSI300 / CSI500 / all-A tradeable, label = open[t+6]/open[t+1]-1, 2016-2024.
Purpose: estimate what single-factor IC levels the data/universe can support at all."""
import os, sys, glob, time
import numpy as np, pandas as pd
import pyarrow.parquet as pq

ROOT = r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/parquet_cn_a_qfq_tradeable_v2_2015_2025"
INST = r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin/instruments"
COLS = ["date","code","open","high","low","close","volume","amount","float_shares","float_market_cap","is_st","is_delisting"]

t0=time.time()
frames=[]
for f in glob.glob(os.path.join(ROOT,"*.parquet")):
    try:
        t=pq.read_table(f,columns=COLS).to_pandas()
    except Exception as e:
        continue
    frames.append(t)
df=pd.concat(frames,ignore_index=True)
df["code"]=df["code"].str.upper()
df=df.sort_values(["code","date"])
print("loaded",len(df),"rows",time.time()-t0)

def member_mask(name):
    m=pd.read_csv(os.path.join(INST,name),sep="	",header=None,names=["code","start","end"])
    m["start"]=pd.to_datetime(m["start"]); m["end"]=pd.to_datetime(m["end"])
    spans={k:list(zip(v["start"],v["end"])) for k,v in m.groupby("code")}
    out=np.zeros(len(df),bool)
    codes=df["code"].values; dates=df["date"].values
    for code,ix in df.groupby("code").indices.items():
        if code not in spans: continue
        d=dates[ix]; ok=np.zeros(len(ix),bool)
        for a,b in spans[code]:
            ok|=(d>=np.datetime64(a))&(d<=np.datetime64(b))
        out[ix]=ok
    return out

g=df.groupby("code",sort=False)
c=df["close"]; v=df["volume"]; o=df["open"]
ret1=g["close"].pct_change()
df["ret1"]=ret1
# label: open[t+6]/open[t+1]-1
df["label"]=g["open"].shift(-6)/g["open"].shift(-1)-1
# factors
df["rev20"]=-(c/g["close"].shift(20)-1)
df["mom_f1"]=g["close"].shift(20)/g["close"].shift(120)-1
df["vol20"]=-g["ret1"].rolling(20).std().reset_index(level=0,drop=True)
df["turn20"]=-(v/df["float_shares"]).groupby(df["code"]).rolling(20).mean().reset_index(level=0,drop=True)
df["size"]=-np.log(df["float_market_cap"])
df["amihud20"]=(ret1.abs()/df["amount"]).groupby(df["code"]).rolling(20).mean().reset_index(level=0,drop=True)
df["maxret20"]=-g["ret1"].rolling(20).max().reset_index(level=0,drop=True)
df["intra_over20"]=-(np.log(c/o)-np.log(o/g["close"].shift(1))).groupby(df["code"]).rolling(20).mean().reset_index(level=0,drop=True)
FACTORS=["rev20","mom_f1","vol20","turn20","size","amihud20","maxret20","intra_over20"]

base_elig=(~df["is_st"])&(~df["is_delisting"])&(df["amount"]>0)&df["label"].notna()
df["year"]=df["date"].dt.year

def zs(x):
    return (x-x.mean())/x.std(ddof=0)

def annual_ic(mask,title):
    sub=df[mask&base_elig&(df["year"].between(2016,2024))].copy()
    out=[]
    for fac in FACTORS+["combo_z4"]:
        if fac=="combo_z4":
            # equal-weight z-score of rev20,turn20,vol20,size within date
            parts=[]
            for f2 in ["rev20","turn20","vol20","size"]:
                parts.append(sub.groupby("date")[f2].transform(lambda s: zs(s.clip(s.quantile(.01),s.quantile(.99)))))
            sub[fac]=sum(parts)
        d=sub[["date","year",fac,"label"]].dropna()
        pear=d.groupby("date").apply(lambda x: x[fac].corr(x["label"]) if len(x)>=100 else np.nan)
        rank=d.groupby("date").apply(lambda x: x[fac].corr(x["label"],method="spearman") if len(x)>=100 else np.nan)
        yrs=pear.index.year
        row={"factor":fac}
        for y in range(2016,2025):
            row[f"P{y}"]=round(pear[yrs==y].mean(),4)
        for y in range(2016,2025):
            row[f"R{y}"]=round(rank[yrs==y].mean(),4)
        row["P_min19_24"]=round(min(row[f"P{y}"] for y in range(2019,2025)),4)
        row["R_min19_24"]=round(min(row[f"R{y}"] for y in range(2019,2025)),4)
        row["R_mean19_24"]=round(np.mean([row[f"R{y}"] for y in range(2019,2025)]),4)
        row["n_stocks_med"]=int(d.groupby("date").size().median())
        out.append(row)
    res=pd.DataFrame(out)
    pd.set_option("display.width",250); pd.set_option("display.max_columns",40)
    print("\n=====",title,"=====")
    print(res[["factor","n_stocks_med"]+[f"P{y}" for y in range(2019,2025)]+["P_min19_24"]].to_string(index=False))
    print(res[["factor"]+[f"R{y}" for y in range(2019,2025)]+["R_min19_24","R_mean19_24"]].to_string(index=False))
    return res

m300=member_mask("csi300.txt")
m500=member_mask("csi500.txt")
annual_ic(m300,"CSI300 (point-in-time members)")
annual_ic(m500,"CSI500 (point-in-time members)")
annual_ic(np.ones(len(df),bool),"ALL A-share tradeable")
print("done",time.time()-t0)
