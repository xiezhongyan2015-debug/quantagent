"""Point-in-time security-name events verified against issuer disclosures."""
import datetime as dt
from .choice_pcf import TradeError

RENAME_300223={
    'code':'SZ300223','old_name':'北京君正','new_name':'君正股份',
    'effective_date':'2026-08-13','announcement_date':'2026-08-10',
    'source':'https://static.cninfo.com.cn/finalpage/2026-08-10/1225466827.PDF',
    'announcement_number':'2026-052',
}


def validate_known_name(code,name,asof_date):
    """Do not rewrite names or bypass code checks. Reject unexpected mapped names."""
    if code!=RENAME_300223['code']:return None
    date=dt.date.fromisoformat(str(asof_date))
    effective=dt.date.fromisoformat(RENAME_300223['effective_date'])
    expected=RENAME_300223['new_name'] if date>=effective else RENAME_300223['old_name']
    if name!=expected:raise TradeError('Security name differs from effective-dated issuer record')
    return dict(expected_name=expected,effective_date=RENAME_300223['effective_date'],source=RENAME_300223['source'])
