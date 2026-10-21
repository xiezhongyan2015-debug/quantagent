"""One bounded public five-minute query for an original-source positive control."""
import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path
from public_baostock_probe import PublicClient
from extract_cached_quote_control_sample import ROOT, OUT, sha


def main():
    dest = OUT / 'control_coarse_reference'
    dest.mkdir(exist_ok=True)
    output = dest / 'bounded_reference_acquisition.json'
    assert not output.exists()
    result = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
              'personal_credentials_used': False, 'planned_queries': 1, 'frequency': '5',
              'coarse_bars_expanded_to_minutes': False, 'candidate_changed': False, 'candidate_hours_admitted': 0}
    client = None
    lock = (ROOT / 'acquired_data/data_round2/baostock_session.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        client = PublicClient(dest)
        client.login()
        record = client.query('sz.000009', '2026-09-23', '2026-09-23', '5')
        data = record.get('data', [])
        rows = data.get('record', []) if isinstance(data, dict) else data
        result.update(status='response_retained', actual_reference_rows=len(rows),
                      query_record={k: v for k, v in record.items() if k != 'data'})
    except Exception as error:
        result.update(status='failed_terminal', error_type=type(error).__name__)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as error:
                result['logout_error_type'] = type(error).__name__
            result['protocol_records'] = client.records
        lock.close()
    result['completed_utc'] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
