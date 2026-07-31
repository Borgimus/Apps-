#!/usr/bin/env bash
# Back up the swing PostgreSQL database. Credentials come from SWING_DATABASE_URL (never logged).
# Usage: SWING_DATABASE_URL=postgresql://... scripts/backup_db.sh [output_dir]
set -euo pipefail

OUT_DIR="${1:-./backups}"
mkdir -p "$OUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$OUT_DIR/swing_${STAMP}.dump"

if [[ -z "${SWING_DATABASE_URL:-}" ]]; then
  echo "SWING_DATABASE_URL is required" >&2
  exit 2
fi

# Custom format enables selective/parallel restore. Do not echo the URL (it contains credentials).
pg_dump --format=custom --no-owner --dbname "$SWING_DATABASE_URL" --file "$OUT"
echo "backup written: $OUT"

# Retention: keep the 14 most recent dumps.
ls -1t "$OUT_DIR"/swing_*.dump 2>/dev/null | tail -n +15 | xargs -r rm -f
