import { useSignalStore } from '../../store/signalStore'
import SignalCard from './SignalCard'
import type { SignalStatus } from '../../types/ict'

interface SignalListProps {
  maxItems?: number
  compact?: boolean
  filterStatus?: SignalStatus[]
}

export default function SignalList({ maxItems = 20, compact = false, filterStatus }: SignalListProps) {
  const { signals } = useSignalStore()

  const filtered = filterStatus
    ? signals.filter((s) => filterStatus.includes(s.status))
    : signals

  const displayed = filtered.slice(0, maxItems)

  if (displayed.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center">
        <div className="text-3xl mb-3">📡</div>
        <div className="text-sm text-text-secondary">No signals yet</div>
        <div className="text-xs text-text-muted mt-1">Waiting for ICT setups...</div>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {displayed.map((signal) => (
        <SignalCard key={signal.id} signal={signal} compact={compact} />
      ))}
      {filtered.length > maxItems && (
        <div className="text-center text-xs text-text-muted py-2">
          +{filtered.length - maxItems} more signals
        </div>
      )}
    </div>
  )
}
