import { useState } from 'react'
import { useSearchParams, useOutletContext } from 'react-router-dom'
import ICTChart from '../components/charts/ICTChart'
import SessionLevelsPanel from '../components/charts/SessionLevels'
import FVGOverlay from '../components/charts/FVGOverlay'
import MarketStructurePanel from '../components/charts/MarketStructure'
import TradeOverlay from '../components/charts/TradeOverlay'
import { useSessionLevels } from '../hooks/useSessionLevels'
import { useMarketStructure } from '../hooks/useMarketStructure'
import { useScanner } from '../hooks/useScanner'
import { useSignalStore } from '../store/signalStore'

interface OutletContext {
  symbol: string
  timeframe: string
}

const RIGHT_PANELS = ['Session Levels', 'FVG Zones', 'Market Structure', 'Trades'] as const
type RightPanel = typeof RIGHT_PANELS[number]

export default function ChartPage() {
  const { symbol } = useOutletContext<OutletContext>()
  const [searchParams] = useSearchParams()
  const urlSymbol = searchParams.get('symbol') || symbol
  const [activePanel, setActivePanel] = useState<RightPanel>('Session Levels')

  const { data: sessionLevels, isLoading: levelsLoading } = useSessionLevels(urlSymbol)
  const { data: marketStructure = [] } = useMarketStructure(urlSymbol)
  const { data: scannerData } = useScanner()
  const { signals } = useSignalStore()

  const symbolScanner = scannerData?.find((s) => s.symbol === urlSymbol)
  const fvgZones = symbolScanner?.active_fvgs ?? []
  const activeSignals = signals.filter((s) => s.symbol === urlSymbol)

  return (
    <div className="flex flex-col xl:flex-row gap-4 h-full min-h-0">
      {/* Chart - takes up most space */}
      <div className="flex-1 min-w-0">
        <div className="card p-0 overflow-hidden h-full">
          <div className="px-4 py-3 border-b border-border flex items-center justify-between">
            <h2 className="text-sm font-semibold text-text-primary">{urlSymbol} — Full Chart View</h2>
            <div className="flex items-center gap-2 text-xs text-text-muted">
              <span>{marketStructure.length} structure points</span>
              <span>•</span>
              <span>{fvgZones.filter((z) => !z.filled).length} active FVGs</span>
            </div>
          </div>
          <ICTChart
            symbol={urlSymbol}
            sessionLevels={sessionLevels}
            signals={activeSignals}
            fvgZones={fvgZones}
            marketStructure={marketStructure}
            height={580}
          />
        </div>
      </div>

      {/* Right Panel */}
      <div className="w-full xl:w-72 shrink-0">
        <div className="card p-0 overflow-hidden">
          {/* Panel Tabs */}
          <div className="flex border-b border-border overflow-x-auto">
            {RIGHT_PANELS.map((panel) => (
              <button
                key={panel}
                onClick={() => setActivePanel(panel)}
                className={`px-3 py-2.5 text-xs font-medium whitespace-nowrap transition-colors ${
                  activePanel === panel
                    ? 'text-accent border-b-2 border-accent bg-accent/5'
                    : 'text-text-muted hover:text-text-primary'
                }`}
              >
                {panel}
              </button>
            ))}
          </div>
          <div className="p-4">
            {activePanel === 'Session Levels' && (
              <SessionLevelsPanel levels={sessionLevels} isLoading={levelsLoading} />
            )}
            {activePanel === 'FVG Zones' && <FVGOverlay zones={fvgZones} maxItems={8} />}
            {activePanel === 'Market Structure' && (
              <MarketStructurePanel points={marketStructure} maxItems={10} />
            )}
            {activePanel === 'Trades' && <TradeOverlay signals={activeSignals} />}
          </div>
        </div>
      </div>
    </div>
  )
}
