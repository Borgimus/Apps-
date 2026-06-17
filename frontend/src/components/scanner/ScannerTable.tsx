import { useNavigate } from 'react-router-dom'
import { useScanner } from '../../hooks/useScanner'
import ScannerRow from './ScannerRow'

export default function ScannerTable() {
  const navigate = useNavigate()
  const { data, isLoading, isError, dataUpdatedAt } = useScanner()

  const handleChartClick = (symbol: string) => {
    navigate(`/chart?symbol=${symbol}`)
  }

  if (isLoading) {
    return (
      <div className="space-y-2">
        {[...Array(8)].map((_, i) => (
          <div key={i} className="h-12 skeleton rounded" />
        ))}
      </div>
    )
  }

  if (isError) {
    return (
      <div className="text-center py-12">
        <div className="text-bear text-sm mb-2">Failed to load scanner data</div>
        <div className="text-text-muted text-xs">Check backend connection</div>
      </div>
    )
  }

  const results = data ?? []
  const withSignals = results.filter((r) => r.signal !== null)

  return (
    <div>
      {/* Summary */}
      <div className="flex items-center gap-4 mb-4">
        <div className="text-sm text-text-secondary">
          <span className="font-semibold text-text-primary">{results.length}</span> symbols scanned
        </div>
        <div className="text-sm text-text-secondary">
          <span className="font-semibold text-bull">{withSignals.length}</span> with signals
        </div>
        {dataUpdatedAt > 0 && (
          <div className="text-xs text-text-muted ml-auto">
            Updated {new Date(dataUpdatedAt).toLocaleTimeString()}
          </div>
        )}
      </div>

      {results.length === 0 ? (
        <div className="text-center py-16 text-text-muted text-sm">No scanner results</div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full">
            <thead>
              <tr className="bg-bg-tertiary border-b border-border">
                <th className="text-left px-4 py-3 text-xs font-medium text-text-secondary uppercase tracking-wide">Symbol</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-text-secondary uppercase tracking-wide">Session</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-text-secondary uppercase tracking-wide">Sweep</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-text-secondary uppercase tracking-wide">FVGs</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-text-secondary uppercase tracking-wide">Signal</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-text-secondary uppercase tracking-wide">Confidence</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-text-secondary uppercase tracking-wide">Action</th>
              </tr>
            </thead>
            <tbody>
              {results.map((result) => (
                <ScannerRow key={result.symbol} result={result} onChartClick={handleChartClick} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
