"""Idempotent submission; at most three CPU tasks leave four CPUs for the GPU."""
from datetime import datetime
from pathlib import Path
import json
import subprocess


def main():
    path = Path('outputs/gru_dualplatform_20261011/cluster_submit_receipt.json')
    receipt = json.loads(path.read_text())
    assert receipt['A1_remaining'] == '423607' and receipt['A4'] == '423608'
    assert Path('/data/users/s1155284034/gru_daily_20261011/daily_audit.json').exists()
    assert json.loads(Path('/data/users/s1155284034/gru_daily_20261011/daily_audit.json').read_text())['status'] == 'passed'

    def save():
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(receipt, indent=2))
        temporary.replace(path)

    def submit(*args):
        return subprocess.check_output(['sbatch', '--parsable', *args], text=True).strip().split(';')[0]

    if 'B2_cluster_gru_24' not in receipt:
        receipt['B2_cluster_gru_24'] = submit('--dependency=afterany:423607', 'gru_dualplatform_20261011/daily_gru_cluster.sbatch')
        save()
    gpu_job = receipt['B2_cluster_gru_24']
    if receipt.get('A4_dependency') != f'afterany:{gpu_job}':
        subprocess.run(['scontrol','update','JobId=423608',f'Dependency=afterany:{gpu_job}'],check=True)
        receipt['A4_dependency'] = f'afterany:{gpu_job}'
        save()
    arrays = receipt.setdefault('B3_cluster_lgb_30', [])
    for k, seed in enumerate([11,22,33,44,55]):
        if k < len(arrays):
            assert arrays[k]['seed'] == seed
            continue
        dependency = [] if not arrays else ['--dependency=afterok:' + arrays[-1]['job_id']]
        job = submit(*dependency, f'--export=ALL,LGB_SEED={seed}', 'gru_dualplatform_20261011/lightgbm.sbatch')
        arrays.append({'seed':seed,'job_id':job,'array':'0-5%3'})
        save()
    receipt['B_submitted_at'] = datetime.now().astimezone().isoformat()
    save()
    Path('outputs/cluster_sync_20261011/dualplatform_submit_receipt.json').write_text(path.read_text())
    print(path.read_text(),flush=True)


if __name__ == '__main__':
    main()
