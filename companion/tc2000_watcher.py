"""Optional Windows TC2000 companion: watch an export folder and ship complete scan batches.

This utility ONLY reads exported symbol files and POSTs complete 3-file batches to the trading
service over TLS with an API key. It does NOT automate the TC2000 UI and — by construction — has
NO ability to place, cancel, or simulate broker orders: it imports no broker/execution modules and
exposes no order functions. The trading service performs all validation and is the sole order
authority.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Mirrors src/tc2000/importer FILENAME_RE (kept local so the companion has no server deps).
FILENAME_RE = re.compile(r"^strength_(1m|3m|6m)_(\d{4}-\d{2}-\d{2})\.(txt|csv)$")
_TAGS = ("1m", "3m", "6m")


@dataclass
class Batch:
    market_date: str
    filenames: list[str]


def find_complete_batches(filenames: list[str]) -> list[Batch]:
    """Group filenames by market date and return only dates with all three scans present."""
    by_date: dict[str, dict[str, str]] = {}
    for name in filenames:
        m = FILENAME_RE.match(name)
        if not m:
            continue
        tag, date, _ext = m.groups()
        by_date.setdefault(date, {})[tag] = name
    batches = []
    for date in sorted(by_date):
        present = by_date[date]
        if all(t in present for t in _TAGS):
            batches.append(Batch(market_date=date, filenames=[present[t] for t in _TAGS]))
    return batches


def build_upload(files: dict[str, str]) -> dict:
    """Build the JSON payload the trading service ingests: raw text + per-file SHA-256 hashes.

    The service re-validates everything; the companion never decides anything about trading.
    """
    return {
        "files": dict(files),
        "hashes": {name: hashlib.sha256(text.encode("utf-8")).hexdigest()
                   for name, text in files.items()},
    }


def post_batch(url: str, api_key: str, payload: dict, *, sender) -> object:
    """POST the payload via an INJECTED sender(url, headers, json) so transport/creds stay out of
    this module and out of tests. The API key authenticates the companion; it is never logged."""
    if not url or not api_key:
        raise ValueError("url and api_key are required")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    return sender(url, headers, payload)
