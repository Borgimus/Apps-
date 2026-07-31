# TC2000 Setup, EasyScans & Export Handoff

> TC2000 is the charting / EasyScan tool from the strategy. This project does **not** automate
> TC2000's UI and does **not** invent a TC2000 API or webhook. The operator exports symbol lists
> from three EasyScans and hands them to the trading service via the importer.
>
> ⚠️ **Chart angle is scale-dependent.** The "45-degree" moving-average concept is represented in
> code by normalized slope thresholds (percent change of the MA over a lookback), never by literal
> on-screen angle. See `config/strategy.yaml → trend`.

## 1. The three strength EasyScans

Create three EasyScans, one per lookback. Each selects the **top ~2%** of the chosen watchlist
universe by lookback return, then applies the eligibility filters.

| Scan | Lookback (daily bars) | Purpose |
|------|-----------------------|---------|
| `strength_1m.txt` | ~20 | one-month gainers |
| `strength_3m.txt` | ~60 | three-month gainers |
| `strength_6m.txt` | ~120 | six-month gainers |

### Personal Criteria Formulas (PCF) — provisional

Enter these in TC2000 (Formula tab). Symbol names follow TC2000 PCF conventions; verify against
the TC2000 help ("copying WatchList symbols", "importing saved symbol files", "exporting chart data").

- **Lookback return (rank; keep top 2%):**
  - 1M: `(C / C21 - 1) * 100`  (close vs 21 bars ago)
  - 3M: `(C / C61 - 1) * 100`
  - 6M: `(C / C121 - 1) * 100`
- **Price filter:** `C > 1`
- **Minimum daily movement / volatility (ADR%, 20-bar):**
  `AvgC20( (H - C1) / C1 * 100 )` is **not** ADR; use the range form:
  `Avg( (H - L) / C1 * 100, 20 ) >= 5`
  (In code the authoritative ADR% is `mean((H−L)/prev_close·100, 20)` — see `src/indicators`.)
- **Average daily dollar volume (≥ $30M default):**
  `Avg( C * V, 20 ) >= 30000000`
  For a small paper account you may lower this floor to **no less than $5,000,000**
  (`config/strategy.yaml → universe.small_account_min_dollar_volume`).

> Rank the scan output by the lookback-return column and keep the top 2% of rows. TC2000's
> "top X%" selection or a manual top-N cut both work; record which you used.

## 2. Daily export procedure (operator, on the Windows/TC2000 machine)

1. Run each of the three EasyScans **against the same watchlist universe** on the same market date.
2. For each scan, select all resulting symbols → **copy WatchList symbols** (or export) to a
   **symbol-only** `.txt`/`.csv` (one ticker per line; no prices, no headers required).
3. Save with the required filenames and a date stamp (see below).
4. Upload all three files as **one atomic batch** via the dashboard importer or CLI
   (`scripts/import_tc2000.py`). Partial or mismatched-date batches are rejected.

### Required filenames & timestamp convention

```
strength_1m_YYYY-MM-DD.txt
strength_3m_YYYY-MM-DD.txt
strength_6m_YYYY-MM-DD.txt
```

- `YYYY-MM-DD` = the **market date** the scan represents (America/New_York).
- All three files in a batch must share the same market date.
- The importer preserves the raw files and records their SHA-256 hashes.

### File format

```
# optional comment lines starting with '#'
AAPL
NVDA
SMCI
```
One symbol per line. Case-insensitive; uppercased on import. Blank lines and `#` comments ignored.
Basic symbol validation: `^[A-Z][A-Z0-9.\-]{0,9}$`.

## 3. Import validation & candidate sets

The importer validates: file presence (all three), market-date consistency, symbol format,
intra-file duplicates, empty lists, and batch freshness. It then produces:

- `intersection_3_of_3` — symbols in all three scans (**strict default; only this places orders**)
- `agreement_2_of_3` — symbols in ≥ two scans (shadow only)
- `union_ranked` — any scan, ranked by composite strength (shadow only)

Every candidate records `source = TC2000`, the batch hash, the market date, and per-scan membership.
Alpaca (or the configured market-data source) independently re-validates all execution-critical
prices and indicators — TC2000 and Alpaca feeds are **not** assumed identical.

## 4. Validation checklist (known samples)

Before trusting a batch:

- [ ] All three files present, same market date, non-empty.
- [ ] A known large-cap that should fail the "smaller/faster" spirit is absent or explainable.
- [ ] A known sub-$1 symbol never appears (price filter).
- [ ] Spot-check one symbol's 20/60/120-bar return in TC2000 vs the importer's recorded membership.
- [ ] Re-run the same scans twice → identical symbol sets (determinism).
- [ ] Importer reports the batch as `fresh` and hashes match the saved raw files.

## 5. Screenshots

Direct TC2000 screenshots are not bundled in this repo. Insert them here when captured:

- `docs/img/tc2000_scan_1m.png` — _placeholder: 20-bar strength EasyScan with PCF & filters_
- `docs/img/tc2000_scan_3m.png` — _placeholder: 60-bar_
- `docs/img/tc2000_scan_6m.png` — _placeholder: 120-bar_
- `docs/img/tc2000_export.png`  — _placeholder: copy/export WatchList symbols dialog_

## 6. Optional Windows companion

A small auditable utility may watch the export folder and POST newly exported lists to the trading
service over TLS with an API key. It must **not** automate the TC2000 UI and must be **unable to
place broker orders**. Its own install/auth/retry/uninstall docs live in
`docs/windows_companion.md` (later phase).
