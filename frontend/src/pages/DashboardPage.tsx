import { useOutletContext } from 'react-router-dom'
import ICTChart from '../components/charts/ICTChart'
import SignalList from '../components/signals/SignalList'
import SessionLevelsPanel from '../components/charts/SessionLevels'
import FVGOverlay from '../components/charts/FVGOverlay'
import { useSessionLevels } from '../hooks/useSessionLevels'
import { useICTSignals } from '../hooks/useICTSignals'
import { useScanner } from '../hooks/useScanner'
import { useSignalStore } from '../store/signalStore'
import { DirectionBadge } from '../components/signals/SignalBadge'

interface OutletContext {
  symbol: string
  timeframe: string
}

export default function DashboardPage() {
  const { symbol } = useOutletContext<OutletContext>()
  const { data: sessionLevels, isLoading: levelsLoading } = useSessionLevels(symbol)
  const { isLoading: signalsLoading } = useICTSignals()
  const { data: scannerData } = useScanner()
  const { signals } = useSignalStore()

  const activeSignals = signals.filter((s) => s.status === 'active' || s.status === 'pending')
  const symbolScanner = scannerData?.find((s) => s.symbol === symbol)
  const fvgZones = symbolScanner?.active_fvgs ?? []
  const topOpportunities = scannerData?.filter((s) => s.signal !== null).slice(0, 3) ?? []

  return (
    <div className="flex flex-col lg:flex-row gap-4 h-full min-h-0">
      {/* Left: Chart + Session Info */}
      <div className="flex-1 min-w-0 space-y-4">
        {/* Chart */}
        <div className="card p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-border flex items-center justify-between">
            <h2 className="text-sm font-semibold text-text-primary">{symbol} — Live Chart</h2>
            <div className="flex items-center gap-2 text-xs text-text-muted">
              <div className="w-1.5 h-1.5 rounded-full bg-bull animate-pulse" />
              1m Candlesticks
            </div>
          </div>
          <ICTChart
            symbol={symbol}
            sessionLevels={sessionLevels}
            signals={activeSignals}
            fvgZones={fvgZones}
            height={420}
          />
        </div>

        {/* Bottom row: Session Levels + FVG Zones + Scanner Opportunities */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="card">
            <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wide mb-3">Session Levels</h3>
            <SessionLevelsPanel levels={sessionLevels} isLoading={levelsLoading} />
          </div>
          <div className="card">
            <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wide mb-3">Active FVGs</h3>
            <FVGOverlay zones={fvgZones} />
          </div>
          <div className="card">
            <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wide mb-3">Top Setups</h3>
            {topOpportunities.length > 0 ? (
              <div className="space-y-2">
                {topOpportunities.map((opp) => (
                  <div key={opp.symbol} className="flex items-center justify-between text-xs">
                    <span className="font-mono font-semibold text-text-primary">{opp.symbol}</span>
                    <DirectionBadge direction={opp.signal} />
                    <span className="font-mono text-text-muted">
                      {Math.round(opp.confidence * 100)}%
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-xs text-text-muted text-center py-4">No setups detected</div>
            )}
          </div>
        </div>
      </div>

      {/* Right: Signal List */}
      <div className="w-full lg:w-80 xl:w-96 shrink-0 space-y-4">
        {/* Active Signals */}
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-text-primary">Live Signals</h2>
            <span className="text-xs text-text-muted bg-bg-tertiary px-2 py-0.5 rounded-full">
              {activeSignals.length} active
            </span>
          </div>
          {signalsLoading ? (
            <div className="space-y-2">
              {[...Array(3)].map((_, i) => (
                <div key={i} className="h-24 skeleton rounded-md" />
              ))}
            </div>
          ) : (
            <SignalList compact maxItems={8} filterStatus={['active', 'pending']} />
          )}
        </div>

        {/* Recent Signals */}
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-text-primary">Recent History</h2>
          </div>
          <SignalList compact maxItems={5} filterStatus={['filled', 'stopped', 'target_hit']} />
        </div>
      </div>
    </div>
  )
}
