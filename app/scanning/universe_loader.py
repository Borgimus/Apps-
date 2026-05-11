"""Load and filter the ticker universe from config/ticker_universe.yaml."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Set

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).parents[2] / "config" / "ticker_universe.yaml"


class UniverseLoader:
    """
    Loads symbols from the universe YAML file, applies the blacklist,
    and honours max_symbols_per_scan.
    """

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else _DEFAULT_PATH
        self._data: dict = {}
        self._symbols: List[str] = []
        self._blacklist: Set[str] = set()

    def load(self) -> "UniverseLoader":
        if not self._path.exists():
            logger.warning("Universe file not found: %s — returning empty universe", self._path)
            return self
        with self._path.open() as fh:
            self._data = yaml.safe_load(fh) or {}
        self._symbols = [str(s).upper() for s in (self._data.get("symbols") or [])]
        self._blacklist = {str(s).upper() for s in (self._data.get("blacklist") or [])}
        logger.info(
            "UniverseLoader: loaded %d symbols from %s (blacklist=%d)",
            len(self._symbols), self._path, len(self._blacklist),
        )
        return self

    def get_symbols(
        self,
        max_symbols: Optional[int] = None,
        extra_blacklist: Optional[List[str]] = None,
    ) -> List[str]:
        """Return filtered, capped symbol list."""
        bl = self._blacklist | {s.upper() for s in (extra_blacklist or [])}
        filtered = [s for s in self._symbols if s not in bl]
        if max_symbols is not None:
            filtered = filtered[:max_symbols]
        return filtered

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
