import { useState } from 'react'
import { useSignalStore } from '../../store/signalStore'

const SYMBOLS = ['SPY', 'QQQ', 'AAPL', 'TSLA', 'NVDA', 'BTCUSD', 'ETHUSD', 'EURUSD', 'GBPUSD', 'NQ', 'ES']
const TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1D']

interface TopBarProps {
  symbol: string
  setSymbol: (s: string) => void
  timeframe: string
  setTimeframe: (tf: string) => void
}

export default function TopBar({ symbol, setSymbol, timeframe, setTimeframe }: TopBarProps) {
  const { signals, wsConnected, lastUpdate } = useSignalStore()
  const [showSymbolMenu, setShowSymbolMenu] = useState(false)

  const activeSignals = signals.filter((s) => s.status === 'active')

  return (
    <header className="h-14 bg-bg-secondary border-b border-border flex items-center px-4 gap-4 shrink-0">
      {/* Symbol Selector */}
      <div className="relative">
        <button
          onClick={() => setShowSymbolMenu(!showSymbolMenu)}
          className="flex items-center gap-2 bg-bg-tertiary hover:bg-border border border-border rounded-md px-3 py-1.5 text-sm font-mono font-medium text-text-primary transition-colors"
        >
          {symbol}
          <svg className="w-3 h-3 text-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>
        {showSymbolMenu && (
          <div className="absolute top-full mt-1 left-0 bg-bg-secondary border border-border rounded-md shadow-xl z-50 min-w-[120px]">
            {SYMBOLS.map((s) => (
              <button
                key={s}
                onClick={() => { setSymbol(s); setShowSymbolMenu(false) }}
                className={`w-full text-left px-3 py-2 text-sm font-mono hover:bg-bg-tertiary transition-colors ${s === symbol ? 'text-accent' : 'text-text-primary'}`}
              >
                {s}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Timeframe Selector */}
      <div className="flex gap-1">
        {TIMEFRAMES.map((tf) => (
          <button
            key={tf}
            onClick={() => setTimeframe(tf)}
            className={`px-2 py-1 text-xs rounded transition-colors ${
              tf === timeframe
                ? 'bg-accent text-white'
                : 'text-text-muted hover:text-text-primary hover:bg-bg-tertiary'
            }`}
          >
            {tf}
          </button>
        ))}
      </div>

      <div className="flex-1" />

      {/* Active Signals Count */}
      {activeSignals.length > 0 && (
        <div className="flex items-center gap-1.5 bg-accent/10 border border-accent/30 rounded-md px-3 py-1">
          <div className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
          <span className="text-xs text-accent font-medium">{activeSignals.length} Active Signal{activeSignals.length !== 1 ? 's' : ''}</span>
        </div>
      )}

      {/* WS Status */}
      <div className="flex items-center gap-1.5">
        <div className={`w-2 h-2 rounded-full ${wsConnected ? 'bg-bull animate-pulse' : 'bg-bear'}`} />
        <span className="text-xs text-text-muted hidden sm:block">
          {wsConnected ? 'Live' : 'Offline'}
        </span>
      </div>

      {/* Last Update */}
      {lastUpdate && (
        <div className="text-xs text-text-muted hidden md:block">
          Updated {lastUpdate.toLocaleTimeString()}
        </div>
      )}
    </header>
  )
}
