#!/usr/bin/env python3
"""Recalculate unified-diff hunk counts in a patch file.

This repairs patch files whose @@ line counts were written incorrectly while
leaving the actual patch content unchanged.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HUNK_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$"
)


def _format_range(start: int, count: int) -> str:
    return str(start) if count == 1 else f"{start},{count}"


def recount(path: Path) -> int:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    output = list(lines)
    repaired = 0
    index = 0

    while index < len(lines):
        match = HUNK_RE.match(lines[index].rstrip("\n"))
        if match is None:
            index += 1
            continue

        end = index + 1
        old_count = 0
        new_count = 0
        while end < len(lines):
            line = lines[end]
            if line.startswith("@@ ") or line.startswith("diff --git "):
                break
            if line.startswith("\\ No newline at end of file"):
                end += 1
                continue
            if line.startswith(" "):
                old_count += 1
                new_count += 1
            elif line.startswith("-"):
                old_count += 1
            elif line.startswith("+"):
                new_count += 1
            else:
                raise ValueError(
                    f"Unexpected line inside hunk at {end + 1}: {line!r}"
                )
            end += 1

        old_start = int(match.group(1))
        new_start = int(match.group(3))
        suffix = match.group(5)
        newline = "\n" if lines[index].endswith("\n") else ""
        corrected = (
            f"@@ -{_format_range(old_start, old_count)} "
            f"+{_format_range(new_start, new_count)} @@{suffix}{newline}"
        )
        if corrected != lines[index]:
            output[index] = corrected
            repaired += 1
        index = end

    path.write_text("".join(output), encoding="utf-8")
    return repaired


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} PATCH_FILE", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"patch file not found: {path}", file=sys.stderr)
        return 2

    repaired = recount(path)
    print(f"recounted_hunks={repaired}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
