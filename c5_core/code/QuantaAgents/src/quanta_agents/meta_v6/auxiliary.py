"""Explicitly registered auxiliary observations, never a replacement price feed.

Registration inspects metadata and hashes only. Numeric attachment is a separate
operation requiring an admission bound to both that registry and a base panel.
Neither step calls a factor evaluator, model or account execution engine.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .data import (
    MarketPanel, _MemorySample, _cache_read, _cache_write, _code, _day, _digest,
    _need, _sha, _source_unchanged, inspect_parquet_sources,
)


VERSION = "v6_registered_auxiliary_v1"
HF0280_FIELDS = (
    "gu_1m", "gd_1m", "rbar_up17", "rbar_down17", "r_0931_1000",
    "r_1001_1030", "overnight_return",
)


def _field_list(fields):
    fields = list(fields)
    _need(bool(fields) and len(fields) == len(set(fields)), "unique nonempty auxiliary fields required")
    _need(set(fields) <= set(HF0280_FIELDS), "only the seven HF0280 auxiliary fields may be admitted")
    return [name for name in HF0280_FIELDS if name in fields]


def register_auxiliary_source(source_root, *, symbols, start, end,
                              authorized_start, authorized_end,
                              fields=HF0280_FIELDS, source_notes=""):
    """Freeze source metadata without reading market value column arrays.

    Source identities and missing files are retained for the complete fixed
    universe. Footer date bounds and null counts can span unadmitted dates;
    this function never interprets those as admitted numerical observations.
    """
    start, end = _day(start), _day(end)
    first, last = _day(authorized_start), _day(authorized_end)
    _need(first <= start <= end <= last, "auxiliary registration outside authorization")
    codes = [_code(code) for code in symbols]
    _need(bool(codes) and len(codes) == len(set(codes)), "fixed unique universe required")
    fields = _field_list(fields)
    sources = inspect_parquet_sources(source_root, codes)
    registry = {
        "version": VERSION, "loader_sha256": _sha(__file__),
        "source_root": str(Path(source_root).resolve()), "symbols": codes,
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "authorized_start": first.date().isoformat(), "authorized_end": last.date().isoformat(),
        "fields": fields, "source_files": sources,
        "metadata_summary": {"universe_symbols": len(codes),
            "present_files": sum(p["status"] == "present" for p in sources),
            "missing_symbols": [p["symbol"] for p in sources if p["status"] == "missing"],
            "files_containing_each_field": {f: sum(f in p.get("columns", []) for p in sources) for f in fields}},
        "exposure": "previously_exposed_development", "globally_unseen": False,
        "price_basis": "provider-stored factor-calendar derived statistics, copied without rescaling; provider normalization and correction vintage not independently certified",
        "timing": {"decision": "after completed daily bar", "historical_available_at_verified": False,
            "publication_history_verified": False},
        "source_notes": str(source_notes), "market_value_arrays_read": False,
        "numeric_admission_required": True,
        "range_isolation": "metadata/hash inventory only; footer date bounds may span 2025; no numeric market values inspected",
        "model_calls": 0, "factor_evaluations": 0, "account_executions": 0,
    }
    registry["registration_id"] = _digest(registry)
    return registry


def _check_registry(registry):
    body = {k: v for k, v in registry.items() if k != "registration_id"}
    _need(_digest(body) == registry.get("registration_id"), "auxiliary registry identity mismatch")
    _need(registry.get("version") == VERSION and registry.get("loader_sha256") == _sha(__file__),
          "auxiliary registry implementation changed")
    _need(_field_list(registry["fields"]) == registry["fields"], "noncanonical auxiliary field order")
    _need(registry.get("exposure") == "previously_exposed_development", "auxiliary source is not admitted development")
    _need(registry.get("numeric_admission_required") is True, "registry must require separate numerical admission")
    root = Path(registry["source_root"]).resolve()
    _need([p["symbol"] for p in registry["source_files"]] == registry["symbols"], "registry universe binding mismatch")
    for proof in registry["source_files"]:
        _need(Path(proof["path"]).resolve() == root / (_code(proof["symbol"]) + ".parquet"), "auxiliary source path mismatch")
        _source_unchanged(proof)


def load_auxiliary_panel(panel: MarketPanel, registry: dict, *, admission: dict,
                         cache_dir=None) -> MarketPanel:
    """Attach only explicitly admitted auxiliary columns to a new panel.

    admission requires registration_id, base_panel_fingerprint, authorized_start,
    authorized_end, fields, purpose and authorization_reference. This receipt is
    supplied by the research controller after an explicit source decision; it
    is not inferred from an available file or a factor's requested expression.
    Existing nonmissing auxiliary values cannot be overwritten. Base prices,
    volume, membership-derived eligibility and fixed axes are copied unchanged.
    """
    with _MemorySample() as meter:
        _need(isinstance(admission, dict), "explicit numeric auxiliary admission required")
        required = {"registration_id", "base_panel_fingerprint", "authorized_start",
                    "authorized_end", "fields", "purpose", "authorization_reference"}
        _need(required <= set(admission), "incomplete explicit numeric auxiliary admission")
        _need(bool(str(admission["purpose"]).strip()) and bool(str(admission["authorization_reference"]).strip()),
              "numeric admission needs purpose and authorization reference")
        _need(admission["registration_id"] == registry.get("registration_id"), "admission references another source registry")
        fields = _field_list(admission["fields"])
        _need(fields == registry["fields"], "numeric field admission must match frozen registry")
        start, end = _day(registry["start"]), _day(registry["end"])
        first, last = _day(admission["authorized_start"]), _day(admission["authorized_end"])
        _need(_day(registry["authorized_start"]) <= first <= start <= end <= last <= _day(registry["authorized_end"]),
              "numeric auxiliary dates exceed registration authorization")
        _need(panel.symbols == registry["symbols"], "auxiliary universe must exactly match fixed base panel")
        request = panel.provenance.get("request", {})
        _need(request.get("start") == registry["start"] and request.get("end") == registry["end"],
              "auxiliary range must exactly match base panel request")
        _need(panel.dates.min() >= start and panel.dates.max() <= end, "base panel contains out-of-scope dates")
        _need(panel.provenance.get("source_class") == "previously_exposed_development", "base panel is not admitted development")
        for name in fields:
            _need(name not in panel.fields or not panel.fields[name].notna().any().any(),
                  "refuse to overwrite existing nonmissing auxiliary field: " + name)
        base_fingerprint = panel.fingerprint()
        _need(admission["base_panel_fingerprint"] == base_fingerprint, "numeric admission references another base panel")
        _check_registry(registry)
        identity = deepcopy(request)
        identity["auxiliary_attachment"] = {"version": VERSION, "loader_sha256": _sha(__file__),
            "base_panel_fingerprint": base_fingerprint, "registration_id": registry["registration_id"],
            "admission": deepcopy(admission)}
        key = _digest(identity)
        folder = Path(cache_dir).resolve() / ("auxiliary-" + key) if cache_dir is not None else None
        hit = folder is not None and folder.exists()
        if hit:
            result = _cache_read(folder, key)
        else:
            result_fields = {name: value.copy(deep=True) for name, value in panel.fields.items()}
            for name in fields:
                result_fields[name] = pd.DataFrame(np.nan, index=panel.dates, columns=panel.eligible.columns)
            quality = {}
            for proof in registry["source_files"]:
                code = proof["symbol"]
                available = [f for f in fields if f in proof.get("columns", [])]
                if proof["status"] == "missing" or not available:
                    quality[code] = {"returned_rows": 0, "status": proof["status"], "available_fields": available}
                    continue
                table = pq.read_table(proof["path"], columns=["date", "code", *available],
                    filters=[("date", ">=", start.to_pydatetime()), ("date", "<=", end.to_pydatetime())])
                frame = table.to_pandas()
                frame["date"] = pd.to_datetime(frame["date"])
                _need(not len(frame) or set(frame["code"].str.lower()) == {code}, "foreign code in auxiliary source")
                _need(not frame["date"].duplicated().any(), "duplicate auxiliary stock/date")
                _need(frame["date"].between(start, end).all(), "auxiliary predicate returned out-of-scope numeric rows")
                _need(frame["date"].isin(panel.dates).all(), "auxiliary dates absent from base market calendar")
                numeric = frame.set_index("date")[available].apply(pd.to_numeric, errors="raise").astype(float)
                invalid = {name: int(np.isinf(numeric[name]).sum()) for name in available}
                numeric = numeric.replace([np.inf, -np.inf], np.nan)
                for name in available:
                    result_fields[name].loc[numeric.index, code] = numeric[name]
                quality[code] = {"returned_rows": len(frame), "status": "present",
                    "available_fields": available, "infinite_values_replaced_with_missing": invalid}
            provenance = deepcopy(panel.provenance)
            provenance["request"], provenance["cache_key"] = identity, key
            provenance["auxiliary"] = {"registration": deepcopy(registry), "admission": deepcopy(admission),
                "base_panel_fingerprint": base_fingerprint,
                "fields": fields, "source_quality": quality,
                "nonmissing_stock_days": {name: int(result_fields[name].notna().to_numpy().sum()) for name in fields},
                "join": "exact stock/date left join on frozen base axes; no fill and no rescaling; eligibility unchanged",
                "numeric_range": [start.date().isoformat(), end.date().isoformat()],
                "range_isolation": "Parquet predicate before Python materialization; returned/cached/statistical rows exclude out-of-range dates; storage pages may overlap 2025",
                "historical_available_at_verified": False, "execution_certified": False}
            provenance["causal_fields"] = list(dict.fromkeys([*provenance.get("causal_fields", []), *fields]))
            missing = provenance.setdefault("missing", {})
            counts = missing.setdefault("field_missing_cells", {})
            counts.update({name: int(result_fields[name].isna().to_numpy().sum()) for name in fields})
            result = MarketPanel(result_fields, panel.eligible.copy(deep=True), provenance)
            if folder is not None:
                _cache_write(folder, key, result)
        for proof in registry["source_files"]:
            _source_unchanged(proof)
        _need(result.eligible.equals(panel.eligible), "auxiliary attachment changed base eligibility")
        for name in set(panel.fields) - set(fields):
            _need(result.fields[name].equals(panel.fields[name]), "auxiliary attachment changed a base field")
    result.load_metrics = {**meter.metrics, "cache_hit": hit, "cache_key": key,
        "panel_bytes": result.nbytes, "base_panel_fingerprint": base_fingerprint,
        "registration_id": registry["registration_id"], "market_values_reloaded_on_cache_hit": False if hit else None}
    return result
