"""Prepare local compatibility paths for source imported from the original CSI500 workspace.

No network, credentials, orders or deployment. Existing paths are never overwritten.
"""
from pathlib import Path
import shutil


def main():
    root = Path(__file__).resolve().parents[1]
    compat = root / 'quantagent'
    compat.mkdir(exist_ok=True)
    for name in ('src', 'scripts', 'research', 'docs'):
        target = compat / name
        if target.exists() or target.is_symlink():
            print(f'kept {target.relative_to(root)}')
            continue
        source = root / name
        if not source.is_dir():
            raise RuntimeError(f'Missing source directory: {name}')
        try:
            target.symlink_to(Path('..') / name, target_is_directory=True)
            kind = 'linked'
        except OSError:
            shutil.copytree(source, target)
            kind = 'copied'
        print(f'{kind} {target.relative_to(root)}')


if __name__ == '__main__':
    main()
