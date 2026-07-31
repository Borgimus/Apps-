"""Metrics and result breakdowns on a known trade set."""
from src.backtest.engine import Trade
from src.backtest.metrics import compute_metrics
from src.backtest.reports import by_stop_width, stop_width_bucket


def mk(pnl, r, entry=100.0, stop=97.0, shares=100, holding=3, gap=False):
    t = Trade(symbol="X", entry_index=0, entry_date="d", entry_price=entry,
              initial_stop=stop, shares=shares)
    t.realized_pnl = pnl
    t.r_multiple = r
    t.holding_days = holding
    t.gap_loss = gap
    t.costs = 1.0
    return t


def test_metrics_on_two_winners_one_loser():
    trades = [mk(300, 3.0), mk(100, 1.0), mk(-100, -1.0, gap=True)]
    m = compute_metrics(trades, n_signals=5, starting_equity=10_000)
    assert m.n_trades == 3 and m.n_signals == 5
    assert m.win_rate == round(2 / 3, 4)
    assert m.expectancy_dollars == round((300 + 100 - 100) / 3, 4)
    assert m.expectancy_r == round((3 + 1 - 1) / 3, 4)
    assert m.profit_factor == round(400 / 100, 4)
    assert m.avg_winner == 200.0 and m.avg_loser == -100.0
    assert m.gap_losses == 1
    assert m.total_costs == 3.0


def test_max_drawdown():
    # equity path: +300, -100, -100, +50 -> peak 300, trough after two losses = 100 -> dd 200
    trades = [mk(300, 3), mk(-100, -1), mk(-100, -1), mk(50, 0.5)]
    m = compute_metrics(trades, starting_equity=0.0)
    assert m.max_drawdown == 200.0


def test_no_trades_returns_zeroed_metrics():
    m = compute_metrics([], n_signals=4)
    assert m.n_trades == 0 and m.profit_factor is None and m.win_rate == 0.0


def test_stop_width_bucketing():
    assert stop_width_bucket(mk(0, 0, entry=100, stop=99)) == "0-2%"   # 1%
    assert stop_width_bucket(mk(0, 0, entry=100, stop=96)) == "2-5%"   # 4%
    buckets = by_stop_width([mk(10, 1, entry=100, stop=99), mk(-5, -1, entry=100, stop=90)])
    assert set(buckets) == {"0-2%", "5-10%"}
