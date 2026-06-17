// TradeOverlay — shows active trade info panel (chart lines are in ICTChart)
import type { ICTSignal } from '../../types/ict'

interface TradeOverlayProps {
  signals: ICTSignal[]
}

function formatPrice(p: number): string {
  return p > 100 ? p.toFixed(2) : p.toFixed(5)
}

export default function TradeOverlay({ signals }: TradeOverlayProps) {
  const activeSignals = signals.filter((s) => s.status === 'active' || s.status === 'filled')

  if (activeSignals.length === 0) {
    return <div className="text-xs text-text-muted py-2 text-center">No active trades</div>
  }

  return (
    <div className="space-y-2">
      {activeSignals.map((signal) => {
        const isLong = signal.direction === 'long'
        const risk = Math.abs(signal.entry_price - signal.stop_loss)
        const reward = Math.abs(signal.take_profit - signal.entry_price)
        const rr = risk > 0 ? (reward / risk).toFixed(1) : '—'

        return (
          <div
            key={signal.id}
            className={`rounded-md border p-3 ${
              isLong ? 'border-bull/30 bg-bull/5' : 'border-bear/30 bg-bear/5'
            }`}
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold text-text-primary">{signal.symbol}</span>
              <span className={`text-xs font-medium ${isLong ? 'text-bull' : 'text-bear'}`}>
                {isLong ? '▲ LONG' : '▼ SHORT'}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-1 text-xs">
              <div className="text-center">
                <div className="text-text-muted mb-0.5">Entry</div>
                <div className={`font-mono font-semibold ${isLong ? 'text-bull' : 'text-bear'}`}>
                  {formatPrice(signal.entry_price)}
                </div>
              </div>
              <div className="text-center">
                <div className="text-text-muted mb-0.5">Stop</div>
                <div className="font-mono font-semibold text-bear">{formatPrice(signal.stop_loss)}</div>
              </div>
              <div className="text-center">
                <div className="text-text-muted mb-0.5">Target</div>
                <div className="font-mono font-semibold text-bull">{formatPrice(signal.take_profit)}</div>
              </div>
            </div>
            <div className="mt-2 flex items-center justify-between text-xs text-text-muted">
              <span>R:R 1:{rr}</span>
              <span className="capitalize">{signal.sweep_type.replace(/_/g, ' ')}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}
