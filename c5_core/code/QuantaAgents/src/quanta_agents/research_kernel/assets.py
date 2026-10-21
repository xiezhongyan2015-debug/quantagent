"""Persistent factor definitions and verified, panel-scoped numeric caches.

Definitions are not evidence of profitability. Catalogue imports never promote
unverified formula text to executable code. Scores are raw (direction is not
applied), and cached values cannot authorize a different data scope.
"""
from __future__ import annotations

import ast
from contextlib import closing
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterable
from uuid import uuid4

import numpy as np
import pandas as pd
import pyarrow

from quanta_agents.meta import factor_algebra
from quanta_agents.meta_v6 import factors
from quanta_agents.meta_v6.data import MarketPanel
from quanta_agents.meta_v6.factors import FactorEngine, FactorSpec, canonical_expression

VERSION = "research_kernel_assets_v1"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ROLES = {"return", "risk", "condition", "interaction"}
_METADATA_COLUMNS = {
    "id", "record_id", "factor_id", "factor_key", "year", "date", "name",
    "factor_name", "formula_key", "formula_text", "expression", "category",
    "mode", "implementation_kind", "implementation_ref", "signal_frequency",
    "frequency", "note", "description", "mechanism", "因子名称", "因子代码", "公式",
}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _io(path: Path) -> Path:
    """Windows native extended paths for I/O; identities keep ordinary paths.

    Full content digests are preserved. This is not truncation or a collision-
    prone short-name cache: nested study roots routinely exceed MAX_PATH once
    both a semantic digest and payload digest are appended.
    """
    path = Path(path)
    if os.name != "nt":
        return path
    value = str(path.absolute())
    if value.startswith("\\\\?\\"):
        return path
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with _io(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


_CALCULATOR_PATHS = {"resolver": Path(__file__), "factor_engine": Path(factors.__file__),
                     "factor_algebra": Path(factor_algebra.__file__)}
_LOADED_SOURCE = {key: _sha(path) for key, path in _CALCULATOR_PATHS.items()}


def _calculator_identity() -> dict:
    current = {key: _sha(path) for key, path in _CALCULATOR_PATHS.items()}
    if current != _LOADED_SOURCE:
        raise ValueError("calculator source changed after import; start a fresh worker")
    return {"version": VERSION, "language": factors.LANGUAGE_VERSION,
            "source_sha256": current, "numpy": np.__version__,
            "pandas": pd.__version__, "pyarrow": pyarrow.__version__}


def _fields(expression: str) -> list[str]:
    tree = ast.parse(expression, mode="eval")
    function_nodes = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    return sorted({node.id for node in ast.walk(tree)
                   if isinstance(node, ast.Name) and id(node) not in function_nodes})


def _read_json(path: Path) -> dict:
    value = json.loads(_io(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with _io(temporary).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(_io(temporary), _io(path))
    finally:
        _io(temporary).unlink(missing_ok=True)


def _proof(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": _sha(path)}


class AssetRegistry:
    """Small immutable definitions in SQLite; derived scores in a private cache.

    ``executable`` means validated V6 syntax, conditional on required fields.
    ``catalogued`` means metadata only. ``unavailable`` preserves a known operator
    gap. All resolve failures are explicit and local; callers must reject a
    strategy if any required factor is absent from the returned score mapping.
    """

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self._owned(self.root / "score_cache")
        self.cache_dir.mkdir(exist_ok=True)
        self.db_path = self._owned(self.root / "assets.sqlite")
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS assets (
                asset_id TEXT PRIMARY KEY, definition TEXT NOT NULL,
                definition_sha256 TEXT NOT NULL, search_text TEXT NOT NULL)""")

    def _owned(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("asset storage path escapes registry root")
        return resolved

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._owned(self.db_path), timeout=15)

    @staticmethod
    def _definition(asset: dict) -> dict:
        if not isinstance(asset, dict) or set(asset) - {
                "id", "expression", "name", "roles", "source", "metadata"}:
            raise ValueError("asset must contain only the declared definition fields")
        identity = asset.get("id")
        if not isinstance(identity, str) or not _ID.fullmatch(identity):
            raise ValueError("asset id must be a short reference, never a path")
        name = asset.get("name", identity)
        if not isinstance(name, str) or not name.strip() or len(name) > 256:
            raise ValueError("asset name must be nonempty text up to 256 characters")
        roles = asset.get("roles", ["return"])
        if not isinstance(roles, list) or not roles or any(role not in _ROLES for role in roles):
            raise ValueError("roles must use return/risk/condition/interaction")
        source, metadata = asset.get("source", {}), asset.get("metadata", {})
        if not isinstance(source, dict) or not isinstance(metadata, dict):
            raise ValueError("source and metadata must be JSON objects")
        if len(_json({"source": source, "metadata": metadata}).encode("utf-8")) > 65536:
            raise ValueError("definition metadata must be a summary, not complete history")
        expression = canonical_expression(asset.get("expression"))
        return json.loads(_json({"id": identity, "name": name, "expression": expression,
            "roles": list(dict.fromkeys(roles)), "source": source, "metadata": metadata,
            "status": "executable", "executable": True,
            "required_fields": _fields(expression),
            "formula_id": FactorSpec(identity, expression).factor_id,
            "validation": "syntax_validated; panel_fields_checked_at_resolve",
            "profitability_claim": False, "version": VERSION}))

    def _store(self, records: list[dict]) -> list[dict]:
        saved = []
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for record in records:
                old = db.execute("SELECT definition,definition_sha256 FROM assets WHERE asset_id=?",
                                 (record["id"],)).fetchone()
                if old:
                    prior = self._decode(old)
                    if (prior["expression"] != record["expression"] or
                            prior["formula_id"] != record["formula_id"] or
                            prior["status"] != record["status"]):
                        raise ValueError(f"asset id conflict: {record['id']}; register a new id")
                    # First definition is retained; re-registration cannot rewrite provenance.
                    saved.append(prior)
                    continue
                payload = _json(record)
                search = " ".join((record["id"], record["name"], record["expression"] or "",
                                   _json(record["metadata"]), _json(record["source"])))
                db.execute("INSERT INTO assets VALUES (?,?,?,?)",
                           (record["id"], payload, hashlib.sha256(payload.encode()).hexdigest(), search))
                saved.append(json.loads(payload))
        return saved

    @staticmethod
    def _decode(row) -> dict:
        if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
            raise ValueError("asset definition integrity failure")
        return json.loads(row[0])

    def register(self, asset: dict) -> dict:
        return self._store([self._definition(asset)])[0]

    def get(self, asset_id: str) -> dict:
        with closing(self._connect()) as db:
            row = db.execute("SELECT definition,definition_sha256 FROM assets WHERE asset_id=?",
                             (asset_id,)).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return self._decode(row)

    def list(self, query: str = "", limit: int = 50, offset: int = 0) -> list[dict]:
        if (not isinstance(query, str) or type(limit) is not int or not 1 <= limit <= 1000
                or type(offset) is not int or offset < 0):
            raise ValueError("query must be text, limit 1..1000 and offset nonnegative")
        with closing(self._connect()) as db:
            rows = db.execute("""SELECT definition,definition_sha256 FROM assets
                WHERE instr(lower(search_text),lower(?))>0 ORDER BY asset_id LIMIT ? OFFSET ?""",
                              (query, limit, offset)).fetchall()
        return [self._decode(row) for row in rows]

    def _cache_read(self, identity: dict, reference: pd.DataFrame) -> tuple[Any, str | None]:
        key = _digest(identity)
        folder = self._owned(self.cache_dir / key)
        manifest_path = self._owned(folder / "manifest.json")
        if not _io(manifest_path).exists():
            return None, None
        try:
            manifest = _read_json(manifest_path)
            if manifest.get("identity") != identity or manifest.get("key") != key:
                raise ValueError("cache identity mismatch")
            payload_hash = manifest.get("payload_sha256", "")
            if not isinstance(payload_hash, str) or not _HEX.fullmatch(payload_hash):
                raise ValueError("invalid cache payload digest")
            # Never use a manifest-supplied path or deserialize executable objects.
            if set(manifest) != {"key", "identity", "payload_sha256", "shape", "axis_names"}:
                raise ValueError("unexpected cache manifest fields")
            payload = self._owned(folder / f"scores.{payload_hash}.parquet")
            if _sha(payload) != payload_hash:
                raise ValueError("cache payload hash mismatch")
            frame = pd.read_parquet(_io(payload))
            if _sha(payload) != payload_hash:
                raise ValueError("cache changed during read")
            if (not frame.index.equals(reference.index) or not frame.columns.equals(reference.columns)
                    or frame.index.names != reference.index.names or frame.columns.names != reference.columns.names
                    or manifest["shape"] != list(reference.shape)
                    or manifest["axis_names"] != [list(reference.index.names), list(reference.columns.names)]
                    or any(str(dtype) != "float64" for dtype in frame.dtypes)
                    or np.isinf(frame.to_numpy()).any()):
                raise ValueError("cache axis, dtype or numeric integrity mismatch")
            frame.index, frame.columns = reference.index.copy(), reference.columns.copy()
            frame.attrs = {}
            return frame, None
        except (OSError, ValueError, KeyError, TypeError, pyarrow.ArrowException) as exc:
            return None, f"{type(exc).__name__}: {exc}"

    def _cache_write(self, identity: dict, frame: pd.DataFrame) -> None:
        key = _digest(identity)
        folder = self._owned(self.cache_dir / key)
        _io(folder).mkdir(exist_ok=True)
        temporary = self._owned(folder / f".{uuid4().hex}.parquet")
        try:
            export = frame.copy(deep=False)
            export.attrs = {}
            export.to_parquet(_io(temporary), index=True)
            with _io(temporary).open("r+b") as stream:
                os.fsync(stream.fileno())
            payload_hash = _sha(temporary)
            payload = self._owned(folder / f"scores.{payload_hash}.parquet")
            os.replace(_io(temporary), _io(payload))
            # Content-addressed payload first, manifest last. Concurrent readers see
            # a complete generation; interrupted writes cannot bless a partial file.
            _atomic_json(self._owned(folder / "manifest.json"), {
                "key": key, "identity": identity, "payload_sha256": payload_hash,
                "shape": list(frame.shape),
                "axis_names": [list(frame.index.names), list(frame.columns.names)]})
        finally:
            _io(temporary).unlink(missing_ok=True)

    def resolve(self, ids: Iterable[str], panel: MarketPanel) -> tuple[dict[str, pd.DataFrame], dict]:
        started = time.perf_counter()
        if isinstance(ids, str):
            raise ValueError("ids must be an iterable of asset references, not one string")
        requested = list(ids)
        if any(not isinstance(value, str) for value in requested):
            raise ValueError("all asset references must be strings")
        unique = list(dict.fromkeys(requested))
        calculator = _calculator_identity()
        # Use the engine's defensive snapshot and content hash, never a caller's
        # externally supplied fingerprint. One engine shares all subexpressions.
        engine = FactorEngine(panel)
        reference = engine._eligible
        metrics = {"version": VERSION, "requested": len(requested), "unique_ids": len(unique),
            "computed": 0, "cache_hits": 0, "cache_misses": 0, "alias_reuses": 0,
            "unavailable": {}, "assets": {}, "corruptions": [],
            "panel_fingerprint": engine.data_fingerprint, "calculator": calculator,
            "direction_applied": False}
        scores, shared = {}, {}
        for asset_id in unique:
            try:
                asset = self.get(asset_id)
            except KeyError:
                metrics["unavailable"][asset_id] = {"reason": "unknown_asset", "status": "unavailable"}
                continue
            if not asset["executable"]:
                metrics["unavailable"][asset_id] = {"reason": asset.get("unavailable_reason", "metadata_only"),
                    "status": asset["status"]}
                continue
            missing = sorted(set(asset["required_fields"]) - engine.feature_names)
            if missing:
                metrics["unavailable"][asset_id] = {"reason": "missing_or_noncausal_fields",
                    "missing_fields": missing, "status": "unavailable"}
                continue
            identity = {"expression": asset["expression"], "calculator": calculator,
                        "panel_fingerprint": engine.data_fingerprint}
            key = _digest(identity)
            if key in shared:
                frame, first_id = shared[key]
                metrics["alias_reuses"] += 1
                metrics["assets"][asset_id] = {"status": "resolved", "cache_key": key, "alias_of": first_id}
                scores[asset_id] = frame.copy(deep=True)
                continue
            frame, corrupt = self._cache_read(identity, reference)
            if corrupt:
                metrics["corruptions"].append({"key": key, "reason": corrupt})
            hit = frame is not None
            if hit:
                metrics["cache_hits"] += 1
            else:
                metrics["cache_misses"] += 1
                try:
                    frame = engine.compute(FactorSpec(asset_id, asset["expression"]))
                except (ValueError, KeyError, ArithmeticError) as exc:
                    metrics["unavailable"][asset_id] = {"reason": "compute_failed",
                        "detail": f"{type(exc).__name__}: {exc}", "status": "unavailable"}
                    continue
                self._cache_write(identity, frame)
                metrics["computed"] += 1
            shared[key] = frame, asset_id
            scores[asset_id] = frame.copy(deep=True)
            metrics["assets"][asset_id] = {"status": "resolved", "cache_key": key, "cache_hit": hit}
        metrics.update(resolved=len(scores), complete=not metrics["unavailable"],
                       engine_cache=engine.cache_info, duration_seconds=time.perf_counter() - started)
        return scores, metrics

    def import_metadata(self, path: Path, source_name: str) -> dict:
        """Import CSV/TSV catalogue columns only, never IC/return/price columns.

        No expression, implementation reference or historical completion flag is
        treated as proof of support in this engine. Explicit register is required
        for a separate validated executable reference.
        """
        path = Path(path).resolve()
        if path.suffix.lower() not in {".csv", ".tsv"}:
            raise ValueError("metadata import currently supports CSV/TSV only")
        if not isinstance(source_name, str) or not source_name.strip():
            raise ValueError("source_name must be nonempty")
        separator = "\t" if path.suffix.lower() == ".tsv" else ","
        proof = _proof(path)
        with path.open(encoding="utf-8-sig", newline="") as stream:
            header = next(csv.reader(stream, delimiter=separator))
        selected = [column for column in header if column in _METADATA_COLUMNS]
        if not selected:
            raise ValueError("no recognized catalogue metadata columns")
        table = pd.read_csv(path, sep=separator, encoding="utf-8-sig", usecols=selected,
                            dtype=str, keep_default_na=False)
        if _sha(path) != proof["sha256"]:
            raise ValueError("metadata source changed during import")
        prefix = "catalog." + _digest({"source_name": source_name, "path": str(path)})[:12]
        records = []
        for index, row in enumerate(table.to_dict(orient="records")):
            name = next((row[key] for key in ("name", "factor_name", "因子名称", "formula_key", "factor_id")
                         if row.get(key)), f"catalogue row {index + 1}")
            row_id = next((row[key] for key in ("record_id", "id", "factor_id", "factor_key", "因子代码")
                           if row.get(key)), str(index + 1))
            records.append({"id": prefix + "." + _digest({"row_id": row_id, "row": index + 1})[:16],
                "name": name, "expression": None, "roles": [], "source": {**proof,
                    "source_name": source_name, "row_1based": index + 2},
                "metadata": row, "status": "catalogued", "executable": False,
                "required_fields": [], "formula_id": _digest(row),
                "validation": "metadata_only; formula_and_implementation_not_verified",
                "unavailable_reason": "metadata_only_not_registered_as_executable",
                "profitability_claim": False, "version": VERSION})
        saved = self._store(records)
        return {"status": "completed", "source": proof, "source_name": source_name,
                "imported": len(saved), "catalogued": len(saved), "executable": 0,
                "selected_columns": selected, "numeric_performance_columns_read": [],
                "ids": [record["id"] for record in saved]}

    def import_v6(self, study_root: Path) -> dict:
        """Verify declaration/spec/registry metadata for the original 9 + new 3.

        Does not load scores, IC, account results or market values. Historical
        score manifests remain provenance only; new caches require this engine's
        panel and calculator identity. Registry checks cover selected immutable
        factor/snapshot events, not an audit of every historical evidence event.
        """
        root = Path(study_root).resolve()
        folders = [root, root / "cycles" / "03_factor_information_expansion"]
        records, proofs = [], []
        for folder in folders:
            index_path, declaration_path = folder / "factor_index.json", folder / "factor_declaration.json"
            index_proof, declaration_proof = _proof(index_path), _proof(declaration_path)
            index, declaration = _read_json(index_path), _read_json(declaration_path)
            if index.get("status") != "completed":
                raise ValueError("V6 factor stage is not completed")
            if index.get("factor_declaration_sha256") != declaration_proof["sha256"]:
                raise ValueError("V6 declaration hash mismatch")
            library = Path(index["library_path"]).resolve()
            with closing(sqlite3.connect(library.as_uri() + "?mode=ro", uri=True)) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                snapshot = _v6_event(db, index["library_snapshot_id"], "snapshot")
                payload = snapshot["payload"]
                if _digest({k: v for k, v in payload.items() if k != "snapshot_hash"}) != payload["snapshot_hash"]:
                    raise ValueError("V6 snapshot hash mismatch")
                for entry in index["factors"]:
                    candidates = [a for a in entry.get("artifacts", []) if Path(a["path"]).name == "spec.json"]
                    if len(candidates) != 1:
                        raise ValueError("V6 factor requires one pinned spec")
                    pin = candidates[0]
                    spec_path = Path(pin["path"]).resolve()
                    if not spec_path.is_relative_to(folder) or _sha(spec_path) != pin["sha256"]:
                        raise ValueError("V6 spec path or hash mismatch")
                    document = _read_json(spec_path)
                    raw = document["spec"]
                    spec = FactorSpec(**{k: raw[k] for k in ("name", "expression", "version", "parents", "metadata") if k in raw})
                    if spec.factor_id != entry["factor_id"] or document["factor_key"] != entry["factor_key"]:
                        raise ValueError("V6 spec identity mismatch")
                    if spec.factor_id not in payload["factor_ids"]:
                        raise ValueError("V6 factor absent from frozen registry snapshot")
                    matching = []
                    for event_id in payload["factor_event_ids"]:
                        event = _v6_event(db, event_id, "factor")
                        if event["factor_id"] == spec.factor_id and event["payload"].get("spec_id") == spec.spec_id:
                            if _json(event["payload"]["spec"]) != _json(spec.to_dict()) or event["seq"] > payload["through_seq"]:
                                raise ValueError("V6 registry spec binding mismatch")
                            matching.append(event)
                    if not matching:
                        raise ValueError("V6 registered specification not found")
                    original = document["original"]
                    declared = _v6_declared(declaration, entry["factor_key"], original)
                    if (canonical_expression(declared["expression"]) != spec.expression
                            or declared.get("direction") != entry.get("direction")):
                        raise ValueError("V6 declaration expression or direction mismatch")
                    role = entry.get("role", raw.get("metadata", {}).get("role", "return_prediction"))
                    roles = {"risk_information": ["risk"], "conditional_information": ["condition"],
                             "conditional_gate": ["condition"],
                             "interaction": ["interaction"]}.get(role, ["return"])
                    record = self._definition({"id": entry["factor_key"], "name": spec.name,
                        "expression": spec.expression, "roles": roles,
                        "source": {"kind": "v6_verified_definition_import", "index": index_proof,
                            "declaration": declaration_proof, "spec": {"path": str(spec_path), "sha256": pin["sha256"]},
                            "registry_path": str(library), "registry_event_id": matching[0]["event_id"],
                            "registry_event_hash": matching[0]["event_hash"],
                            "registry_snapshot_id": snapshot["event_id"],
                            "registry_snapshot_hash": payload["snapshot_hash"]},
                        "metadata": {"original_direction": entry.get("direction"),
                            "primary_horizon": entry.get("primary_horizon"), "original_role": role,
                            "original_factor_id": spec.factor_id, "prior_status": entry.get("status"),
                            "prior_results_exposed": True, "score_values_imported": False,
                            "hypothesis": raw.get("metadata", {}).get("hypothesis", "")}})
                    if "hf0280_triple_ols20_v1" in record["required_fields"]:
                        record.update(status="unavailable", executable=False,
                            unavailable_reason="requires_registered_three_daily_cross_sectional_OLS_operator_and_admitted_auxiliary_source; no_approximation",
                            validation="original_definition_verified; special_operator_not_supported_by_generic_engine")
                    records.append(record)
                    proofs.append({"path": str(spec_path), "sha256": pin["sha256"]})
            proofs.extend([index_proof, declaration_proof])
        # Recheck metadata before a single atomic registry transaction.
        for proof in proofs:
            if _sha(Path(proof["path"])) != proof["sha256"]:
                raise ValueError("V6 source metadata changed during import")
        if len(records) != 12 or len({r["id"] for r in records}) != 12:
            raise ValueError("expected the original 9 plus expansion 3 distinct V6 definitions")
        saved = self._store(records)
        return {"status": "completed", "imported": len(saved),
                "executable": sum(r["executable"] for r in saved),
                "unavailable": {r["id"]: r["unavailable_reason"] for r in saved if not r["executable"]},
                "ids": [r["id"] for r in saved], "source_proofs": proofs,
                "scores_imported": 0, "market_or_performance_values_read": False,
                "registry_check": "selected_factor_and_snapshot_event_hashes; not_full_history_audit"}


def _v6_event(db: sqlite3.Connection, event_id: str, kind: str) -> dict:
    row = db.execute("SELECT * FROM factor_library_events WHERE event_id=? AND kind=?",
                     (event_id, kind)).fetchone()
    if row is None:
        raise ValueError("V6 registry event missing")
    event = dict(row)
    event["payload"] = json.loads(event["payload"])
    keys = ("event_id", "kind", "factor_id", "created_utc", "payload", "previous_hash")
    if _digest({key: event[key] for key in keys}) != event["event_hash"]:
        raise ValueError("V6 registry event integrity failure")
    return event


def _v6_declared(declaration: dict, key: str, original: dict) -> dict:
    if "declarations" in declaration:
        rows = [row["original"] for row in declaration["declarations"] if row["factor_key"] == key]
    else:
        rows = [row for row in declaration.get("fixed_seeds", []) + declaration.get("model_factors", [])
                if row.get("name") == original.get("name")]
    if len(rows) != 1 or rows[0] != original:
        raise ValueError("V6 original declaration binding mismatch")
    return rows[0]
