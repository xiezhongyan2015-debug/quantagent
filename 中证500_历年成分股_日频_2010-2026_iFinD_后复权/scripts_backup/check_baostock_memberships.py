"""Compare BaoStock historical constituents with the saved iFinD anomalies."""
import gzip
import json
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

WORK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORK / 'data_expansion/baostock_check_deps'))
import baostock as bs
import baostock.common.context as bs_context

DATA = Path(r'E:\中证500_历年成分股_日频_2010-2026_iFinD_后复权')
OUT = WORK / 'data_expansion/baostock_membership_check'
OUT.mkdir(exist_ok=True)
socket.setdefaulttimeout(20)


def main():
    lg = bs.login()
    if lg.error_code != '0':
        raise RuntimeError(f'BaoStock login: {lg.error_code} {lg.error_msg}')
    report = json.loads((DATA / 'audit/final_quality_report.json').read_text(encoding='utf-8'))
    dates = sorted({x['date'] for x in report['membership_anomalies']} |
                   {'2018-12-27', '2019-01-18', '2021-09-10', '2021-09-28'})
    comparisons = []
    try:
        for date in dates:
            saved = OUT / (date + '.json')
            raw = json.loads(saved.read_text(encoding='utf-8')) if saved.exists() else {}
            if raw.get('error_code') != '0':
                for attempt in range(2):
                    rs = bs.query_zz500_stocks(date=date)
                    rows = []
                    while rs.error_code == '0' and rs.next():
                        rows.append(dict(zip(rs.fields, rs.get_row_data())))
                    raw = dict(requested_date=date, error_code=rs.error_code, error_msg=rs.error_msg,
                               fields=rs.fields, rows=rows, queried_at=datetime.now().isoformat())
                    saved.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')
                    if rs.error_code == '0':
                        break
                    print(f'Retrying {date}: {rs.error_code} {rs.error_msg}', flush=True)
                    getattr(bs_context, 'default_socket').close()
                    time.sleep(5)
                    lg = bs.login()
                    if lg.error_code != '0':
                        raise RuntimeError(f'Reconnect failed: {lg.error_code} {lg.error_msg}')
                if raw['error_code'] != '0':
                    raise RuntimeError(f'BaoStock repeated failure for {date}')
            rows = raw['rows']
            codes = {r['code'].split('.')[1] + '.' + r['code'].split('.')[0].upper() for r in rows}
            with gzip.open(DATA / 'memberships' / (date + '.json.gz'), 'rt', encoding='utf-8') as stream:
                other = json.load(stream)
            ifind = {c for table in other['data'] for c in table['table']['THSCODE']}
            comparison = dict(date=date, baostock_count=len(codes), row_count=len(rows), ifind_count=len(ifind),
                              update_dates=sorted({r.get('updateDate', '') for r in rows}),
                              only_baostock=sorted(codes-ifind), only_ifind=sorted(ifind-codes),
                              added_names=[r for r in rows if r['code'].split('.')[1]+'.'+r['code'].split('.')[0].upper() in codes-ifind])
            comparisons.append(comparison)
            (OUT / 'comparison.json').write_text(json.dumps(comparisons, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(comparison, ensure_ascii=False), flush=True)
            time.sleep(2)
    finally:
        bs.logout()


if __name__ == '__main__':
    main()
