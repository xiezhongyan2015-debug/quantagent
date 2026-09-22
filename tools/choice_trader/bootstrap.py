"""Prepare the local Windows environment; never access an account or start trading."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
REPO = BASE.parent.parent


def run(argv):
    return subprocess.run([str(arg) for arg in argv], cwd=REPO, check=False).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-executable', default=sys.executable)
    parser.add_argument('--skip-install', action='store_true')
    args = parser.parse_args(argv)
    if os.name != 'nt':
        print('Run this installer in the local Windows environment that hosts Choice.')
        return 1
    environment = REPO / '.venv'
    python = environment / 'Scripts/python.exe'
    version_check = 'import sys, venv, tkinter; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
    if not python.is_file():
        if args.skip_install:
            print('No .venv found. Run without --skip-install first. Existing files were preserved.')
            return 1
        if run([args.python_executable, '-c', version_check]): return 1
        if run([args.python_executable, '-m', 'venv', environment]): return 1
    if run([python, '-c', version_check]): return 1
    if not args.skip_install:
        if run([python, '-m', 'pip', 'install', '--disable-pip-version-check', '-r', BASE / 'requirements.txt']): return 1
    if run([python, '-c', 'import requests, psutil, cryptography, numpy, pandas, pyarrow, yaml, tkinter']): return 1
    if run([python, '-m', 'unittest', 'discover', '-s', BASE, '-p', 'test_*.py', '-q']): return 1
    print('Environment and offline tests passed. No account login or trading performed.', flush=True)
    result = run([python, BASE / 'onboarding.py', 'inspect'])
    # Needs-input is the expected result for an unconfigured new teammate.
    return 0 if result in (0, 2) else result


if __name__ == '__main__':
    raise SystemExit(main())
