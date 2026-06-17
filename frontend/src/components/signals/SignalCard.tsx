import type { ICTSignal } from '../../types/ict'
import { DirectionBadge, StatusBadge } from './SignalBadge'

interface SignalCardProps {
  signal: ICTSignal
  compact?: boolean
}

function formatPrice(price: number): string {
  if (price > 100) return price.toFixed(2)
  return price.toFixed(5)
}

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return ts
  }
}

function getRR(signal: ICTSignal): string {
  const risk = Math.abs(signal.entry_price - signal.stop_loss)
  const reward = Math.abs(signal.take_profit - signal.entry_price)
  if (risk === 0) return '—'
  return (reward / risk).toFixed(1)
}

export default function SignalCard({ signal, compact = false }: SignalCardProps) {
  const isLong = signal.direction === 'long'
  const borderColor = isLong ? 'border-bull/40' : 'border-bear/40'
  const accentColor = isLong ? 'text-bull' : 'text-bear'
  const rr = getRR(signal)
  const confidencePct = Math.round(signal.confidence * 100)

  if (compact) {
    return (
      <div className={`bg-bg-secondary border ${borderColor} rounded-md p-3 hover:bg-bg-tertiary transition-colors`}>
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-semibold text-text-primary">{signal.symbol}</span>
            <DirectionBadge direction={signal.direction} />
          </div>
          <StatusBadge status={signal.status} />
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <div>
            <div className="text-text-muted">Entry</div>
            <div className={`font-mono font-medium ${accentColor}`}>{formatPrice(signal.entry_price)}</div>
          </div>
          <div>
            <div className="text-text-muted">R:R</div>
            <div className="font-mono font-medium text-text-primary">1:{rr}</div>
          </div>
          <div>
            <div className="text-text-muted">Conf</div>
            <div className="font-mono font-medium text-text-primary">{confidencePct}%</div>
          </div>
        </div>
        <div className="mt-2 text-xs text-text-muted">{formatTime(signal.timestamp)}</div>
      </div>
    )
  }

  return (
    <div className={`bg-bg-secondary border-l-2 ${borderColor} border border-border rounded-md p-4`}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="font-mono text-base font-bold text-text-primary">{signal.symbol}</span>
          <DirectionBadge direction={signal.direction} />
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={signal.status} />
          <span className="text-xs text-text-muted">{formatTime(signal.timestamp)}</span>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
        <div>
          <div className="text-xs text-text-muted mb-0.5">Entry</div>
          <div className={`font-mono text-sm font-semibold ${accentColor}`}>{formatPrice(signal.entry_price)}</div>
        </div>
        <div>
          <div className="text-xs text-text-muted mb-0.5">Stop Loss</div>
          <div className="font-mono text-sm font-semibold text-bear">{formatPrice(signal.stop_loss)}</div>
        </div>
        <div>
          <div className="text-xs text-text-muted mb-0.5">Take Profit</div>
          <div className="font-mono text-sm font-semibold text-bull">{formatPrice(signal.take_profit)}</div>
        </div>
        <div>
          <div className="text-xs text-text-muted mb-0.5">R:R Ratio</div>
          <div className="font-mono text-sm font-semibold text-text-primary">1:{rr}</div>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <div>
          <div className="text-xs text-text-muted mb-0.5">FVG Zone</div>
          <div className="font-mono text-xs text-text-secondary">
            {formatPrice(signal.fvg_lower)} — {formatPrice(signal.fvg_upper)}
          </div>
        </div>
        <div>
          <div className="text-xs text-text-muted mb-0.5">Sweep</div>
          <div className="text-xs text-text-secondary capitalize">{signal.sweep_type.replace(/_/g, ' ')}</div>
        </div>
        <div>
          <div className="text-xs text-text-muted mb-0.5">Confidence</div>
          <div className="flex items-center gap-2">
            <div className="flex-1 h-1.5 bg-bg-tertiary rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${confidencePct >= 75 ? 'bg-bull' : confidencePct >= 50 ? 'bg-asian' : 'bg-bear'}`}
                style={{ width: `${confidencePct}%` }}
              />
            </div>
            <span className="text-xs font-mono text-text-primary">{confidencePct}%</span>
          </div>
        </div>
      </div>
    </div>
  )
}
