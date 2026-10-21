"""Compose the frozen V3 market/account inputs with reviewed primary V4 facts."""
import hashlib
import json
from trading_inputs_v3 import load_base_provider as load_v3_base_provider
from primary_reference_inputs_v4 import PrimaryReferenceInputsV4, PrimaryInputCatalogV4


def load_base_provider(directory, *, official_calendar, calendar_source, protocol_path):
    base=load_v3_base_provider(directory, official_calendar=official_calendar,
        calendar_source=calendar_source, protocol_path=protocol_path)
    inputs=PrimaryReferenceInputsV4()
    inputs.require_all100_admitted()
    base.catalog=PrimaryInputCatalogV4(base.catalog, inputs)
    base.source='sha256:'+hashlib.sha256(json.dumps(dict(original_source=base.source,
        independent_primary_v4_input_identity=inputs.source_identity),sort_keys=True).encode()).hexdigest()
    base.input_fingerprints=dict(base.input_fingerprints, independent_primary_v4=inputs.source_identity)
    return base
