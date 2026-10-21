"""Explicit reuse of the immutable v18 kernel without replacing old modules."""
import importlib
from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[3]
FROZEN = ROOT / "experiment_traces/meta_ashare_revision18/src/quanta_agents"
NAME = "_quanta_v3_frozen_v18"


def module(name):
    if NAME not in sys.modules:
        package = types.ModuleType(NAME)
        package.__path__ = [str(FROZEN)]
        sys.modules[NAME] = package
        meta = types.ModuleType(NAME + ".meta")
        meta.__path__ = [str(FROZEN / "meta")]
        sys.modules[NAME + ".meta"] = meta
    return importlib.import_module(NAME + "." + name)
