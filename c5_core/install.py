# -*- coding: utf-8 -*-
"""Relocate the handover package: rewrite every absolute path in code and small text data files so that it points
inside this directory.  Run once after copying the package to its final place (and again after moving it).

    python install.py            # rewrite paths to the directory this file sits in
    python install.py --check    # only report paths that still point outside the package

The original machine kept its data on several drives (F:/A_Layer_Live, D:/qlib_data, ...).  path_map.json lists each
original prefix and where it lives inside the package; the last root this script wrote is kept in .package_root so a
second run can move from one place to another.  Path separators inside a rewritten path are normalised to "/".
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
TEXT_SUFFIXES = (".py", ".sh", ".json", ".jsonl", ".md", ".txt", ".yaml", ".yml", ".toml", ".ps1", ".bat", ".cfg", ".ini")
MAX_DATA_FILE = 50 * 1024 * 1024      # data/ text files above this size are records, not configuration
PATH_CHARS = r"[^\"'\s,;()`<>|*?，。（）：；、]"


def prefix_pattern(prefix):
    """Regex for an absolute prefix written with any of / \\ \\\\ between its parts, plus the rest of the path."""
    drive, _, rest = prefix.partition(":")
    parts = [re.escape(p) for p in re.split(r"[\\/]+", rest) if p]
    sep = r"(?:/|\\\\|\\)+"
    head = (re.escape(drive) + ":" + sep) if drive and len(drive) == 1 else ""
    body = sep.join(parts) if head else re.escape(prefix.rstrip("/"))
    return re.compile(head + body + r"(?P<tail>(?:(?:/|\\\\|\\)" + PATH_CHARS + r"+)*)")


def build_rules(root):
    path_map = json.load(open(os.path.join(HERE, "path_map.json"), encoding="utf-8"))
    rules = [(old, f"{root}/{new}") for old, new in path_map.items()]
    last = os.path.join(HERE, ".package_root")
    if os.path.exists(last):
        prev = open(last, encoding="utf-8").read().strip()
        if prev and prev != root:
            rules = [(f"{prev}/{new}", f"{root}/{new}") for new in path_map.values()] + rules
    rules.sort(key=lambda r: -len(r[0]))
    return [(prefix_pattern(old), new) for old, new in rules]


def candidate_files():
    for top in ("code", "data", "docs"):
        for dp, dns, fns in os.walk(os.path.join(HERE, top)):
            dns[:] = [d for d in dns if d not in ("__pycache__", ".git")]
            for fn in fns:
                if not fn.endswith(TEXT_SUFFIXES):
                    continue
                p = os.path.join(dp, fn)
                if top != "code" and os.path.getsize(p) > MAX_DATA_FILE:
                    continue
                yield p


def rewrite(text, rules):
    def sub_one(new):
        def repl(m):
            tail = m.group("tail")
            # a string escape right at the end of the literal ("...\n") is not part of the path: keep it as written
            esc = re.search(r"(?:\\\\|\\)[ntr]$", tail)
            keep = tail[esc.start():] if esc else ""
            tail = tail[:esc.start()] if esc else tail
            return new + tail.replace("\\\\", "/").replace("\\", "/") + keep
        return repl
    for pattern, new in rules:
        text = pattern.sub(sub_one(new), text)
    return text


def main():
    check_only = "--check" in sys.argv
    root = HERE
    rules = build_rules(root)
    changed = 0; outside = []
    leftover = re.compile(r"(?<![A-Za-z])[A-Z]:(?:/|\\\\|\\)(?:A_Layer_|qlib_data|TushareData|ChoiceData|大学|A股)")
    for p in candidate_files():
        try:
            text = open(p, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        if check_only:
            if leftover.search(text):
                outside.append(os.path.relpath(p, HERE))
            continue
        new = rewrite(text, rules)
        if new != text:
            # write a new file and swap it in: on the original disk the data files may be hard links to the source
            # machine's files, and writing in place would change those too
            tmp = p + ".relocate.tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                fh.write(new)
            os.replace(tmp, p)
            changed += 1
        if leftover.search(new):
            outside.append(os.path.relpath(p, HERE))
    if not check_only:
        with open(os.path.join(HERE, ".package_root"), "w", encoding="utf-8") as fh:
            fh.write(root)
        print(f"package root: {root}\nfiles rewritten: {changed}")
    print(f"files still mentioning an original drive path: {len(outside)}")
    for p in outside[:40]:
        print("  ", p)


if __name__ == "__main__":
    main()
