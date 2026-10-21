"""Compose unchanged V2 market loading with verified primary action facts."""
from datetime import date
import hashlib
import json
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from trading_inputs import load_base_provider as load_v2_base_provider
from primary_action_supplement_v3 import PrimaryActionSupplementV3,PrimarySupplementCatalogV3


def load_base_provider(directory,*,official_calendar,calendar_source,protocol_path):
    # Every vendor original query/row reconciliation still runs in the V2 loader.
    base=load_v2_base_provider(directory,official_calendar=official_calendar,
        calendar_source=calendar_source,protocol_path=protocol_path)
    proof=PrimaryActionSupplementV3()
    base.catalog=PrimarySupplementCatalogV3(base.catalog,proof)
    base.source='sha256:'+hashlib.sha256(json.dumps(dict(original_source=base.source,
        primary_receipt=proof.receipt_sha256,primary_overlay=proof.overlay_sha256),sort_keys=True).encode()).hexdigest()
    base.input_fingerprints=dict(base.input_fingerprints,primary_action_receipt=proof.receipt_sha256,
        primary_action_overlay=proof.overlay_sha256)
    return base
