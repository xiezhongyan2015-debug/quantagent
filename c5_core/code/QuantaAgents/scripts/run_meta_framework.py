from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))

from quanta_agents.meta.runtime import Engine
from quanta_agents.meta.server import StateLock, make_server
from quanta_agents.meta.store import dumps


def main():
    parser = argparse.ArgumentParser(description='Astra xhigh 元研究框架与实时监控')
    parser.add_argument('command', choices=['serve', 'run'])
    parser.add_argument('--root', type=Path, default=PROJECT / 'experiment_traces' / 'meta')
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--mode', choices=['live', 'fixture'], default='live')
    parser.add_argument('--case', choices=['synthetic', 'ashare'], default='synthetic')
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    state_lock = StateLock(args.root)
    engine = Engine(args.root)
    try:
        if args.command == 'serve':
            server = make_server(engine, args.port)
            print(f'Meta Research GUI: http://192.0.2.10:{server.server_port}', flush=True)
            try:
                server.serve_forever(poll_interval=0.25)
            finally:
                server.server_close()
        else:
            run_id = engine.create(args.mode, case=args.case)
            print(dumps({'run_id': run_id, 'mode': args.mode}), flush=True)
            cursor = 0
            while engine.worker and engine.worker.is_alive():
                for event in engine.store.events(run_id, cursor):
                    cursor = event['id']
                    print(dumps(event), flush=True)
                time.sleep(0.5)
            result = engine.get(run_id)
            print(dumps({'run_id': run_id, 'status': result['status'], 'usage': result['usage'],
                         'elapsed_seconds': result['elapsed_seconds'], 'comparison': result['comparison'],
                         'last_error': result.get('last_error')}), flush=True)
            return 0 if result['status'] == 'completed' else 1
    except KeyboardInterrupt:
        print('Stopping local worker...', flush=True)
    finally:
        engine.close()
        state_lock.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
