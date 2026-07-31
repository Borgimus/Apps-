"""TC2000 candidate importer.

Accepts the three EasyScan symbol files as ONE ATOMIC BATCH and validates:
  * all three scans present
  * consistent market date across files (and filename date matches)
  * symbol format, intra-file duplicates, empty lists
  * batch freshness (not stale vs the current market date)
Preserves raw file content hashes (SHA-256) and produces the 3-of-3 / 2-of-3 / union sets.
A stale or partially uploaded batch is REJECTED — never partially applied.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

from src.scanner.strength import agreement_sets, membership

SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
REQUIRED_SCANS = ("one_month", "three_month", "six_month")
FILENAME_RE = re.compile(r"^strength_(1m|3m|6m)_(\d{4}-\d{2}-\d{2})\.(txt|csv)$")
_SCAN_BY_TAG = {"1m": "one_month", "3m": "three_month", "6m": "six_month"}


class ImportError_(ValueError):
    """Batch validation error (kept distinct from builtins.ImportError)."""


@dataclass
class ScanFile:
    scan: str
    filename: str
    raw_text: str
    symbols: list[str] = field(default_factory=list)
    file_hash: str = ""


@dataclass
class ImportBatch:
    market_date: date
    files: dict[str, ScanFile]
    candidate_sets: dict[str, list[str]]
    memberships: dict[str, dict]
    batch_hash: str
    status: str = "ACCEPTED"


def parse_symbols(raw_text: str) -> list[str]:
    """Parse a symbol-only file. Ignores blanks and ``#`` comments; uppercases; dedups (ordered)."""
    seen: set[str] = set()
    out: list[str] = []
    for lineno, line in enumerate(raw_text.splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # A csv line may contain trailing commas; take the first field.
        s = s.split(",")[0].strip().upper()
        if not s:
            continue
        if not SYMBOL_RE.match(s):
            raise ImportError_(f"invalid symbol {s!r} on line {lineno}")
        if s in seen:
            raise ImportError_(f"duplicate symbol {s!r} on line {lineno}")
        seen.add(s)
        out.append(s)
    return out


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_filename(filename: str) -> tuple[str, date]:
    m = FILENAME_RE.match(filename)
    if not m:
        raise ImportError_(
            f"filename {filename!r} does not match strength_(1m|3m|6m)_YYYY-MM-DD.(txt|csv)"
        )
    tag, datestr, _ext = m.groups()
    return _SCAN_BY_TAG[tag], date.fromisoformat(datestr)


def build_batch(
    raw_files: dict[str, str],
    *,
    current_market_date: date,
    max_age_days: int = 1,
) -> ImportBatch:
    """Validate and assemble an atomic batch.

    ``raw_files`` maps filename -> raw text for all three files.
    ``current_market_date`` is the market date the system considers current.
    Raises ImportError_ on any validation failure (batch is never partially applied).
    """
    if len(raw_files) != 3:
        raise ImportError_(f"expected exactly 3 scan files, got {len(raw_files)}")

    parsed: dict[str, ScanFile] = {}
    dates: set[date] = set()
    for filename, text in raw_files.items():
        scan, fdate = _parse_filename(filename)
        if scan in parsed:
            raise ImportError_(f"duplicate scan for {scan!r}")
        symbols = parse_symbols(text)
        if not symbols:
            raise ImportError_(f"empty symbol list in {filename!r}")
        parsed[scan] = ScanFile(scan=scan, filename=filename, raw_text=text,
                                symbols=symbols, file_hash=_hash(text))
        dates.add(fdate)

    missing = [s for s in REQUIRED_SCANS if s not in parsed]
    if missing:
        raise ImportError_(f"missing required scans: {missing}")

    if len(dates) != 1:
        raise ImportError_(f"inconsistent market dates across files: {sorted(dates)}")
    market_date = dates.pop()

    # Freshness: reject stale batches and any future-dated batch.
    age = (current_market_date - market_date).days
    if age > max_age_days:
        raise ImportError_(f"stale batch: market_date {market_date} is {age} days old")
    if age < 0:
        raise ImportError_(f"future-dated batch: market_date {market_date} > {current_market_date}")

    sets = agreement_sets(
        parsed["one_month"].symbols,
        parsed["three_month"].symbols,
        parsed["six_month"].symbols,
    )
    scan_map = {name: sf.symbols for name, sf in parsed.items()}
    all_syms = sorted(set().union(*scan_map.values()))
    memberships = {sym: membership(sym, scan_map) for sym in all_syms}

    # Deterministic batch hash over sorted (filename, file_hash) pairs.
    batch_hash = _hash("|".join(
        f"{sf.filename}:{sf.file_hash}" for sf in sorted(parsed.values(), key=lambda s: s.filename)
    ))

    return ImportBatch(
        market_date=market_date,
        files=parsed,
        candidate_sets=sets,
        memberships=memberships,
        batch_hash=batch_hash,
    )
