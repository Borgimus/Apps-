import type { ScannerResult } from '../../types/ict'
import { DirectionBadge } from '../signals/SignalBadge'

interface ScannerRowProps {
  result: ScannerResult
  onChartClick?: (symbol: string) => void
}

function SessionStatus({ levels }: { levels: ScannerResult['session_levels'] }) {
  const hasAsian = levels.asian_high > 0 && levels.asian_low > 0
  const hasLondon = levels.london_high > 0 && levels.london_low > 0
  return (
    <div className="flex gap-1">
      <span className={`text-xs px-1.5 py-0.5 rounded ${hasAsian ? 'bg-asian/20 text-asian' : 'bg-bg-tertiary text-text-muted'}`}>
        AS
      </span>
      <span className={`text-xs px-1.5 py-0.5 rounded ${hasLondon ? 'bg-london/20 text-london' : 'bg-bg-tertiary text-text-muted'}`}>
        LN
      </span>
    </div>
  )
}

export default function ScannerRow({ result, onChartClick }: ScannerRowProps) {
  const confidencePct = Math.round(result.confidence * 100)
  const activeFvgCount = result.active_fvgs?.filter((f) => !f.filled).length ?? 0
  const hasSweep = result.signal !== null

  return (
    <tr className="border-b border-border hover:bg-bg-tertiary transition-colors">
      <td className="px-4 py-3">
        <span className="font-mono font-semibold text-sm text-text-primary">{result.symbol}</span>
      </td>
      <td className="px-4 py-3">
        <SessionStatus levels={result.session_levels} />
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-1.5">
          <div className={`w-2 h-2 rounded-full ${hasSweep ? 'bg-asian animate-pulse' : 'bg-bg-tertiary'}`} />
          <span className="text-xs text-text-secondary">{hasSweep ? 'Active' : 'None'}</span>
        </div>
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-1.5">
          {activeFvgCount > 0 ? (
            <>
              <span className="text-sm font-mono font-semibold text-accent">{activeFvgCount}</span>
              <div className="flex gap-0.5">
                {result.active_fvgs?.filter((f) => !f.filled).slice(0, 3).map((fvg, i) => (
                  <div
                    key={i}
                    className={`w-2 h-3 rounded-sm ${fvg.type === 'bullish' ? 'bg-bull/60' : 'bg-bear/60'}`}
                    title={`${fvg.type} FVG`}
                  />
                ))}
              </div>
            </>
          ) : (
            <span className="text-xs text-text-muted">—</span>
          )}
        </div>
      </td>
      <td className="px-4 py-3">
        <DirectionBadge direction={result.signal} />
      </td>
      <td className="px-4 py-3">
        {result.signal ? (
          <div className="flex items-center gap-2">
            <div className="w-16 h-1.5 bg-bg-tertiary rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${
                  confidencePct >= 75 ? 'bg-bull' : confidencePct >= 50 ? 'bg-asian' : 'bg-bear'
                }`}
                style={{ width: `${confidencePct}%` }}
              />
            </div>
            <span className="text-xs font-mono text-text-primary">{confidencePct}%</span>
          </div>
        ) : (
          <span className="text-xs text-text-muted">—</span>
        )}
      </td>
      <td className="px-4 py-3">
        <button
          onClick={() => onChartClick?.(result.symbol)}
          className="text-xs text-accent hover:text-indigo-400 transition-colors"
        >
          View Chart →
        </button>
      </td>
    </tr>
  )
}
