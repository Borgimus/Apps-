"""Pure exit policy shared by broker positions and shadow evaluation."""

import os
from datetime import datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def trailing_activation_setting(position_settings) -> float:
    value = getattr(position_settings, "trailing_activation_pct", 0.25)
    if not isinstance(value, (int, float, str)):
        value = 0.25
    return max(0.0, float(os.getenv("POSITION_TRAILING_ACTIVATION_PCT", str(value))))


def exit_reason(*, entry_price: float, current_price: float, peak_price: float,
                entry_time: datetime, now: datetime, stop_loss_pct: float,
                take_profit_pct: float, trailing_stop_pct: float,
                trailing_stop_armed: bool, max_hold_minutes: int,
                eod_exit_time: time) -> str | None:
    """Preserve PositionManager's price, duration, then Eastern-time precedence."""
    if current_price <= entry_price * (1.0 - stop_loss_pct):
        return "stop_loss"
    if current_price >= entry_price * (1.0 + take_profit_pct):
        return "take_profit"
    if trailing_stop_armed and current_price <= peak_price * (1.0 - trailing_stop_pct):
        return "trailing_stop"
    now_et = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
    entry_et = entry_time.astimezone(ET) if entry_time.tzinfo else entry_time.replace(tzinfo=ET)
    if (now_et - entry_et).total_seconds() / 60.0 >= max_hold_minutes:
        return "max_hold"
    if now_et.time() >= eod_exit_time:
        return "eod_exit"
    return None
