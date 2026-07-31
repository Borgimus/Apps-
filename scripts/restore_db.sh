#!/usr/bin/env bash
# Restore the swing PostgreSQL database from a pg_dump custom-format file.
# Usage: SWING_DATABASE_URL=postgresql://... scripts/restore_db.sh path/to/swing_*.dump
# SAFETY: restoring overwrites data. Confirm you are pointed at the intended (paper) database.
set -euo pipefail

DUMP="${1:?usage: restore_db.sh <dumpfile>}"
if [[ -z "${SWING_DATABASE_URL:-}" ]]; then
  echo "SWING_DATABASE_URL is required" >&2
  exit 2
fi
[[ -f "$DUMP" ]] || { echo "dump not found: $DUMP" >&2; exit 2; }

read -r -p "Restore '$DUMP' into the target database? This overwrites data. [y/N] " ans
[[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "aborted"; exit 1; }

pg_restore --clean --if-exists --no-owner --dbname "$SWING_DATABASE_URL" "$DUMP"
echo "restore complete from: $DUMP"
