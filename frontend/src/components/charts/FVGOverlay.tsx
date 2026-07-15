// FVGOverlay — renders FVG zone info cards (chart overlay is in ICTChart)
import type { FVGZone } from '../../types/ict'

interface FVGOverlayProps {
  zones: FVGZone[]
  maxItems?: number
}

function formatPrice(p: number): string {
  return p > 100 ? p.toFixed(2) : p.toFixed(5)
}

export default function FVGOverlay({ zones, maxItems = 5 }: FVGOverlayProps) {
  const active = zones.filter((z) => !z.filled).slice(0, maxItems)

  if (active.length === 0) {
    return <div className="text-xs text-text-muted py-2 text-center">No active FVG zones</div>
  }

  return (
    <div className="space-y-1.5">
      {active.map((zone, i) => {
        const size = ((zone.upper - zone.lower) * 10000).toFixed(1)
        const isBull = zone.type === 'bullish'
        return (
          <div
            key={i}
            className={`flex items-center justify-between px-3 py-2 rounded border text-xs ${
              isBull
                ? 'bg-bull/5 border-bull/20 text-bull'
                : 'bg-bear/5 border-bear/20 text-bear'
            }`}
          >
            <span className="font-medium">{isBull ? '▲ Bull FVG' : '▼ Bear FVG'}</span>
            <span className="font-mono">
              {formatPrice(zone.lower)} — {formatPrice(zone.upper)}
            </span>
            <span className="text-text-muted">{size}p</span>
          </div>
        )
      })}
    </div>
  )
}
