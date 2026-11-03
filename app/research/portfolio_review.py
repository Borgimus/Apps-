"""Offline, causal portfolio proxies and cumulative research checkpoints.

No broker, credential, subscription, or order API is imported or called.
Prices are completed-bar proxies. Costs are assumptions, never actual fills.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
from zoneinfo import ZoneInfo

from app.research.baseline import frozen_baseline, baseline_identity, PROTOCOL

ET = ZoneInfo('America/New_York')
POLICY_FILE = 'evaluation/equity_portfolio_policy_2026_11_03.json'
POLICY_SHA256 = '2577d5be2d5076c0cefe4f04f5b6f715e288129da552f938b096ce2ec679c455'
STEP = timedelta(minutes=5)


class EvidenceError(ValueError):
    pass


def stamp(value):
    ts = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if ts.tzinfo is None:
        raise EvidenceError('naive_timestamp')
    return ts.astimezone(ET)


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EvidenceError('nonfinite_number')
    return value


def load_policy(root):
    raw = (root / POLICY_FILE).read_bytes()
    if hashlib.sha256(raw).hexdigest() != POLICY_SHA256:
        raise EvidenceError('replay_policy_changed_requires_new_version')
    return {**json.loads(raw), 'policy_sha256': POLICY_SHA256}


@dataclass
class Session:
    report: dict
    category: str
    reasons: list = field(default_factory=list)
    bars: dict = field(default_factory=lambda: defaultdict(dict))
    available: dict = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)


def required_times(signal, minutes=30):
    start = stamp(signal['entry_bar'])
    return [start + STEP * i for i in range(minutes // 5)]


def forward_bps(session, signal, minutes):
    times = required_times(signal, minutes)
    bars = session.bars[signal['symbol']]
    if not all(ts in bars for ts in times):
        return None
    sign = 1 if signal['direction'] == 'long' else -1
    return round(sign * (bars[times[-1]]['close'] / bars[times[0]]['open'] - 1) * 10000, 4)


def load_session(path, evidence, baseline):
    raw = path.read_bytes()
    report = json.loads(raw)
    day = date.fromisoformat(path.stem)
    if report.get('date') != str(day) or day.weekday() >= 5:
        raise EvidenceError('session_date_mismatch')
    if (report.get('protocol') != PROTOCOL or report.get('feed') != 'iex'
            or report.get('mode') != 'equity_research'
            or report.get('broker_entries_enabled') is not False):
        raise EvidenceError('unverified_protocol_or_order_disable_state')
    if (not isinstance(report.get('signals'), list) or not isinstance(report.get('symbols'), list)
            or type(report.get('cycles')) is not int or report['cycles'] < 0
            or not isinstance(report.get('code_sha'), str)
            or not re.fullmatch('[0-9a-f]{40}', report['code_sha'])):
        raise EvidenceError('invalid_report_shape')
    if report.get('baseline') != baseline_identity(baseline):
        if report.get('baseline') is not None or report.get('code_sha') not in baseline['verified_legacy_source_commits']:
            raise EvidenceError('unverified_or_changed_collection_baseline')
    session = Session(report, 'invalid', inputs={'report_sha256': hashlib.sha256(raw).hexdigest()})
    if report.get('research_status') == 'running':
        session.category = 'in_progress'
        return session
    start, end = stamp(report['session_start']), stamp(report['last_cycle_at'])
    generated = stamp(report['generated_at'])
    if any(ts.date() != day for ts in (start, end, generated)) or not start <= end <= generated:
        raise EvidenceError('session_clock_mismatch')
    if report.get('symbols') != baseline['symbols']:
        raise EvidenceError('universe_changed')
    fresh = report.get('fresh_symbols', [])
    if not isinstance(fresh, list) or not set(fresh) <= set(report['symbols']):
        raise EvidenceError('invalid_fresh_symbols')
    coverage = len(set(fresh)) / len(report['symbols'])
    if abs(number(report['coverage_fraction']) - coverage) > 1e-9:
        raise EvidenceError('coverage_mismatch')
    if (type(report.get('request_failures')) is not int or type(report.get('signal_errors')) is not int
            or report['request_failures'] != 0 or report['signal_errors'] != 0
            or report.get('data_status') not in ('ok', 'outside_active_window')
            or coverage < .8 or 'SPY' not in fresh):
        session.reasons.append('data_or_strategy_health_failed')
        return session
    events_path = evidence / 'equity_research/events' / f'{day}.jsonl'
    if events_path.is_symlink() or not events_path.is_file():
        raise EvidenceError('raw_events_missing_or_symlink')
    signals = {}
    digest = hashlib.sha256()
    previous = None
    starts = []
    with events_path.open('rb') as incoming:
        for line in incoming:
            digest.update(line)
            if not line.strip():
                continue
            event = json.loads(line)
            observed = stamp(event['observed_at'])
            if (event.get('protocol') != PROTOCOL or event.get('feed') != 'iex'
                    or event.get('code_sha') != report['code_sha']
                    or observed.date() != day or observed > end
                    or (previous is not None and observed < previous)):
                raise EvidenceError('raw_event_provenance_or_clock_mismatch')
            previous = observed
            kind = event['event']
            if kind == 'session_start':
                starts.append(observed)
                if event.get('broker_entries_enabled') is not False or event.get('symbols') != report['symbols']:
                    raise EvidenceError('raw_start_mismatch')
            elif kind == 'completed_bar':
                symbol, bar = event['symbol'], event['bar']
                ts = stamp(bar['t'])
                prices = [number(bar[k]) for k in ('open', 'high', 'low', 'close')]
                if (symbol not in report['symbols'] or ts.minute % 5 or ts.second or ts.microsecond
                        or ts + STEP > observed or min(prices) <= 0
                        or prices[1] < max(prices[0], prices[3])
                        or prices[2] > min(prices[0], prices[3]) or prices[1] < prices[2]
                        or number(bar['volume']) < 0):
                    raise EvidenceError('invalid_or_forming_raw_bar')
                if ts.date() == day:
                    if not time(9, 30) <= ts.time() < time(16):
                        raise EvidenceError('non_session_raw_bar')
                    if ts in session.bars[symbol] and session.bars[symbol][ts] != bar:
                        raise EvidenceError('first_observed_bar_rewritten')
                    session.bars[symbol].setdefault(ts, bar)
                    session.available.setdefault((symbol, ts), observed)
            elif kind == 'signal':
                signal = event['signal']
                if signal['id'] in signals or stamp(signal['observed_at']) != observed:
                    raise EvidenceError('duplicate_or_mistimed_signal')
                signals[signal['id']] = signal
            else:
                raise EvidenceError('raw_error_or_unknown_event')
    if starts != [start]:
        raise EvidenceError('raw_session_start_mismatch')
    raw_fresh = {symbol for symbol in report['symbols'] if session.bars[symbol]
        and 0 <= (end - max(session.bars[symbol]) - STEP).total_seconds() <= 600}
    if raw_fresh != set(fresh):
        raise EvidenceError('raw_freshness_mismatch')
    session.inputs['events_sha256'] = digest.hexdigest()
    if len(report['signals']) != len(signals):
        raise EvidenceError('raw_report_signal_count_mismatch')
    ids = set()
    for signal in report['signals']:
        key = signal['id']
        if key in ids or key not in signals:
            raise EvidenceError('duplicate_or_missing_report_signal')
        ids.add(key)
        if {k: v for k, v in signal.items() if k != 'gross_forward_bps'} != signals[key]:
            raise EvidenceError('raw_report_signal_mismatch')
        confirmed, observed, entry = (stamp(signal[k]) for k in ('confirmed_at', 'observed_at', 'entry_bar'))
        lag = (observed - confirmed).total_seconds()
        expected_entry = datetime.fromtimestamp(math.ceil(observed.timestamp() / 300) * 300, ET)
        expected_id = f"{signal['symbol']}|{signal['strategy']}|{signal['direction']}|{confirmed.isoformat()}"
        eligible = lag <= 600 and entry.time() < time(12)
        if (signal['symbol'] not in report['symbols'] or signal['strategy'] not in ('orb', 'vwap_reclaim')
                or signal['direction'] not in ('long', 'short') or confirmed.date() != day
                or confirmed.time() >= time(12) or confirmed.minute % 5 or confirmed.second or confirmed.microsecond
                or lag < 0 or abs(number(signal['signal_lag_seconds']) - lag) > .001
                or entry != expected_entry or key != expected_id or signal['prospective'] is not eligible):
            raise EvidenceError('invalid_signal_causality_or_eligibility')
        if session.available.get((signal['symbol'], confirmed - STEP), generated + STEP) > observed:
            raise EvidenceError('signal_confirmed_before_bar_was_observed')
        for horizon in (5, 15, 30):
            expected = forward_bps(session, signal, horizon)
            actual = signal['gross_forward_bps'][str(horizon)]
            if (expected is None and actual is not None) or (expected is not None and
                    (actual is None or abs(number(actual) - expected) > .00011)):
                raise EvidenceError('forward_return_mismatch')
    if (report.get('session_complete') is not True or report.get('termination') != 'scheduled_end'
            or report.get('research_status') != 'completed' or end.time() < time(12, 30)):
        session.category = 'partial'
        session.reasons.append('interrupted_or_early_finish')
    elif not time(9, 30) <= start.time() <= time(9, 35):
        session.category = 'partial'
        session.reasons.append('started_after_first_bar_window')
    else:
        session.category = 'full_valid'
    return session


def signal_summary(sessions):
    rows = []
    for strategy in ('orb', 'vwap_reclaim'):
        for horizon in (5, 15, 30):
            by_day = {}
            for session in sessions:
                values = [s['gross_forward_bps'][str(horizon)] for s in session.report['signals']
                          if s['prospective'] and s['strategy'] == strategy
                          and s['gross_forward_bps'][str(horizon)] is not None]
                by_day[session.report['date']] = values
            values = [v for day in by_day.values() for v in day]
            mean = statistics.mean(values) if values else None
            daily = {day: statistics.mean(vals) if vals else None for day, vals in by_day.items()}
            rows.append({'strategy': strategy, 'horizon_minutes': horizon, 'observations': len(values),
                'mean_gross_bps': mean, 'median_gross_bps': statistics.median(values) if values else None,
                'positive_fraction': sum(v > 0 for v in values) / len(values) if values else None,
                'cost_sensitivity_bps': {str(c): mean - c if mean is not None else None for c in (5, 10, 20)},
                'daily_mean_gross_bps': daily,
                'mean_without_best_signal_bps': statistics.mean(sorted(values)[:-1]) if len(values) > 1 else None,
                'leave_one_day_out_mean_bps': {day: statistics.mean(other) if other else None for day in by_day
                    for other in [[v for key, vals in by_day.items() if key != day for v in vals]]}})
    return rows


def money(value):
    return round(float(value), 6)


def replay(sessions, policy, cost_bps):
    """Reserve slots at discovery; recognize P&L only when exit bars were received."""
    equity = Decimal(str(policy['initial_equity_dollars']))
    starting = equity
    trades, days = [], []
    skip_counts = Counter()
    unresolved_prior = False
    for session in sessions:
        report = session.report
        day_start = equity
        realized = Decimal(0)
        active = None
        entries = 0
        day_trades = []
        skipped = Counter()
        order = {s: i for i, s in enumerate(report['symbols'])}
        candidates = sorted((s for s in report['signals'] if s['prospective']), key=lambda s:
            (stamp(s['entry_bar']), stamp(s['observed_at']), order[s['symbol']], s['strategy'], s['id']))

        def settle(at):
            nonlocal active, equity, realized
            if active is not None and active['settled_at'] is not None and stamp(active['settled_at']) <= at:
                equity += active['_net']
                realized += active['_net']
                active = None

        for signal in candidates:
            if unresolved_prior:
                skipped['unresolved_prior_exposure'] += 1
                continue
            decision = stamp(signal['observed_at'])
            settle(decision)
            if signal['direction'] not in policy['directions']:
                skipped['unverified_short_availability'] += 1
                continue
            if active is not None:
                skipped['position_slot_reserved'] += 1
                continue
            if entries >= policy['max_entries_per_day']:
                skipped['daily_entry_limit'] += 1
                continue
            if realized <= -Decimal(str(policy['daily_realized_loss_entry_gate_dollars'])):
                skipped['daily_realized_loss_entry_gate'] += 1
                continue
            times = required_times(signal, policy['holding_minutes'])
            bars = session.bars[signal['symbol']]
            entry_bar = bars.get(times[0])
            quantity = None
            if entry_bar:
                cash_budget = min(Decimal(str(policy['max_position_notional_dollars'])),
                                  equity / (1 + Decimal(str(cost_bps)) / 10000))
                quantity = int((cash_budget / Decimal(str(entry_bar['open']))).to_integral_value(rounding=ROUND_FLOOR))
                if quantity <= 0:
                    skipped['whole_share_cash_limit'] += 1
                    continue
            entries += 1
            complete = all(ts in bars for ts in times)
            trade = {'date': report['date'], 'signal_id': signal['id'], 'symbol': signal['symbol'],
                'strategy': signal['strategy'], 'decision_at': signal['observed_at'],
                'entry_at': signal['entry_bar'], 'exit_at': (times[-1] + STEP).isoformat(),
                'quantity': quantity, 'status': 'closed_proxy' if complete else 'unresolved',
                'entry_price_proxy': entry_bar['open'] if entry_bar else None,
                'exit_price_proxy': bars[times[-1]]['close'] if complete else None,
                'settled_at': None, 'proxy_gross_pnl_dollars': None, 'assumed_cost_dollars': None,
                'proxy_net_pnl_dollars': None, '_net': None}
            if complete:
                entry = Decimal(str(entry_bar['open']))
                gross = Decimal(quantity) * (Decimal(str(bars[times[-1]]['close'])) - entry)
                cost = Decimal(quantity) * entry * Decimal(str(cost_bps)) / 10000
                trade.update(settled_at=max(session.available[(signal['symbol'], ts)] for ts in times).isoformat(),
                    proxy_gross_pnl_dollars=money(gross), assumed_cost_dollars=money(cost),
                    proxy_net_pnl_dollars=money(gross - cost), _net=gross - cost)
            active = trade
            trades.append(trade)
            day_trades.append(trade)
        settle(stamp(report['generated_at']))
        if active is not None:
            unresolved_prior = True
        skip_counts.update(skipped)
        days.append({'date': report['date'], 'entries': entries, 'skipped': dict(skipped),
            'closed_proxies': sum(t['status'] == 'closed_proxy' for t in day_trades),
            'unresolved': sum(t['status'] == 'unresolved' for t in day_trades),
            'known_proxy_net_pnl_dollars': money(equity - day_start),
            'proxy_net_pnl_dollars': None if unresolved_prior else money(equity - day_start)})
    closed = [t for t in trades if t['status'] == 'closed_proxy']
    known = equity - starting
    day_values = [d['proxy_net_pnl_dollars'] for d in days if d['proxy_net_pnl_dollars'] is not None]
    peak, curve, drawdown = starting, starting, Decimal(0)
    for t in closed:
        curve += t['_net']; peak = max(peak, curve); drawdown = max(drawdown, peak - curve)
    best = max((t['proxy_net_pnl_dollars'] for t in closed), default=None)
    best_day = max(day_values, default=None)
    for trade in trades:trade.pop('_net')
    return {'cost_bps': cost_bps, 'sessions': len(sessions), 'closed_proxies': len(closed),
        'unresolved': sum(t['status'] == 'unresolved' for t in trades),
        'proxy_net_pnl_dollars': None if unresolved_prior else money(known),
        'known_proxy_net_pnl_dollars': money(known),
        'proxy_return_on_initial_equity_pct': None if unresolved_prior else money(known / starting * 100),
        'positive_days': sum(v > 0 for v in day_values),
        'positive_day_fraction': sum(v > 0 for v in day_values) / len(sessions) if sessions else None,
        'largest_proxy_win_dollars': best,
        'largest_proxy_loss_dollars': min((t['proxy_net_pnl_dollars'] for t in closed), default=None),
        'net_without_best_proxy_dollars': money(known) - best if best is not None and not unresolved_prior else None,
        'net_without_best_day_dollars': money(known) - best_day if best_day is not None and not unresolved_prior else None,
        'realized_proxy_drawdown_dollars': money(drawdown), 'skipped': dict(skip_counts),
        'days': days, 'trades': trades}


def screen(scenarios, required_sessions, minimum_closed, policy):
    primary = scenarios[str(policy['primary_cost_bps'])]
    stress = scenarios[str(policy['stress_cost_bps'])]
    checks = {'enough_full_sessions': primary['sessions'] >= required_sessions,
        'enough_closed_proxies': primary['closed_proxies'] >= minimum_closed,
        'all_selected_outcomes_resolved': primary['unresolved'] == stress['unresolved'] == 0,
        'primary_net_positive': (primary['proxy_net_pnl_dollars'] or 0) > 0,
        'stress_net_nonnegative': stress['proxy_net_pnl_dollars'] is not None and stress['proxy_net_pnl_dollars'] >= 0,
        'positive_days': (primary['positive_day_fraction'] or 0) >= policy['minimum_positive_day_fraction'],
        'positive_without_best_day': (primary['net_without_best_day_dollars'] or 0) > 0}
    return {'passed': all(checks.values()), 'checks': checks}


def build_review(root: Path, evidence: Path, *, now=None):
    if evidence.resolve() == root.resolve() or root.resolve() in evidence.resolve().parents:
        raise EvidenceError('review_evidence_must_be_outside_source')
    baseline, policy = frozen_baseline(root), load_policy(root)
    sessions, invalid = [], []
    directory = evidence / 'equity_research'
    for path in sorted((directory / 'reports').glob('????-??-??.json')):
        try:
            if path.is_symlink():raise EvidenceError('report_symlink')
            sessions.append(load_session(path, evidence, baseline))
        except EvidenceError as exc:
            invalid.append({'date': path.stem, 'category': 'invalid', 'reasons': [str(exc)]})
        except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
            invalid.append({'date': path.stem, 'category': 'invalid', 'reasons': ['evidence_validation_failed']})
    full = [s for s in sessions if s.category == 'full_valid']
    dev = full[:policy['development_sessions']]
    holdout = full[policy['development_sessions']:policy['development_sessions'] + policy['holdout_sessions']]
    def scenarios(subset):return {str(c): replay(subset, policy, c) for c in policy['round_trip_cost_bps']}
    all_replay, dev_replay, holdout_replay = scenarios(full), scenarios(dev), scenarios(holdout)
    development = screen(dev_replay, policy['development_sessions'], policy['minimum_development_closed_proxies'], policy)
    validation = screen(holdout_replay, policy['holdout_sessions'], policy['minimum_holdout_closed_proxies'], policy)
    counts = Counter(s.category for s in sessions);counts['invalid'] += len(invalid)
    health_blocked = counts['invalid'] > 0
    if len(full) < 10:stage = 'collecting_to_10'
    elif len(full) < policy['development_sessions']:stage = 'collecting_to_20'
    elif not development['passed']:stage = 'development_screen_failed'
    elif len(holdout) < policy['holdout_sessions']:stage = 'collecting_frozen_holdout'
    elif not validation['passed']:stage = 'holdout_screen_failed'
    else:stage = 'candidate_for_manual_paper_review'
    if health_blocked:stage = 'evidence_review_required'
    daily = [{'date': s.report['date'], 'category': s.category, 'reasons': s.reasons,
        'code_sha': s.report['code_sha'], 'session_start': s.report.get('session_start'),
        'cycles': s.report.get('cycles'), 'eligible_signals': sum(x['prospective'] for x in s.report['signals']),
        'inputs_sha256': s.inputs} for s in sessions] + invalid
    daily.sort(key=lambda d: d['date'])
    # Identical inputs/policy must produce identical archives across EOD retries.
    input_time = max((stamp(s.report['generated_at']) for s in sessions),
        default=datetime.combine(date.fromisoformat(policy['declared_on']), time(), ET))
    result = {'review_schema_version': 1, 'protocol': PROTOCOL, 'baseline': baseline_identity(baseline),
        'policy': policy, 'generated_at': (now or input_time).isoformat(),
        'as_of_session': max((d['date'] for d in daily), default=None),
        'full_valid_sessions': len(full), 'partial_sessions': counts['partial'],
        'invalid_sessions': counts['invalid'], 'in_progress_sessions': counts['in_progress'],
        'eligible_signals_full_sessions': sum(s['prospective'] for day in full for s in day.report['signals']),
        'cycles_full_sessions': sum(s.report['cycles'] for s in full),
        'checkpoints': [{'full_sessions': n, 'reached': len(full) >= n} for n in policy['checkpoints_full_sessions']],
        'stage': stage, 'development_screen': development, 'holdout_screen': validation,
        'candidate_for_manual_paper_review': not health_blocked and development['passed'] and validation['passed'],
        'broker_entries_enabled': False, 'options_pnl': None, 'broker_realized_pnl': None,
        'sessions': daily, 'signal_diagnostics': signal_summary(full), 'portfolio_scenarios': all_replay,
        'development_portfolios': dev_replay, 'holdout_portfolios': holdout_replay,
        'development_dates': [s.report['date'] for s in dev], 'holdout_dates': [s.report['date'] for s in holdout],
        'limitations': [
            'Completed stock-bar entry/exit proxies with assumed costs; no fills or actual broker P&L.',
            'One long position, whole shares, no leverage, at most three entries per day. Shorts are excluded because availability is unverified.',
            'Exit is fixed at 30 minutes. No stop or profit target is inferred from intrabar price ordering.',
            'The daily realized-loss gate blocks subsequent entries; it is not a guaranteed loss cap.',
            'Pending slots remain reserved until exit evidence arrives. An unresolved selected outcome blocks subsequent replay exposure.',
            'Drawdown is based on realized proxy results and excludes unrealized intratrade losses.',
            'Signals/horizons overlap. Session counts represent market days, not independent trade samples.',
            'IEX covers one exchange; its volume, VWAP and signals can differ from consolidated data.',
            'First 20 full sessions are development; the next 10 are a disjoint frozen holdout. Checkpoints never enable orders.',
            'Five historical full sessions were known when this analysis policy was declared. Their replay is retrospective.',
            'Positive proxy results do not establish stock or options profitability or paper/live readiness.']}
    destination = directory / 'review'
    destination.mkdir(parents=True, exist_ok=True)
    # Same atomic-write convention as the collector, without importing its broker dependencies.
    raw = json.dumps(result, indent=2, allow_nan=False) + '\n'
    temp = destination / 'latest.json.tmp';temp.write_text(raw);temp.replace(destination / 'latest.json')
    temp = destination / 'latest.md.tmp';temp.write_text(markdown(result));temp.replace(destination / 'latest.md')
    return result


def markdown(review):
    lines = ['# Cumulative free-equity research review', '', f"As of session: {review['as_of_session']}",
        f"Full valid sessions: {review['full_valid_sessions']}; partial: {review['partial_sessions']}; invalid: {review['invalid_sessions']}",
        f"Eligible signals in full sessions: {review['eligible_signals_full_sessions']}",
        f"Stage: {review['stage']}", 'Order placement: disabled. Broker and options P&L: unavailable.', '',
        '| Strategy | Horizon | Outcomes | Mean gross bps | After assumed 10 bps |', '| --- | ---: | ---: | ---: | ---: |']
    for r in review['signal_diagnostics']:
        mean = r['mean_gross_bps']; net = r['cost_sensitivity_bps']['10']
        lines.append(f"| {r['strategy']} | {r['horizon_minutes']} | {r['observations']} | {None if mean is None else round(mean, 4)} | {None if net is None else round(net, 4)} |")
    lines += ['', '| Assumed cost bps | Closed proxies | Unresolved | Proxy net dollars |', '| ---: | ---: | ---: | ---: |']
    for r in review['portfolio_scenarios'].values():
        lines.append(f"| {r['cost_bps']} | {r['closed_proxies']} | {r['unresolved']} | {r['proxy_net_pnl_dollars']} |")
    lines += ['', '## Sessions', '', '| Date | Classification | Eligible signals |', '| --- | --- | ---: |']
    lines += [f"| {r['date']} | {r['category']} | {r.get('eligible_signals', 'Unavailable')} |" for r in review['sessions']]
    lines += ['', '## Limitations', ''] + ['- ' + text for text in review['limitations']]
    return '\n'.join(lines) + '\n'
