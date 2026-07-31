# Optional Windows TC2000 Companion

A small, auditable utility that runs on the Windows machine where TC2000 is installed. It watches
the folder you export EasyScan symbol files into and securely ships **complete 3-file batches** to
the trading service. It does **not** automate the TC2000 UI and **cannot place broker orders** — it
imports no broker/execution code and exposes no order functions. The trading service validates
every batch and remains the sole order authority.

Reference implementation: `companion/tc2000_watcher.py` (transport and credentials are injected;
the batch-detection and payload logic are unit-tested in `tests_swing/test_companion.py`).

## What it does
1. Watches a configured export folder for `strength_{1m,3m,6m}_YYYY-MM-DD.(txt|csv)` files.
2. When all three files for a market date are present, builds a payload of raw text + SHA-256
   hashes and POSTs it to the service ingest URL over TLS with a bearer API key.
3. Retries transient failures with backoff; never partially uploads a batch.

## Installation
1. Install Python 3.12 on the Windows host.
2. Copy the `companion/` folder to the host.
3. Create `companion.env` (NOT committed) with:
   ```
   SWING_INGEST_URL=https://your-service.example/ingest/tc2000
   SWING_COMPANION_API_KEY=...        # issued by the service; least-privilege, ingest-only
   TC2000_EXPORT_DIR=C:\Users\you\Documents\TC2000\exports
   ```
4. Configure TC2000 to export the three scans as symbol-only files into `TC2000_EXPORT_DIR`
   using the required filenames (see `docs/tc2000_setup.md`).

## Authentication
- The companion authenticates with a bearer API key scoped to the ingest endpoint only. The key is
  never logged and never grants order authority.
- Prefer a machine-scoped secret store (Windows Credential Manager) over a plaintext env file where
  available.

## Running as a service
- Run under Task Scheduler (at logon, restart on failure) or NSSM as a Windows service.
- Point logs to a rotating file; the companion redacts the API key from all output.

## Retry & idempotency
- Transient network/5xx failures retry with capped exponential backoff.
- Uploads carry file hashes; the service de-duplicates by batch hash, so a re-send of the same batch
  is a safe no-op.

## Uninstall
1. Stop/disable the Task Scheduler task or `nssm remove swing-companion confirm`.
2. Delete the `companion/` folder and `companion.env`.
3. Revoke the companion API key in the service so it can no longer authenticate.

## Security boundaries (enforced)
- No TC2000 UI automation.
- No broker/execution imports; no order/cancel/replace capability.
- Ingest-only API key; the service performs all validation and every trading decision.
