"""Authenticate the original minute positive control against the complete archive."""
import json
from datetime import datetime, timezone
from pathlib import Path
import extract_intraday_concordance_samples as extractor
from extract_cached_quote_control_sample import sha


def main():
    extractor.OUT = extractor.ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/additional_native_inventory_20261009/quote_boundary_audit/original_control'
    extractor.TARGETS = {('20260923', '000009')}
    extractor.OUT.mkdir(exist_ok=True)
    basis = {'started_utc': datetime.now(timezone.utc).isoformat(), 'wrapper_path': str(Path(__file__).resolve()),
             'wrapper_sha256': sha(__file__), 'extractor_path': str(Path(extractor.__file__).resolve()),
             'extractor_sha256': sha(extractor.__file__), 'targets': sorted(extractor.TARGETS),
             'scope': 'One already complete stockday; quote-definition positive control only'}
    path = extractor.OUT / 'invocation_basis.json'
    assert not path.exists()
    path.write_text(json.dumps(basis, ensure_ascii=False, indent=2))
    extractor.main()
    receipt_path = extractor.OUT / 'extraction_receipt.json'
    receipt = json.loads(receipt_path.read_text())
    assert sha(__file__) == basis['wrapper_sha256'] and sha(extractor.__file__) == basis['extractor_sha256']
    receipt.update(wrapper_path=basis['wrapper_path'], wrapper_sha256=basis['wrapper_sha256'],
                   invocation_basis_sha256=sha(path), checked_utc=datetime.now(timezone.utc).isoformat())
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
