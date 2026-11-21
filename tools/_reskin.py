"""Mechanical class bridge so legacy Bootstrap markup picks up the Courtside skin.

Run:  python tools/_reskin.py templates/*.html
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CLASS_MAP = {
    "card": "panel",
    "card-body": "panel-body",
    "card-header": "panel-head",
    "card-footer": "panel-foot",
    "card-title": "panel-title",
    "shadow-sm": None,
    "shadow": None,
    "table-dark": None,
    "border-0": None,
}

CLASS_ATTR = re.compile(r'class="([^"]*)"')


def rewrite_classes(match: re.Match[str]) -> str:
    tokens = match.group(1).split()
    out: list[str] = []
    for token in tokens:
        if token == "text-secondary" and "small" in tokens:
            continue
        if token == "small" and "text-secondary" in tokens:
            continue
        if token in CLASS_MAP:
            replacement = CLASS_MAP[token]
            if replacement:
                out.append(replacement)
            continue
        out.append(token)
    if "text-secondary" in tokens and "small" in tokens:
        out.insert(0, "u-caps")
    deduped: list[str] = []
    for token in out:
        if token not in deduped:
            deduped.append(token)
    return 'class="' + " ".join(deduped) + '"'


def main(paths: list[str]) -> None:
    total = 0
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        before = text
        text = CLASS_ATTR.sub(rewrite_classes, text)
        text = re.sub(r'<h1 class="h3 mb-1"', '<h1 class="page-title mb-1"', text)
        text = re.sub(r'<h1 class="h3"', '<h1 class="page-title"', text)
        text = re.sub(r'<thead class="table-dark">', "<thead>", text)
        if text != before:
            path.write_text(text, encoding="utf-8")
            total += 1
            print(f"reskinned {path.name}")
    print(f"files touched: {total}")


if __name__ == "__main__":
    main(sys.argv[1:])
