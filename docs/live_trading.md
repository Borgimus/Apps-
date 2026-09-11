# Live Paper-Trading Loop (PAPER ONLY)

The live loop ties the tested deterministic components into a running decision cycle against an
Alpaca **paper** account. It is still paper-only — the adapter rejects any non-paper endpoint — and
mode-gated so it cannot trade unattended without explicit operator acknowledgement.

## The tick cycle (`src/live/orchestrator.py`)

Each `tick()`:
1. **Fetch + reconcile** broker truth (`reconcile_live`). Broker state wins; a mismatch (unknown
   position, missing stop, qty drift) blocks new entries.
2. **Manage** every open position (`manage_open_position`): one-time 5R partial + breakeven stop
   move, confirmed daily-close-below-SMA10 exit (precedence), and a missing-stop alert →
   `RISK_BLOCKED`. Managing is **never** gated — it runs even under emergency stop / shutdown.
3. **Enter**: only if the fail-closed gate passes (`can_open_new_entry` — endpoint, data, recon,
   batch, DB, stops, risk, buying power, session, clock, duplicate, emergency stop), evaluate each
   qualified candidate (`evaluate_entry`) and, in PAPER_AUTO, submit a stop-limit entry with an
   attached protective stop.

## Mode gating (strict)

| Mode | Behavior |
|------|----------|
| `SHADOW` | computes the exact decisions, emits **proposals**, submits nothing |
| `PAPER_CONFIRM` | same as shadow; proposals await out-of-band operator approval |
| `PAPER_AUTO` | **submits** orders through the paper adapter (idempotent client order IDs) |

There is no live mode. `run_swing.py` refuses `PAPER_AUTO` unless `SWING_ACCEPT_PAPER_AUTO=1` is set
(the operator attesting the acceptance gates in `docs/implementation_plan.md` are met); otherwise it
downgrades to SHADOW.

## Wiring (`src/live/service.py`, `src/broker/alpaca_client.py`, `src/data/alpaca_data.py`)

- `AlpacaPaperRESTClient` — account/positions/orders REST calls (paper host enforced; credentials
  from env, never logged).
- `AlpacaDataClient` — daily bars + latest trade/quote → `MarketSnapshot` with feed metadata.
- `build_market_view` — assembles the per-symbol view (fresh snapshot, last price, and — once the
  session is complete — the daily close + SMA10 the final-exit rule needs).
- `reconcile_live` — maps live positions/orders into a `ReconResult`.
- `load_setups_file` — reads scanner-produced candidate setups (symbol, breakout level, initial
  stop, reference price, version) from a JSON file, mirroring the TC2000 file-handoff philosophy.

## Running it

The live loop needs `pyyaml` + `httpx` (and `tzdata` on Windows, where Python has no system
IANA time-zone database — without it startup fails with
`No time zone found with key America/New_York`):

```bash
pip install pyyaml httpx tzdata     # tzdata required on Windows; harmless on Linux/macOS
# in .env (never committed): paper keys + dashboard token, then:
export SWING_MODE=SHADOW            # start here; PAPER_AUTO also needs SWING_ACCEPT_PAPER_AUTO=1
export SWING_ENABLE_LOOP=1          # opt-in; without it the process is dashboard/health only
export SWING_SETUPS_FILE=./data/live_setups.json
python scripts/run_swing.py
```

`data/live_setups.json` (gitignored) example:
```json
[
  {"symbol": "SMCI", "breakout_level": 48.20, "initial_stop": 45.10,
   "reference_price": 47.90, "setup_version": "2026-07-31"}
]
```

## Durable trade-state store (`src/live/trade_state.py`)

A JSON-backed store persists each open trade's lifecycle so the loop can **manage positions across
restarts**: `trade_id`, actual VWAP entry, the original `initial_stop` (R reference), the current
resting stop, `partial_done`, the running high since entry, and the client_order_ids this system
originated. On each PAPER_AUTO action the orchestrator writes to the store — critically,
`on_partial` is idempotent so a restart **cannot take the 5R partial twice**.

`run_swing.py` now wires it in: `get_positions = store.reconstruct_positions(broker.list_positions())`
(broker truth wins on quantity; a stored trade with no live position is never fabricated), and
reconciliation draws `expected_positions` + `known_client_order_ids` from the store.

## What's wired vs. the next increment

**Wired & tested (fakes):** the full tick cycle, entry/manage decisions, mode gating, fail-closed
gate, the Alpaca REST + data clients (request building), market-view assembly, live reconciliation,
the interval runner, and the durable trade-state store (entries/partials/exits persisted;
5R-once survives restart).

**Next increment (documented):** **fill tracking** — the store records the *expected* entry at
submit time; a trade-update stream (or REST fill polling) would correct it to the *actual* VWAP
fill and confirm the attached stop before the position is treated as fully protected. Until then the
loop sizes/records from the conservative expected fill and relies on per-tick REST reconciliation to
surface drift (broker wins), which is fail-closed but coarser than streaming.

## Safety recap

Paper endpoint enforced · AI never in this path · idempotent orders · every entry carries an
attached protective stop · managing/closing never blocked · PAPER_AUTO double-gated (config mode +
explicit env attestation).
