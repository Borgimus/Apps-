"""Load and filter the ticker universe from config/ticker_universe.yaml.

Supports two modes:
  manual:  flat `symbols:` list (backward compatible)
  grouped: load symbols from named `groups:` with per-group and total caps

In grouped mode, symbols that appear in multiple groups are deduplicated
(first-listed group wins). Blacklist is applied across all groups.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).parents[2] / "config" / "ticker_universe.yaml"


class UniverseLoader:
    """
    Loads symbols from the universe YAML file, applies blacklist,
    and enforces group/total caps.

    Backward compatible: if the YAML uses the old flat `symbols:` key and
    mode=manual, get_symbols() behaves exactly as before.
    """

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else _DEFAULT_PATH
        self._data: dict = {}
        self._symbols: List[str] = []            # flat list (manual mode)
        self._groups: Dict[str, List[str]] = {}  # group_name → [symbol, ...]
        self._blacklist: Set[str] = set()

    # ── Load ─────────────────────────────────────────────────────────────────

    def load(self) -> "UniverseLoader":
        if not self._path.exists():
            logger.warning(
                "Universe file not found: %s — returning empty universe", self._path
            )
            return self
        with self._path.open() as fh:
            self._data = yaml.safe_load(fh) or {}

        self._blacklist = {
            str(s).upper() for s in (self._data.get("blacklist") or [])
        }

        # Load groups (present in grouped mode)
        raw_groups = self._data.get("groups") or {}
        self._groups = {
            grp: [str(s).upper() for s in (syms or [])]
            for grp, syms in raw_groups.items()
            if syms  # skip empty groups (e.g. watchlist_experimental: [])
        }

        # Load flat symbols list (manual mode / fallback)
        self._symbols = [
            str(s).upper() for s in (self._data.get("symbols") or [])
        ]

        logger.info(
            "UniverseLoader: loaded %d groups (%d flat symbols) from %s (blacklist=%d)",
            len(self._groups), len(self._symbols), self._path, len(self._blacklist),
        )
        return self

    # ── Public API ────────────────────────────────────────────────────────────

    def get_symbols(
        self,
        max_symbols: Optional[int] = None,
        extra_blacklist: Optional[List[str]] = None,
        enabled_groups: Optional[List[str]] = None,
        max_per_group: int = 15,
        max_total: int = 40,
    ) -> List[str]:
        """Return filtered, capped symbol list (preserves backward compat)."""
        return list(
            self.get_symbols_with_groups(
                enabled_groups=enabled_groups,
                max_per_group=max_per_group,
                max_total=max_total,
                max_symbols=max_symbols,
                extra_blacklist=extra_blacklist,
            ).keys()
        )

    def get_symbols_with_groups(
        self,
        enabled_groups: Optional[List[str]] = None,
        max_per_group: int = 15,
        max_total: int = 40,
        max_symbols: Optional[int] = None,
        extra_blacklist: Optional[List[str]] = None,
    ) -> "OrderedDict[str, str]":
        """
        Return an OrderedDict of {symbol: group_name}, deduplicated.

        In grouped mode: iterates enabled_groups in order; each symbol is
        assigned to the first group that contains it.
        In manual mode: all symbols assigned group_name "manual".

        Respects blacklist, max_per_group, max_total, and max_symbols caps.
        """
        bl = self._blacklist | {s.upper() for s in (extra_blacklist or [])}
        result: "OrderedDict[str, str]" = OrderedDict()

        if self.mode == "grouped" and self._groups:
            # Determine which groups to use
            if enabled_groups is None:
                yaml_enabled = self._data.get("enabled_groups") or list(self._groups.keys())
                enabled_groups = [str(g) for g in yaml_enabled]

            yaml_max_per = int(self._data.get("max_per_group") or max_per_group)
            yaml_max_total = int(self._data.get("max_total_symbols") or max_total)

            for grp in enabled_groups:
                if grp not in self._groups:
                    logger.warning("UniverseLoader: group %r not found in YAML", grp)
                    continue
                grp_syms = self._groups[grp]
                added_this_group = 0
                for sym in grp_syms:
                    if sym in bl or sym in result:
                        continue
                    if added_this_group >= yaml_max_per:
                        break
                    if len(result) >= yaml_max_total:
                        break
                    result[sym] = grp
                    added_this_group += 1
                logger.debug(
                    "UniverseLoader: group=%s added %d symbols", grp, added_this_group
                )
                if len(result) >= yaml_max_total:
                    break
        else:
            # manual / off / fallback: use flat symbols list
            for sym in self._symbols:
                if sym not in bl:
                    result[sym] = "manual"

        # Final optional cap (e.g. max_symbols_per_scan from settings)
        if max_symbols is not None and len(result) > max_symbols:
            result = OrderedDict(list(result.items())[:max_symbols])

        logger.info(
            "UniverseLoader: %d symbols from %d group(s) (blacklist=%d)",
            len(result),
            len({v for v in result.values()}),
            len(bl),
        )
        return result

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._data.get("mode", "manual")

    @property
    def scan_config(self) -> dict:
        return dict(self._data.get("scan_config") or {})

    @property
    def all_symbols(self) -> List[str]:
        return list(self._symbols)

    @property
    def blacklist(self) -> Set[str]:
        return set(self._blacklist)

    @property
    def available_groups(self) -> List[str]:
        return list(self._groups.keys())

    @property
    def enabled_groups_from_yaml(self) -> List[str]:
        return [str(g) for g in (self._data.get("enabled_groups") or [])]
