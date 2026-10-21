"""One bounded attempt for the seven remaining explicitly documented peers."""
import hashlib
import json
from pathlib import Path
import probe_documented_tdx_modern_peers as probe

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/tdx_remaining_documented_peers_20261009'
PEERS = [('123.60.73.44', 7709), ('116.205.163.254', 7709), ('121.36.225.169', 7709),
         ('123.60.70.228', 7709), ('124.71.9.153', 7709), ('110.41.147.114', 7709), ('124.71.187.122', 7709)]


def main():
    OUT.mkdir(exist_ok=True)
    invocation = OUT / 'invocation_basis.json'
    assert not invocation.exists(), 'Preserve previous acquisition before another attempt'
    basis = {'wrapper_path': str(Path(__file__)), 'wrapper_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'reviewed_own_query_code_path': probe.__file__, 'reviewed_own_query_code_sha256': probe.sha(probe.__file__),
        'explicit_documented_peers': PEERS, 'candidate_changed': False, 'personal_credentials_used': False}
    invocation.write_text(json.dumps(basis, indent=2))
    probe.OUT = OUT
    probe.PEERS = PEERS
    probe.main()
    path = OUT / 'probe_receipt.json'
    receipt = json.loads(path.read_text())
    receipt.update({'invocation_basis_path': str(invocation), 'invocation_basis_sha256': probe.sha(invocation),
        'wrapper_source_code_path': str(Path(__file__)), 'wrapper_source_code_sha256': basis['wrapper_sha256'],
        'bounded_run_complete': True})
    path.write_text(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
