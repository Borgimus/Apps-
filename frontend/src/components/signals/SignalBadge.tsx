import type { Direction, SignalStatus } from '../../types/ict'

interface SignalBadgeProps {
  direction?: Direction | null
  status?: SignalStatus
  className?: string
}

export function DirectionBadge({ direction, className = '' }: { direction: Direction | null; className?: string }) {
  if (!direction) return <span className="text-text-muted text-xs">—</span>
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium font-mono ${
        direction === 'long'
          ? 'bg-bull/20 text-bull border border-bull/30'
          : 'bg-bear/20 text-bear border border-bear/30'
      } ${className}`}
    >
      {direction === 'long' ? '▲' : '▼'} {direction.toUpperCase()}
    </span>
  )
}

export function StatusBadge({ status, className = '' }: { status: SignalStatus; className?: string }) {
  const statusConfig: Record<SignalStatus, { color: string; label: string }> = {
    active: { color: 'bg-bull/20 text-bull border-bull/30', label: 'Active' },
    pending: { color: 'bg-asian/20 text-asian border-asian/30', label: 'Pending' },
    filled: { color: 'bg-accent/20 text-accent border-accent/30', label: 'Filled' },
    stopped: { color: 'bg-bear/20 text-bear border-bear/30', label: 'Stopped' },
    target_hit: { color: 'bg-bull/20 text-bull border-bull/30', label: 'Target Hit' },
    cancelled: { color: 'bg-text-muted/20 text-text-muted border-text-muted/30', label: 'Cancelled' },
  }
  const cfg = statusConfig[status]
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${cfg.color} ${className}`}>
      {cfg.label}
    </span>
  )
}

export default function SignalBadge({ direction, status, className = '' }: SignalBadgeProps) {
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      {direction != null && <DirectionBadge direction={direction} />}
      {status && <StatusBadge status={status} />}
    </div>
  )
}
