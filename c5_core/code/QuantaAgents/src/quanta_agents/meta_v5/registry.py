"""Controller-only immutable development inputs. No model-supplied NAV or paths."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time

from quanta_agents.meta_v3.ledger import digest, need, serial
from quanta_agents.meta_v3.research_tools import save_once
from .analytics import evaluate_bundle

VERSION = "v5_development_catalog_v1"


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for data in iter(lambda: f.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


def safe_id(value):
    need(type(value) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value), "invalid candidate identifier")
    return value


def admitted_bundle(bundle):
    need(bundle.get("split") == "development", "confirmation/final data cannot enter V5 reflection")
    p = bundle.get("provenance", {})
    need(p.get("exposed") is True, "development exposure must be explicit")
    need(p.get("source_class") in ("saved_development", "generated_engineering"), "unadmitted source class")
    # This importer cannot attest executable economics from a caller's boolean.
    # Future certified releases remain a separately audited V4 custody adapter.
    need(p.get("execution_certified") is False, "imported assertions cannot certify execution")
    need(type(p.get("accounting_mode")) is str and p["accounting_mode"], "accounting identity required")
    need(type(p.get("source_hashes")) is dict and 1 <= len(p["source_hashes"]) <= 2048, "nonempty bounded source file bindings required")
    for path, pin in p["source_hashes"].items():
        need(type(pin) is str and re.fullmatch(r"[a-f0-9]{64}", pin), "source digest invalid")
        need(file_hash(path) == pin, "registered development source changed")
    safe_id(bundle["candidate_id"])


def prepare_catalog(bundles, policy):
    """Validate and compute before creating any stage or calling a model."""
    start, cpu = time.perf_counter(), time.process_time()
    need(type(bundles) is list and len(bundles) <= 32, "bounded controller catalog required")
    entries = []
    seen, scopes = set(), {}
    for bundle in bundles:
        admitted_bundle(bundle)
        need(bundle["candidate_id"] not in seen, "duplicate catalog candidate")
        seen.add(bundle["candidate_id"])
        profile = evaluate_bundle(bundle, policy)
        scope = bundle["scope_id"]
        need(scope not in scopes or scopes[scope] == profile["comparison_identity"],
             "one scope cannot change units/calendar/benchmark/capital/cost/source identity")
        scopes[scope] = profile["comparison_identity"]
        entries.append({"candidate_id": bundle["candidate_id"], "bundle": bundle, "profile": profile})
    return {"version": VERSION, "policy": policy, "policy_hash": digest(policy), "entries": entries,
            "preparation_measurement": {"wall_ms": (time.perf_counter()-start)*1000,
                                        "cpu_ms": (time.process_time()-cpu)*1000,
                                        "billing_amount": None, "independent_financial_validation": False}}


def catalog_identity(prepared):
    return {"version": VERSION, "policy_hash": prepared["policy_hash"],
            "entries": [{"candidate_id": e["candidate_id"], "bundle_hash": digest(e["bundle"]),
                         "profile_hash": e["profile"]["profile_hash"]} for e in prepared["entries"]]}


def exposure_record(bundles):
    return {"split": "development", "exposed": True,
        "sources": [b["provenance"]["source_hashes"] for b in bundles],
        "scope_ids": [b["scope_id"] for b in bundles],
        "rename_does_not_restore_holdout": True, "formal_release_authority": False}


def write_catalog(root, prepared):
    folder = Path(root) / "v5_catalog"
    folder.mkdir(exist_ok=False)
    manifest = catalog_identity(prepared)
    for entry in prepared["entries"]:
        name = safe_id(entry["candidate_id"])
        save_once(folder / (name + ".bundle.json"), entry["bundle"])
        save_once(folder / (name + ".profile.json"), entry["profile"])
    save_once(folder / "policy.json", prepared["policy"])
    save_once(folder / "manifest.json", manifest)
    save_once(folder / "preparation_measurement.json", prepared["preparation_measurement"])
    # This is an exposure record, never an authorization for future held-out use.
    save_once(folder / "exposure.json", exposure_record([e["bundle"] for e in prepared["entries"]]))
    return manifest


class Catalog:
    def __init__(self, root, identity):
        self.folder = Path(root) / "v5_catalog"
        self.identity = identity

    def _read(self, name):
        return json.loads((self.folder / name).read_text(encoding="utf-8"))

    def verify(self):
        need(self._read("manifest.json") == self.identity, "V5 frozen catalog manifest drift")
        need(digest(self._read("policy.json")) == self.identity["policy_hash"], "V5 frozen policy drift")
        bundles = []
        for entry in self.identity["entries"]:
            bundle = self._read(entry["candidate_id"] + ".bundle.json")
            bundles.append(bundle)
            profile = self._read(entry["candidate_id"] + ".profile.json")
            need(digest(bundle) == entry["bundle_hash"], "V5 controller bundle drift")
            need(profile["profile_hash"] == entry["profile_hash"] and
                 digest({k:v for k,v in profile.items() if k != "profile_hash"}) == entry["profile_hash"],
                 "V5 profile drift")
            need(profile["bundle_hash"] == entry["bundle_hash"], "V5 profile source binding drift")
            need(profile["policy_hash"] == self.identity["policy_hash"] and
                 profile["candidate_id"] == entry["candidate_id"] and
                 profile["program_hash"] == bundle["program_hash"], "V5 profile identity drift")
            admitted_bundle(bundle)
        need(self._read("exposure.json") == exposure_record(bundles), "V5 exposure evidence changed")
        return True

    @property
    def policy(self):
        value = self._read("policy.json")
        need(digest(value) == self.identity["policy_hash"], "V5 policy changed")
        return value

    def profiles(self):
        self.verify()
        return {e["candidate_id"]: self._read(e["candidate_id"] + ".profile.json")
                for e in self.identity["entries"]}

    def bundle(self, candidate_id):
        safe_id(candidate_id)
        need(any(e["candidate_id"] == candidate_id for e in self.identity["entries"]), "unregistered candidate")
        self.verify()
        return self._read(candidate_id + ".bundle.json")
