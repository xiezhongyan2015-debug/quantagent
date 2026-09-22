"""Atomic private journals and conservative fee reservations."""
import json
import os
from pathlib import Path
from decimal import Decimal

def save(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
    os.replace(temporary,path)


def reserve(amount):
    # Risk reserve remains conservative; research transaction cost is 0.0002.
    return (Decimal(str(amount))*Decimal('1.001')+Decimal('10')).quantize(Decimal('.01'),rounding='ROUND_CEILING')
