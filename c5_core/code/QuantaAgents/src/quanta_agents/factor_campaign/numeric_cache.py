"""Bounded rank memory maps and authenticated sufficient-statistic records."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import uuid

import numpy as np


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            h.update(chunk)
    return h.hexdigest()


class NumericCache:
    def __init__(self, directory=None, *, max_memory_bytes=256 * 1024**2):
        self.directory = Path(directory) if directory else None
        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=True)
        self.maximum = max_memory_bytes
        self.arrays = OrderedDict()
        self.memory_bytes = 0
        self.hits = self.misses = self.rejected = 0

    def _path(self, kind, key, extension):
        return self.directory / (kind + "_" + digest(key) + extension)

    def _atomic_json(self, path, value):
        temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
            allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)

    def read_record(self, key):
        if self.directory:
            path = self._path("stats", key, ".json")
            if path.exists():
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    if value["key"] != key or value["digest"] != digest(value["payload"]):
                        raise ValueError("statistic integrity mismatch")
                    self.hits += 1
                    return value["payload"]
                except (OSError, ValueError, KeyError, TypeError):
                    self.rejected += 1
        self.misses += 1
        return None

    def write_record(self, key, payload):
        if self.directory:
            self._atomic_json(self._path("stats", key, ".json"),
                {"key": key, "payload": payload, "digest": digest(payload)})

    def rank_array(self, key, shape, compute):
        identifier = digest(key)
        if identifier in self.arrays:
            self.hits += 1
            self.arrays.move_to_end(identifier)
            return self.arrays[identifier]
        array = None
        if self.directory:
            path = self._path("rank", key, ".npy")
            manifest = self._path("rank", key, ".json")
            if path.exists() and manifest.exists():
                try:
                    info = json.loads(manifest.read_text(encoding="utf-8"))
                    if info["key"] != key or info["sha256"] != file_hash(path):
                        raise ValueError("rank integrity mismatch")
                    array = np.load(path, allow_pickle=False, mmap_mode="r")
                    if tuple(array.shape) != tuple(shape) or array.dtype != np.float64:
                        raise ValueError("rank axes/dtype mismatch")
                    self.hits += 1
                except (OSError, ValueError, KeyError, TypeError):
                    array = None
                    self.rejected += 1
            if array is None:
                self.misses += 1
                values = np.asarray(compute(), dtype=np.float64)
                if values.shape != tuple(shape):
                    raise ValueError("rank calculator changed axes")
                temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
                with temporary.open("wb") as stream:
                    np.save(stream, values, allow_pickle=False)
                os.replace(temporary, path)
                self._atomic_json(manifest, {"key": key, "sha256": file_hash(path)})
                array = np.load(path, allow_pickle=False, mmap_mode="r")
        else:
            self.misses += 1
            array = np.asarray(compute(), dtype=np.float64)
            array.setflags(write=False)
        # Bound addressable rank pages too; mapped arrays are not counted as free.
        if array.nbytes <= self.maximum:
            while self.arrays and self.memory_bytes + array.nbytes > self.maximum:
                _, old = self.arrays.popitem(last=False)
                self.memory_bytes -= old.nbytes
            self.arrays[identifier] = array
            self.memory_bytes += array.nbytes
        return array

    @property
    def info(self):
        return {"hits": self.hits, "misses": self.misses, "rejected": self.rejected,
                "retained_rank_bytes": self.memory_bytes, "maximum_rank_bytes": self.maximum}
