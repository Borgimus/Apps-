import { useScanner } from '../hooks/useScanner'
import ScannerTable from '../components/scanner/ScannerTable'

export default function ScannerPage() {
  const { refetch, isFetching, dataUpdatedAt } = useScanner()

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-text-primary">Multi-Symbol Scanner</h1>
          <p className="text-sm text-text-muted mt-0.5">
            Real-time ICT setup detection across all tracked symbols
          </p>
        </div>
        <div className="flex items-center gap-3">
          {dataUpdatedAt > 0 && (
            <span className="text-xs text-text-muted hidden sm:block">
              Last scan: {new Date(dataUpdatedAt).toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="btn-secondary flex items-center gap-2 text-xs"
          >
            {isFetching ? (
              <>
                <div className="w-3 h-3 border border-text-muted border-t-transparent rounded-full animate-spin" />
                Scanning...
              </>
            ) : (
              <>
                <span>↺</span>
                Refresh
              </>
            )}
          </button>
        </div>
      </div>

      {/* Legend */}
      <div className="card">
        <div className="flex flex-wrap gap-4 text-xs">
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded-full bg-asian/40 border border-asian" />
            <span className="text-text-muted">AS = Asian Session</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded-full bg-london/40 border border-london" />
            <span className="text-text-muted">LN = London Session</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded-sm bg-bull/40 border border-bull/60" />
            <span className="text-text-muted">Bullish FVG</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded-sm bg-bear/40 border border-bear/60" />
            <span className="text-text-muted">Bearish FVG</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-2 h-2 rounded-full bg-asian animate-pulse" />
            <span className="text-text-muted">Active Sweep</span>
          </div>
        </div>
      </div>

      {/* Scanner Table */}
      <div className="card p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <h2 className="text-sm font-semibold text-text-primary">Scanner Results</h2>
          <p className="text-xs text-text-muted mt-0.5">Auto-refreshes every 30 seconds</p>
        </div>
        <div className="p-4">
          <ScannerTable />
        </div>
      </div>
    </div>
  )
}
