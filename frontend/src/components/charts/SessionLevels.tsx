// SessionLevels component — renders session info cards outside the chart
import type { SessionLevels } from '../../types/ict'

interface SessionLevelsProps {
  levels: SessionLevels | null | undefined
  isLoading?: boolean
}

function formatPrice(p: number): string {
  if (!p) return '—'
  return p > 100 ? p.toFixed(2) : p.toFixed(5)
}

function LevelRow({
  label,
  high,
  low,
  colorClass,
}: {
  label: string
  high: number
  low: number
  colorClass: string
}) {
  const range = (high - low)
  const rangePips = (range * 10000).toFixed(1)

  return (
    <div className={`p-3 rounded-md border ${colorClass}`}>
      <div className="text-xs font-medium mb-2 uppercase tracking-wide opacity-80">{label}</div>
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div>
          <div className="opacity-60 mb-0.5">High</div>
          <div className="font-mono font-semibold">{formatPrice(high)}</div>
        </div>
        <div>
          <div className="opacity-60 mb-0.5">Low</div>
          <div className="font-mono font-semibold">{formatPrice(low)}</div>
        </div>
        <div>
          <div className="opacity-60 mb-0.5">Range</div>
          <div className="font-mono font-semibold">{high > 0 ? `${rangePips}p` : '—'}</div>
        </div>
      </div>
    </div>
  )
}

export default function SessionLevelsPanel({ levels, isLoading }: SessionLevelsProps) {
  if (isLoading) {
    return (
      <div className="grid grid-cols-2 gap-2">
        <div className="h-20 skeleton rounded-md" />
        <div className="h-20 skeleton rounded-md" />
      </div>
    )
  }

  if (!levels) {
    return (
      <div className="text-center py-4 text-text-muted text-xs">
        Session levels unavailable
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <LevelRow
        label="Asian Session"
        high={levels.asian_high}
        low={levels.asian_low}
        colorClass="border-asian/30 bg-asian/5 text-asian"
      />
      <LevelRow
        label="London Session"
        high={levels.london_high}
        low={levels.london_low}
        colorClass="border-london/30 bg-london/5 text-london"
      />
    </div>
  )
}
