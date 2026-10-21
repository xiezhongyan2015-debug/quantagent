"""Read an existing dated research signal; no models or trading calls."""
from pathlib import Path
import argparse,csv,datetime as dt
import numpy as np

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--arm',choices=['R','H','C'],required=True)
    p.add_argument('--signal-date',required=True)
    p.add_argument('--out',required=True)
    a=p.parse_args()
    wanted=(dt.date.fromisoformat(a.signal_date)-dt.date(1970,1,1)).days
    directory=Path(__file__).resolve().parent/a.arm
    with np.load(directory/'scores.npz',allow_pickle=False) as data, np.load(directory/'mask.npz',allow_pickle=False) as masks:
        matches=np.flatnonzero(data['dates']==wanted)
        if len(matches)!=1:raise ValueError('Exactly one existing signal date required')
        i=int(matches[0])
        if not np.array_equal(data['dates'],masks['dates']) or not np.array_equal(data['symbols'],masks['symbols']):
            raise ValueError('Signal/mask axes differ')
        dest=Path(a.out)
        if dest.exists():raise FileExistsError('Preserve existing export')
        fields=['signal_date','signal_time_ns_UTC','code','pcf_code6','exchange','alpha_common_scale_utility','ensemble_raw_score','has_score','neutral_alpha']
        with dest.open('x',encoding='utf-8-sig',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
            for j in np.flatnonzero(masks['mask'][i]):
                symbol=str(data['symbols'][j])
                if len(symbol)!=9 or not symbol[:6].isdigit() or symbol[6:] not in ('.SH','.SZ','.BJ'):
                    raise ValueError('Unsupported source stock identity')
                value=float(data['scores'][i,j]);raw=float(data['ensemble_raw_scores'][i,j])
                valid=np.isfinite(value)
                writer.writerow(dict(signal_date=a.signal_date,signal_time_ns_UTC=int(data['signal_time_ns'][i]),
                    code=symbol,pcf_code6=symbol[:6],exchange=symbol[7:],alpha_common_scale_utility=value if valid else '',
                    ensemble_raw_score=raw if np.isfinite(raw) else '',has_score=int(valid),
                    neutral_alpha=value if valid else 0))
    print(str(dest))
if __name__=='__main__':main()
