# Operations Runbook (PAPER ONLY)

This system fails **closed**: when in doubt it stops taking new risk while continuing to manage and
close existing positions. It never abandons a position or its protective stop. Every incident below
has the same first step: **check `/state` on the dashboard and the JSON logs**, then act.

Health/readiness:
- `GET /health` — liveness (always 200 if the process is up).
- `GET /ready` — 200 only after startup reconciliation passes; **503 blocks the load balancer** and
  means no new risk is being taken.

Emergency control: set the paper-only emergency stop (blocks new entries; keeps managing/closing
existing positions). Never use a control that abandons open positions or their stops.

---

## 1. Broker outage (Alpaca unreachable / 5xx / disconnect)
**Symptoms:** `BROKER_DISCONNECT` notifications; `/ready` 503; reconciliation cannot fetch state.
**Automatic behavior:** retryable faults back off (capped exponential, Retry-After honored); on
exhaustion the call fails closed — no order is assumed filled. New entries are blocked.
**Do:**
1. Confirm Alpaca paper status; verify network egress from the host.
2. Leave the process running — it keeps protective stops in place at the broker and resumes
   reconciliation automatically.
3. When connectivity returns, confirm reconciliation is clean (`reconciliation.ok`) before readiness
   flips back to 200. Investigate any incident before allowing new risk.

## 2. Market-data outage / stale data
**Symptoms:** `DATA_DISCONNECT`; entries rejected with `market_data_stale_or_unavailable`.
**Automatic behavior:** stale/missing bars fail closed; no entry is sized on stale prices.
**Do:**
1. Verify the data feed (record whether IEX or SIP) and the host clock (`clock_drift`).
2. Existing positions are still protected by stops at the broker. Wait for fresh data; do not
   override the staleness block.

## 3. Missing protective stop
**Symptoms:** `STOP_MISSING`; reconciliation `MISSING_STOP`; state `RISK_BLOCKED`.
**Automatic behavior:** new entries blocked; bounded recovery attempts to (re)submit the stop.
**Do:**
1. Inspect the position on the broker. If it truly lacks a covering stop, submit one immediately
   (the recovery routine uses an idempotent client order id).
2. Confirm `STOP_RECOVERED` and that reconciliation clears before resuming.
3. Never leave a filled position without a stop or an actively managed exit state.

## 4. Duplicate order intent
**Symptoms:** duplicate `client_order_id` insert rejected; `duplicate_order_intent` blocker.
**Why it's safe:** client order ids are deterministic — a retry reuses the same id, so the broker
de-duplicates and the DB unique constraint blocks a second local row.
**Do:** confirm only one broker order exists for the intent; if two somehow exist, cancel the
extra, reconcile, and record a `reconciliation_incidents` note.

## 5. Stale / invalid TC2000 scan
**Symptoms:** `IMPORT_REJECTED`; `tc2000_batch_stale_or_invalid`; dashboard batch `fresh=false`.
**Do:**
1. Re-run the three EasyScans for the current market date (see `docs/tc2000_setup.md`).
2. Re-upload all three files as one atomic batch with correct filenames/date.
3. Only `intersection_3_of_3` places orders; 2-of-3 and union are shadow-only.

## 6. Corrupted / unavailable database
**Symptoms:** `database_persistence_failed`; writes error.
**Automatic behavior:** new entries blocked (can't persist the audit record).
**Do:**
1. Stop the service gracefully (SIGTERM) — it drains new entries but keeps protective stops.
2. Restore from the latest backup: `scripts/restore_db.sh <dump>` (see backup below).
3. Run startup reconciliation; broker state wins for actual positions/orders. Resolve every
   discrepancy before readiness returns.

---

## Startup & shutdown
- **Startup:** logging → config validation (`allow_live` must be false) → paper-endpoint
  verification → **startup reconciliation** → readiness. A reconciliation mismatch keeps `/ready`
  at 503 until resolved.
- **Graceful shutdown (SIGTERM):** stop accepting new entries; keep managing existing positions and
  their stops until they close or the process exits. Stops are never cancelled on shutdown.

## Backups
- Nightly: `SWING_DATABASE_URL=... scripts/backup_db.sh /var/backups/swing` (keeps 14 dumps).
- Test restores periodically into a scratch database; a backup you have not restored is not a backup.

## Escalation
- Page on: `STOP_MISSING`, `RECON_MISMATCH`, `BROKER_DISCONNECT` > N minutes, or any state stuck in
  `RISK_BLOCKED`/`RECON_BLOCKED`. Capture `/state` and the relevant JSON log lines with the incident.
