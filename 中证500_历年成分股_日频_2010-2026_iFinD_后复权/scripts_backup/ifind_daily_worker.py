"""Dedicated daily-data login and request queue; minute jobs remain stopped."""
import traceback
from pathlib import Path
import ifind_api_worker as worker

worker.ROOT = Path(__file__).resolve().parent.parent / 'data_expansion' / 'ifind_daily_hfq_control'
worker.JOBS = worker.ROOT / 'jobs'
worker.RESULTS = worker.ROOT / 'results'
worker.JOBS.mkdir(parents=True, exist_ok=True)
worker.RESULTS.mkdir(parents=True, exist_ok=True)

if __name__ == '__main__':
    try:
        worker.main('中证500历史日频下载 · iFinD 本机登录', concurrency=12)
    except Exception:
        worker.state('crashed', error=traceback.format_exc())
        raise
