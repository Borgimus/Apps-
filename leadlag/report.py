"""Report rendering: charts + executive markdown for the lead-lag study."""
from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .config import Config

_B = "#3f74d9"
_O = "#d9843f"
_R = "#c23b3b"


def chart_xcorr(agg: dict, out: Path) -> None:
    d = agg["xcorr_mean_by_lag_ms"]
    ks = sorted(d)
    vs = [d[k] for k in ks]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    colors = [_O if k < 0 else (_B if k > 0 else "#888") for k in ks]
    ax.bar(range(len(ks)), vs, color=colors)
    ax.set_xticks(range(len(ks)), [f"{k}" for k in ks], rotation=45, fontsize=8)
    ax.set_xlabel("lag ms  (negative = SPY leads, positive = ES leads)")
    ax.set_ylabel("mean corr(r_ES[t−k], r_SPY[t])")
    ax.set_title("ES↔SPY cross-correlation by lag (per-session mean)")
    ax.grid(alpha=0.25)
    fig.tight_layout(); fig.savefig(out, dpi=130); plt.close(fig)


def chart_heatmap(phase2: dict, out: Path) -> None:
    fig, axes = plt.subplots(1, 4, figsize=(15, 3.6))
    panels = [("by_minute", "minute after open"), ("by_volatility", "volatility"),
              ("by_volume", "SPY volume"), ("by_spread", "spread proxy")]
    for ax, (key, title) in zip(axes, panels):
        data = phase2[key]
        names = list(data)
        vals = np.array([[data[n]["mean_lead_mass"]] for n in names])
        im = ax.imshow(vals, aspect="auto", cmap="RdBu_r",
                       vmin=-np.nanmax(np.abs(vals)), vmax=np.nanmax(np.abs(vals)))
        ax.set_yticks(range(len(names)), [f"{n} (n={data[n]['n']})" for n in names],
                      fontsize=8)
        ax.set_xticks([])
        ax.set_title(f"lead mass by {title}", fontsize=9)
        for i, v in enumerate(vals[:, 0]):
            ax.text(0, i, f"{v:+.3f}", ha="center", va="center", fontsize=8)
    fig.suptitle("ES-leads-SPY mass (Σcorr k>0 − Σcorr k<0) by regime", fontsize=11)
    fig.tight_layout(); fig.savefig(out, dpi=130); plt.close(fig)


def chart_irf(phase3: dict, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.5))
    for thr, r in phase3.items():
        if "irf_bps" not in r:
            continue
        irf = np.array(r["irf_bps"])
        t = (np.arange(len(irf)) - r["irf_impulse_end_idx"]) * r["irf_dt_ms"] / 1000
        ax.plot(t, irf, label=f"|ES move| ≥ {thr} bp (n={r['n']})")
    ax.axvline(0, color="gray", lw=0.7, ls="--")
    ax.set_xlabel("seconds relative to ES impulse end")
    ax.set_ylabel("mean SPY response (bps, impulse-sign oriented)")
    ax.set_title("SPY impulse response to ES moves")
    ax.legend(fontsize=8); ax.grid(alpha=0.25)
    fig.tight_layout(); fig.savefig(out, dpi=130); plt.close(fig)


def chart_latency_decay(sweep_rows: list, out: Path) -> None:
    """Expectancy vs latency for each (L,θ,H) with enough trades — the money
    chart: where does the edge die?"""
    fig, ax = plt.subplots(figsize=(9, 4.5))
    groups: dict[tuple, list] = {}
    for r in sweep_rows:
        if r.n_trades >= 50:
            groups.setdefault((r.lookback_ms, r.threshold_bps, r.hold_ms), []).append(r)
    for (lb, thr, hold), rows in sorted(groups.items()):
        rows.sort(key=lambda r: r.latency_ms)
        ax.plot([r.latency_ms for r in rows], [r.expectancy_bps for r in rows],
                marker="o", ms=3, lw=1,
                label=f"L={lb}ms θ={thr}bp H={hold}ms")
    ax.axhline(0, color=_R, lw=0.8)
    ax.set_xlabel("execution latency (ms)")
    ax.set_ylabel("net expectancy per trade (bps)")
    ax.set_title("Edge vs latency — momentum-following ES→SPY")
    ax.legend(fontsize=6, ncol=2); ax.grid(alpha=0.25)
    fig.tight_layout(); fig.savefig(out, dpi=130); plt.close(fig)


def render_markdown(cfg: Config, results: dict) -> str:
    a = results["phase1"]
    L = ["# ES/MES → SPY Lead-Lag Study\n"]
    m = results["meta"]
    L.append(f"Sessions: {m['sessions']} ({m['first_day']} → {m['last_day']}) · "
             f"grid {cfg.grid['base_dt_ms']}ms · window 09:30–10:00 ET · "
             f"ES: Databento GLBX.MDP3 trades (continuous {cfg.fut_symbol}) · "
             f"SPY: Alpaca SIP trades\n")
    v = results.get("verdict", {})
    L.append(f"## Verdict: **{v.get('overall','(pending)')}**\n")
    for k, val in v.get("checks", {}).items():
        L.append(f"- {'✅' if val else '❌'} {k}")
    L.append("\n## Phase 1 — Price discovery\n")
    L.append(f"- xcorr peak lag: **{a['xcorr_peak_lag_ms']} ms** "
             f"(positive = ES leads); lead mass ES→SPY {a['lead_mass_es_leads']:+.3f} "
             f"vs SPY→ES {a['lead_mass_spy_leads']:+.3f}")
    g = a["granger"]
    L.append(f"- Granger ({g['n']} sessions): ES→SPY significant in "
             f"{100*g['frac_es_to_spy_sig']:.0f}% of sessions, SPY→ES in "
             f"{100*g['frac_spy_to_es_sig']:.0f}%")
    i = a["info_shares"]
    L.append(f"- Info shares ({i['n']} sessions): Gonzalo-Granger CS(ES) = "
             f"**{i['mean_gg_cs_es']:.2f}**; Hasbrouck IS(ES) ∈ "
             f"[{i['mean_hasbrouck_lower']:.2f}, {i['mean_hasbrouck_upper']:.2f}]; "
             f"α_ES {i['mean_alpha_es']:+.4f} vs α_SPY {i['mean_alpha_spy']:+.4f} "
             f"(the instrument that error-corrects is the follower)")
    t = a["transfer_entropy"]
    L.append(f"- Transfer entropy ({t['n']} sessions): net TE(ES→SPY) "
             f"{t['mean_net_te']:+.5f} nats, significant in {100*t['frac_sig']:.0f}%\n")
    L.append("![xcorr](xcorr.png)\n")
    L.append("## Phase 2 — Opening-session conditioning\n")
    L.append("![heatmap](heatmap.png)\n")
    p2 = results["phase2"]
    L.append("| slice | n | mean lead mass |")
    L.append("|---|---|---|")
    for grp in ("by_minute", "by_volatility", "by_volume", "by_spread"):
        for name, r in p2[grp].items():
            L.append(f"| {grp[3:]}:{name} | {r['n']} | {r['mean_lead_mass']:+.3f} |")
    L.append("\n## Phase 3 — Event study\n")
    L.append("![irf](irf.png)\n")
    L.append("| threshold | n | SPY resp @impulse end | final | ratio | half-resp delay | P(continue) |")
    L.append("|---|---|---|---|---|---|---|")
    for thr, r in results["phase3"].items():
        if "irf_bps" not in r:
            L.append(f"| {thr} bp | {r['n']} | — | | | | |")
            continue
        L.append(f"| {thr} bp | {r['n']} | {r['spy_response_at_impulse_end_bps']:+.2f} bp "
                 f"| {r['spy_response_final_bps']:+.2f} bp | {r['response_ratio']:.2f} "
                 f"| {r['half_response_delay_ms']} ms | {100*r['p_continuation_1s_to_10s']:.0f}% |")
    L.append("\n## Phase 4 — ES aggressor order flow → SPY\n")
    L.append("| window→horizon | sessions | mean rank IC | t | %>0 |")
    L.append("|---|---|---|---|---|")
    for k, r in results["phase4"].items():
        L.append(f"| {k} | {r['n_sessions']} | {r['mean_rank_ic']:+.4f} "
                 f"| {r['t_stat']:.1f} | {100*r['frac_positive']:.0f}% |")
    L.append("\n## Phase 5 — Strategy sweep (latency decay)\n")
    L.append("![latency](latency_decay.png)\n")
    rows = [r for r in results["phase5_rows"] if r.n_trades >= 50]
    rows.sort(key=lambda r: -(r.expectancy_bps if math.isfinite(r.expectancy_bps) else -9e9))
    L.append("| L ms | θ bp | H ms | λ ms | trades | exp bps | win | PF | t |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows[:15]:
        L.append(f"| {r.lookback_ms} | {r.threshold_bps} | {r.hold_ms} | {r.latency_ms} "
                 f"| {r.n_trades} | {r.expectancy_bps:+.2f} | {100*r.win_rate:.0f}% "
                 f"| {r.profit_factor:.2f} | {r.t_stat:.1f} |")
    L.append("\n## Phase 6 — Walk-forward + ML (at realistic latency)\n")
    for wf in results["phase6"]:
        L.append(f"- λ={wf['latency_ms']}ms: OOS {wf.get('oos_trades',0)} trades, "
                 f"exp {wf.get('oos_expectancy_bps', float('nan')):+.2f} bps, "
                 f"PF {wf.get('oos_profit_factor', float('nan')):.2f}, "
                 f"PF≥{cfg.validation['min_profit_factor']} gate: "
                 f"{'PASS' if wf.get('passes_pf_gate') else 'FAIL'}, "
                 f"params stable: {wf.get('param_stability', False)}")
    ml = results.get("phase5_ml", {})
    if "gbm" in ml:
        L.append(f"- ML (λ={ml['latency_ms']}ms): GBM OOS IC {ml['gbm']['oos_ic']:+.3f}, "
                 f"top/bottom-decile combined net {ml['gbm']['combined_net_bps']:+.2f} bps "
                 f"({ml['gbm']['n_signals']} signals); "
                 f"linear IC {ml['linear']['oos_ic']:+.3f}")
    L.append("")
    return "\n".join(L)


def write_all(cfg: Config, results: dict) -> Path:
    out = Path(cfg.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    chart_xcorr(results["phase1"], out / "xcorr.png")
    chart_heatmap(results["phase2"], out / "heatmap.png")
    chart_irf(results["phase3"], out / "irf.png")
    chart_latency_decay(results["phase5_rows"], out / "latency_decay.png")
    md = render_markdown(cfg, results)
    (out / "REPORT.md").write_text(md)
    serializable = {k: v for k, v in results.items() if k != "phase5_rows"}
    serializable["phase5_rows"] = [r.__dict__ for r in results["phase5_rows"]]
    if "session_rows" in serializable.get("phase2", {}):
        serializable["phase2"] = {k: v for k, v in serializable["phase2"].items()
                                  if k != "session_rows"}
    (out / "results.json").write_text(json.dumps(serializable, indent=1, default=str))
    return out / "REPORT.md"
