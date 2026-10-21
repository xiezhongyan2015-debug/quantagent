"""Copy exact recent positive-control payloads without changing prior receipts."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import extract_intraday_concordance_samples as extractor


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    extractor.OUT = extractor.ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/original_recent_sina_control_20261009'
    extractor.TARGETS = {('20260922', '600004'), ('20260923', '600004')}
    extractor.OUT.mkdir(exist_ok=True)
    basis = {'started_utc': datetime.now(timezone.utc).isoformat(),
             'wrapper_path': str(Path(__file__).resolve()), 'wrapper_sha256': sha(__file__),
             'extractor_path': str(Path(extractor.__file__).resolve()),
             'extractor_sha256': sha(extractor.__file__),
             'targets': sorted(extractor.TARGETS),
             'scope': 'Two already complete member stockdays; diagnostic positive controls only'}
    path = extractor.OUT / 'invocation_basis.json'
    assert not path.exists(), 'Preserve existing run instead of overwriting its invocation'
    path.write_text(json.dumps(basis, ensure_ascii=False, indent=2))
    extractor.main()
    receipt_path = extractor.OUT / 'extraction_receipt.json'
    receipt = json.loads(receipt_path.read_text())
    assert sha(__file__) == basis['wrapper_sha256']
    assert sha(extractor.__file__) == basis['extractor_sha256']
    receipt['wrapper_path'] = basis['wrapper_path']
    receipt['wrapper_sha256'] = basis['wrapper_sha256']
    receipt['invocation_basis_sha256'] = sha(path)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
