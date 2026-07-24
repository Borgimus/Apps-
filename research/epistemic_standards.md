# Epistemic Standards — Options Research Documents

**Applies to:** All documents in `research/` and `evaluation/`  
**Created:** 2026-07-12  
**Purpose:** Every analytical claim in this codebase's research documents is tagged with one of the four categories below. The tag describes how well-supported the claim is, independent of whether it turns out to be correct.

---

## Category Definitions

| Tag | Meaning | Requirements |
|-----|---------|--------------|
| `OBSERVED` | A fact directly measured from the data with no interpretation | Can be independently verified by re-running the same query or reading the same log |
| `DERIVED` | A number or quantity computed from observed facts (ratio, sum, average, rank) | The computation is mechanical; the only error possible is arithmetic |
| `INFERRED` | A pattern, mechanism, or causal explanation drawn from derived or observed facts | Requires reasoning; a different analyst could reasonably reach a different conclusion from the same data |
| `SPECULATIVE` | A hypothesis, counterfactual, or generalization not yet supported by sufficient data | May be plausible or even likely; it has not been tested with adequate sample size or independent confirmation |

A claim can carry multiple tags when it has both a derived component (the number) and an inferred component (the interpretation of that number).

---

## Contamination Flag

Claims based on pre-phase3 sessions (before 2026-07-12) are flagged `[CONTAMINATED SOURCE]`. These sessions ran on code with defects corrected in P1–P7:

| Defect | Priority | Effect on data |
|--------|----------|---------------|
| Bug C: midpoint exit P&L | P2 (earlier) | Exit prices overstated by ~$0.06–0.40/share |
| Bug D: duplicate exit 403 | P2 (earlier) | `daily_pnl` corrupted in affected session |
| No EXIT_PENDING state | P2 | Duplicate exits possible; realized P&L may be wrong |
| Kill-switch blocked exits | P3 | Positions could be stranded |
| RiskManager counter reset | P6 | Daily trade limit unreliable after restart |

A `[CONTAMINATED SOURCE]` flag means the data supporting that claim came from sessions where one or more of these defects were active. The claim may still be directionally correct, but it cannot be used as the basis for strategy-performance conclusions.

---

## How to Read the Claims Registries

Each research document begins with a `## Claims Registry` block listing its significant analytical claims, their epistemic tag, and contamination status. The registry is not exhaustive of every sentence — it covers claims that could influence a decision (about strategy, thresholds, architecture, or protocol design).

Claims marked `SPECULATIVE` or `INFERRED + [CONTAMINATED SOURCE]` must not be used to justify parameter changes, strategy modifications, or performance conclusions until confirmed with Phase 3 data.

---

## Claim Tags Summary Across All Research Documents

| Category | Count | Typical use |
|----------|-------|-------------|
| `OBSERVED` | ~18 | Counts, code structure facts, config values |
| `DERIVED` | ~25 | Win rates, P&L sums, score averages, ratios |
| `INFERRED` | ~60 | Mechanism explanations, pattern attributions, "appears correlated" |
| `SPECULATIVE` | ~17 | Generalization from n=1, counterfactuals, untested hypotheses |

Most claims in this codebase are `INFERRED` from contaminated data. That is the primary epistemic risk.
