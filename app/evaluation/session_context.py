"""Capture report provenance at session start, never from later environment values."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[2]


def capture_session_context(settings, started_at: datetime, strategy_ids=(), *,
                            baseline_path=None) -> dict:
    from app.evaluation.shadow_book import SHADOW_MODEL_VERSION
    from app.evaluation.shadow_replay import capture_replay_policy, policy_hash
    from app.trading.entry_filters import scaled_entry_block_reason, scaled_guardrails_active

    if started_at.tzinfo is None:
        raise ValueError("Session start time must include a timezone")

    provider = settings.options_data_provider
    adapter_hash = None
    if provider == "tradier":
        adapter_hash = hashlib.sha256(
            (ROOT / "app/brokers/alpaca_tradier_data.py").read_bytes()
        ).hexdigest()[:8]
    guardrails = scaled_guardrails_active(settings)
    base = (settings.paper_scaled_guardrail_cohort if guardrails else
            ("paper_scaled" if settings.paper_scaled_sizing_enabled else "one_contract"))
    cohort = f"{base}.options_{provider}"
    baseline = Path(baseline_path) if baseline_path is not None else ROOT / "evaluation/phase3_tracking.json"
    try:
        fingerprint = json.loads(baseline.read_text())["phase3_fingerprint"]
        amendment = fingerprint.get("_tradier_data_amendment", {})
        if (guardrails and provider == "tradier"
                and fingerprint.get("options_data_provider") == provider
                and fingerprint.get("options_data_adapter_hash") == adapter_hash
                and isinstance(amendment.get("cohort"), str)):
            cohort = amendment["cohort"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass  # Report the explicit provider fallback; preflight owns verification.

    started_at = started_at.astimezone(ET)
    hour, minute = map(int, settings.market_open.split(":"))
    scheduled = started_at.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delay = max(0.0, (started_at - scheduled).total_seconds())
    diagnostic = [sid for sid in strategy_ids if sid == "rsi_trend"
                  and settings.paper_eval_permissive_entry_mode]
    shadow_only = [sid for sid in strategy_ids
                   if scaled_entry_block_reason(settings, "", sid) == "strategy_shadow_only"]
    enabled = sorted(set(strategy_ids) - set(diagnostic) - set(shadow_only))
    replay_policy = capture_replay_policy(settings)
    return {
        "schema_version": 1,
        "options_data_provider": provider,
        "options_data_adapter_hash": adapter_hash,
        "evaluation_cohort": cohort,
        "shadow_model_version": SHADOW_MODEL_VERSION,
        "runner_started_at": started_at.isoformat(),
        "scheduled_start_at": scheduled.isoformat(),
        "start_delay_seconds": round(delay, 3),
        "session_labels": ["late_start"] if delay > 60 else [],
        "broker_entry_strategies": enabled,
        "diagnostic_only_strategies": diagnostic,
        "shadow_only_strategies": shadow_only,
        "replay_policy": replay_policy,
        "replay_policy_hash": policy_hash(replay_policy),
        "observation_review_targets": {"scheduled_sessions": 5, "eligible_opportunities": 10},
    }


def context_from_logs(logs) -> dict:
    contexts = []
    for row in logs:
        if row.event != "session_context":
            continue
        try:
            value = json.loads(row.data_json)
            if isinstance(value, dict) and value.get("schema_version") == 1:
                contexts.append(value)
        except (ValueError, TypeError):
            continue
    if not contexts:
        return {}  # Historical reports must not inherit today's provider/cohort.
    result = dict(contexts[0])
    keys = ("options_data_provider", "options_data_adapter_hash", "evaluation_cohort",
            "shadow_model_version", "broker_entry_strategies", "replay_policy_hash")
    if any(any(c.get(k) != result.get(k) for k in keys) for c in contexts[1:]):
        result["options_data_provider"] = "mixed"
        result["evaluation_cohort"] = "mixed_session_context"
        result["options_data_adapter_hash"] = None
        result["shadow_model_version"] = "mixed"
        result["broker_entry_strategies"] = None
        result["session_labels"] = list(result.get("session_labels", [])) + ["mixed_session_context"]
    return result
