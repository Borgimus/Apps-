// MarketStructure — renders MSS/BOS/CHOCH info list
import type { MarketStructurePoint } from '../../types/ict'

interface MarketStructureProps {
  points: MarketStructurePoint[]
  maxItems?: number
}

const TYPE_CONFIG: Record<string, { label: string; color: string }> = {
  swing_high: { label: 'Swing High', color: 'text-text-secondary' },
  swing_low: { label: 'Swing Low', color: 'text-text-secondary' },
  bos_bullish: { label: 'BOS ↑', color: 'text-bull' },
  bos_bearish: { label: 'BOS ↓', color: 'text-bear' },
  choch_bullish: { label: 'CHoCH ↑', color: 'text-bull' },
  choch_bearish: { label: 'CHoCH ↓', color: 'text-bear' },
}

function formatPrice(p: number): string {
  return p > 100 ? p.toFixed(2) : p.toFixed(5)
}

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return ts
  }
}

export default function MarketStructurePanel({ points, maxItems = 8 }: MarketStructureProps) {
  const recent = [...points]
    .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
    .slice(0, maxItems)

  if (recent.length === 0) {
    return <div className="text-xs text-text-muted py-2 text-center">No market structure data</div>
  }

  return (
    <div className="space-y-1">
      {recent.map((pt, i) => {
        const cfg = TYPE_CONFIG[pt.type] ?? { label: pt.type, color: 'text-text-muted' }
        return (
          <div key={i} className="flex items-center justify-between text-xs py-1 border-b border-border/50">
            <span className={`font-medium ${cfg.color}`}>{cfg.label}</span>
            <span className="font-mono text-text-secondary">{formatPrice(pt.price)}</span>
            <span className="text-text-muted">{formatTime(pt.timestamp)}</span>
          </div>
        )
      })}
    </div>
  )
}
