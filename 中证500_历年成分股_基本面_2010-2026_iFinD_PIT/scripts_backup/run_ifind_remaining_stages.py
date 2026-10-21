"""Run verified remaining stages sequentially, stopping on any stage failure."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for stage in ('download_ifind_industry.py', 'download_ifind_cashflow.py'):
    subprocess.run([sys.executable, str(ROOT/'scripts'/stage)], cwd=ROOT, check=True)
