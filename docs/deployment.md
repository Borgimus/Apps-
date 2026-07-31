# Deployment (PAPER ONLY)

GitHub is source-control and CI only. **Do not** run the intraday trading loop on hosted GitHub
Actions. Deploy on a persistent Linux host/VPS; the optional TC2000 file handoff runs on the
Windows machine where TC2000 is installed.

## Local development (Docker)
```bash
cp .env.example .env      # fill PAPER keys + SWING_DASHBOARD_TOKEN; never commit .env
docker compose -f deploy/docker-compose.yml up --build
# dashboard on http://127.0.0.1:8080  (/, /state require the bearer token; /health, /ready are public)
```

## Persistent deployment — Docker Compose
1. Provision a Linux host; install Docker + compose.
2. Create `.env` with `POSTGRES_PASSWORD`, `SWING_ALPACA_KEY_ID/SECRET_KEY` (paper),
   `SWING_DASHBOARD_TOKEN`, and `SWING_MODE` (start at `SHADOW`).
3. `docker compose -f deploy/docker-compose.yml up -d --build`.
4. Apply migrations: `docker compose -f deploy/docker-compose.yml run --rm swing \
   alembic -c migrations/alembic.ini upgrade head`.
5. Front the loopback-bound dashboard with a reverse proxy terminating TLS; never expose the
   bearer-token endpoints without TLS.

## Persistent deployment — systemd (no Docker)
1. `python3.12 -m venv /opt/swing/venv && /opt/swing/venv/bin/pip install -r deploy/requirements-swing.txt`.
2. Put secrets in `/etc/swing/swing.env` (chmod 600). Copy `deploy/swing.service` to
   `/etc/systemd/system/`.
3. `systemctl daemon-reload && systemctl enable --now swing`. Logs: `journalctl -u swing -f`.

## Health, readiness, logs
- `/health` liveness · `/ready` gated on startup reconciliation (503 until clean).
- Structured JSON logs to stdout and (optionally) a rotating file (`SWING_LOG_FILE`, 10 MB × 5).
  Secrets are redacted before write.

## Startup reconciliation before readiness
The service verifies the paper endpoint and reconciles broker truth against the DB **before**
reporting ready. Broker state wins for actual positions/orders; any discrepancy keeps `/ready` at
503 and blocks new risk until resolved.

## Graceful shutdown
`SIGTERM` (compose `stop_grace_period`/systemd `TimeoutStopSec` = 30s) drains new entries while
continuing to manage and close existing positions. Protective stops are never cancelled on shutdown.

## Backups & restore
`scripts/backup_db.sh` (nightly cron) and `scripts/restore_db.sh` — see `docs/runbook.md §Backups`.

## Modes
`BACKTEST → SHADOW → PAPER_CONFIRM → PAPER_AUTO` via `SWING_MODE`. No live phase exists. PAPER_AUTO
is gated by the acceptance criteria in `docs/implementation_plan.md`.
