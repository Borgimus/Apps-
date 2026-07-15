import type { BacktestMetrics } from '../../types/ict'

interface MetricCardProps {
  label: string
  value: string
  sub?: string
  positive?: boolean
  negative?: boolean
  neutral?: boolean
}

function MetricCard({ label, value, sub, positive, negative, neutral }: MetricCardProps) {
  const valueColor = positive
    ? 'text-bull'
    : negative
    ? 'text-bear'
    : neutral
    ? 'text-text-primary'
    : 'text-text-primary'

  return (
    <div className="card">
      <div className="text-xs text-text-muted mb-1 uppercase tracking-wide">{label}</div>
      <div className={`text-2xl font-mono font-bold ${valueColor}`}>{value}</div>
      {sub && <div className="text-xs text-text-muted mt-1">{sub}</div>}
    </div>
  )
}

interface BacktestMetricsProps {
  metrics: BacktestMetrics
}

export default function BacktestMetricsPanel({ metrics }: BacktestMetricsProps) {
  const winRatePct = (metrics.win_rate * 100).toFixed(1)
  const longWinPct = (metrics.long_win_rate * 100).toFixed(1)
  const shortWinPct = (metrics.short_win_rate * 100).toFixed(1)
  const totalReturnPct = (metrics.total_return * 100).toFixed(2)
  const monthlyReturnPct = (metrics.monthly_return * 100).toFixed(2)
  const maxDrawdownPct = (metrics.max_drawdown * 100).toFixed(2)
  const durationHrs = (metrics.trade_duration_avg / 60).toFixed(1)

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">Performance Metrics</h3>

      {/* Primary metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
        <MetricCard
          label="Win Rate"
          value={`${winRatePct}%`}
          sub={`L: ${longWinPct}% / S: ${shortWinPct}%`}
          positive={metrics.win_rate >= 0.5}
          negative={metrics.win_rate < 0.4}
        />
        <MetricCard
          label="Profit Factor"
          value={metrics.profit_factor.toFixed(2)}
          sub="Gross profit / Gross loss"
          positive={metrics.profit_factor >= 1.5}
          negative={metrics.profit_factor < 1}
        />
        <MetricCard
          label="Avg R:R"
          value={`1:${metrics.avg_rr.toFixed(2)}`}
          sub="Average risk-to-reward"
          positive={metrics.avg_rr >= 1.5}
          negative={metrics.avg_rr < 1}
        />
        <MetricCard
          label="Expectancy"
          value={metrics.expectancy.toFixed(3)}
          sub="Expected value per trade"
          positive={metrics.expectancy > 0}
          negative={metrics.expectancy < 0}
        />
        <MetricCard
          label="Total Return"
          value={`${totalReturnPct}%`}
          sub="Over test period"
          positive={metrics.total_return > 0}
          negative={metrics.total_return < 0}
        />
        <MetricCard
          label="Monthly Return"
          value={`${monthlyReturnPct}%`}
          sub="Avg monthly"
          positive={metrics.monthly_return > 0}
          negative={metrics.monthly_return < 0}
        />
        <MetricCard
          label="Max Drawdown"
          value={`${maxDrawdownPct}%`}
          sub="Peak-to-trough"
          negative={metrics.max_drawdown > 0.15}
          positive={metrics.max_drawdown < 0.05}
        />
        <MetricCard
          label="Sharpe Ratio"
          value={metrics.sharpe_ratio.toFixed(2)}
          sub="Risk-adjusted return"
          positive={metrics.sharpe_ratio >= 1.5}
          negative={metrics.sharpe_ratio < 0.5}
          neutral={metrics.sharpe_ratio >= 0.5 && metrics.sharpe_ratio < 1.5}
        />
        <MetricCard
          label="Total Trades"
          value={String(metrics.total_trades)}
          sub={`Avg ${durationHrs}h duration`}
          neutral
        />
      </div>
    </div>
  )
}
