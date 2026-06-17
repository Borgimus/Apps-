import type { StrategyConfig } from '../../types/ict'

interface RiskConfigProps {
  config: StrategyConfig
  onChange: (updates: Partial<StrategyConfig>) => void
}

export default function RiskConfig({ config, onChange }: RiskConfigProps) {
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-4">Position Sizing</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label block mb-1">Risk Per Trade (%)</label>
            <input
              type="number"
              step="0.1"
              min="0.1"
              max="10"
              value={config.risk_per_trade}
              onChange={(e) => onChange({ risk_per_trade: parseFloat(e.target.value) })}
              className="input"
            />
            <p className="text-xs text-text-muted mt-1">Percentage of account to risk per trade</p>
          </div>
          <div>
            <label className="label block mb-1">Max Concurrent Trades</label>
            <input
              type="number"
              step="1"
              min="1"
              max="20"
              value={config.max_concurrent_trades}
              onChange={(e) => onChange({ max_concurrent_trades: parseInt(e.target.value) })}
              className="input"
            />
          </div>
        </div>
      </div>

      <div>
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-4">Trade Limits</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label block mb-1">Max Daily Trades</label>
            <input
              type="number"
              step="1"
              min="1"
              max="50"
              value={config.max_daily_trades}
              onChange={(e) => onChange({ max_daily_trades: parseInt(e.target.value) })}
              className="input"
            />
          </div>
        </div>
      </div>

      <div className="card bg-bear/5 border-bear/20">
        <h4 className="text-xs font-semibold text-bear uppercase tracking-wide mb-2">Risk Warning</h4>
        <p className="text-xs text-text-muted">
          ICT strategies involve significant risk. Liquidity sweeps and FVG reversals can fail in trending markets.
          Always use proper risk management. Past backtest performance does not guarantee future results.
        </p>
      </div>
    </div>
  )
}
