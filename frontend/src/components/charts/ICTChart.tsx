import { useEffect, useRef, useState, useCallback } from 'react'
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type Time,
  CrosshairMode,
  LineStyle,
} from 'lightweight-charts'
import type { SessionLevels, ICTSignal, FVGZone, MarketStructurePoint, Candle } from '../../types/ict'

interface LayerToggles {
  sessionLevels: boolean
  fvgZones: boolean
  signals: boolean
  marketStructure: boolean
}

interface ICTChartProps {
  symbol: string
  bars?: Candle[]
  sessionLevels?: SessionLevels | null
  signals?: ICTSignal[]
  fvgZones?: FVGZone[]
  marketStructure?: MarketStructurePoint[]
  height?: number
  isLoadingBars?: boolean
}

function generateMockCandles(count = 200): CandlestickData<Time>[] {
  const candles: CandlestickData<Time>[] = []
  let price = 1.08500
  const now = Math.floor(Date.now() / 1000)

  for (let i = count; i >= 0; i--) {
    const t = now - i * 60
    const open = price
    const change = (Math.random() - 0.495) * 0.00050
    const high = open + Math.random() * 0.00040
    const low = open - Math.random() * 0.00040
    const close = open + change
    price = close
    candles.push({
      time: t as Time,
      open,
      high: Math.max(high, open, close),
      low: Math.min(low, open, close),
      close,
    })
  }
  return candles
}

const CHART_COLORS = {
  background: '#0f1117',
  text: '#64748b',
  grid: '#1e2130',
  border: '#2a2d3e',
  upColor: '#26a69a',
  downColor: '#ef5350',
  wickUpColor: '#26a69a',
  wickDownColor: '#ef5350',
}

export default function ICTChart({
  symbol,
  bars,
  sessionLevels,
  signals = [],
  fvgZones = [],
  marketStructure = [],
  height = 500,
  isLoadingBars = false,
}: ICTChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const [layers, setLayers] = useState<LayerToggles>({
    sessionLevels: true,
    fvgZones: true,
    signals: true,
    marketStructure: false,
  })

  const toggleLayer = useCallback((key: keyof LayerToggles) => {
    setLayers((prev) => ({ ...prev, [key]: !prev[key] }))
  }, [])

  // Initialize chart
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height,
      layout: {
        background: { color: CHART_COLORS.background },
        textColor: CHART_COLORS.text,
      },
      grid: {
        vertLines: { color: CHART_COLORS.grid },
        horzLines: { color: CHART_COLORS.grid },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: {
        borderColor: CHART_COLORS.border,
        textColor: CHART_COLORS.text,
      },
      timeScale: {
        borderColor: CHART_COLORS.border,
        timeVisible: true,
        secondsVisible: false,
      },
    })

    const candleSeries = chart.addCandlestickSeries({
      upColor: CHART_COLORS.upColor,
      downColor: CHART_COLORS.downColor,
      borderUpColor: CHART_COLORS.upColor,
      borderDownColor: CHART_COLORS.downColor,
      wickUpColor: CHART_COLORS.wickUpColor,
      wickDownColor: CHART_COLORS.wickDownColor,
    })

    chartRef.current = chart
    seriesRef.current = candleSeries

    const resizeObserver = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth })
      }
    })
    if (containerRef.current) resizeObserver.observe(containerRef.current)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [height])

  // Load bar data (real or mock)
  useEffect(() => {
    if (!seriesRef.current) return
    const data: CandlestickData<Time>[] = bars && bars.length > 0
      ? bars.map((b) => ({ time: b.time as Time, open: b.open, high: b.high, low: b.low, close: b.close }))
      : generateMockCandles(300)
    seriesRef.current.setData(data)
    chartRef.current?.timeScale().fitContent()
  }, [bars])

  // Session levels overlay
  useEffect(() => {
    if (!chartRef.current || !sessionLevels || !layers.sessionLevels) return
    const chart = chartRef.current
    const lines: Array<ISeriesApi<'Line'>> = []

    const addLine = (price: number, color: string, title: string) => {
      if (!price || price === 0) return
      const s = chart.addLineSeries({
        color,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        title,
        lastValueVisible: true,
        priceLineVisible: false,
      })
      const nowNum = Math.floor(Date.now() / 1000)
      s.setData([
        { time: (nowNum - 3600) as Time, value: price },
        { time: nowNum as Time, value: price },
      ])
      lines.push(s)
    }

    addLine(sessionLevels.asian_high, '#f59e0b', 'AS H')
    addLine(sessionLevels.asian_low, '#f59e0b', 'AS L')
    addLine(sessionLevels.london_high, '#3b82f6', 'LN H')
    addLine(sessionLevels.london_low, '#3b82f6', 'LN L')

    return () => {
      lines.forEach((l) => { try { chart.removeSeries(l) } catch { /* ignore */ } })
    }
  }, [sessionLevels, layers.sessionLevels])

  // Signal markers
  useEffect(() => {
    if (!seriesRef.current || !signals.length || !layers.signals) return
    const markers = signals
      .filter((s) => s.entry_price > 0)
      .map((s) => ({
        time: Math.floor(new Date(s.timestamp).getTime() / 1000) as Time,
        position: s.direction === 'long' ? ('belowBar' as const) : ('aboveBar' as const),
        color: s.direction === 'long' ? '#26a69a' : '#ef5350',
        shape: s.direction === 'long' ? ('arrowUp' as const) : ('arrowDown' as const),
        text: `${s.symbol} ${s.direction.toUpperCase()}`,
        size: 2,
      }))
      .sort((a, b) => (a.time as number) - (b.time as number))

    seriesRef.current.setMarkers(markers)
    return () => { seriesRef.current?.setMarkers([]) }
  }, [signals, layers.signals])

  const activeSignal = signals.find((s) => s.status === 'active')
  const usingLiveData = bars && bars.length > 0

  return (
    <div className="flex flex-col gap-0">
      {/* Toggle Controls */}
      <div className="flex items-center gap-2 px-3 py-2 bg-bg-secondary border-b border-border flex-wrap">
        <span className="text-xs text-text-muted mr-1">Layers:</span>
        {(
          [
            ['sessionLevels', 'Sessions', '#f59e0b'],
            ['fvgZones', 'FVG Zones', '#6366f1'],
            ['signals', 'Signals', '#26a69a'],
            ['marketStructure', 'Structure', '#94a3b8'],
          ] as [keyof LayerToggles, string, string][]
        ).map(([key, label, color]) => (
          <button
            key={key}
            onClick={() => toggleLayer(key)}
            className={`flex items-center gap-1.5 px-2 py-1 rounded text-xs transition-colors border ${
              layers[key]
                ? 'bg-bg-tertiary border-border text-text-primary'
                : 'bg-transparent border-transparent text-text-muted'
            }`}
          >
            <div className="w-2 h-2 rounded-full" style={{ background: layers[key] ? color : '#2a2d3e' }} />
            {label}
          </button>
        ))}

        {/* Data source badge */}
        <div className={`ml-auto flex items-center gap-1.5 text-xs px-2 py-0.5 rounded ${
          isLoadingBars ? 'text-text-muted' : usingLiveData ? 'text-bull' : 'text-text-muted'
        }`}>
          <div className={`w-1.5 h-1.5 rounded-full ${usingLiveData ? 'bg-bull animate-pulse' : 'bg-text-muted'}`} />
          {isLoadingBars ? 'Loading…' : usingLiveData ? 'Live data' : 'Mock data'}
        </div>

        {/* Active signal info */}
        {activeSignal && (
          <div className="flex items-center gap-3 text-xs">
            <div className="flex items-center gap-1.5 px-2 py-1 bg-bg-tertiary rounded border border-border">
              <span className="text-text-muted">Entry:</span>
              <span className={`font-mono font-semibold ${activeSignal.direction === 'long' ? 'text-bull' : 'text-bear'}`}>
                {activeSignal.entry_price.toFixed(5)}
              </span>
            </div>
            <div className="flex items-center gap-1.5 px-2 py-1 bg-bg-tertiary rounded border border-border">
              <span className="text-text-muted">SL:</span>
              <span className="font-mono text-bear">{activeSignal.stop_loss.toFixed(5)}</span>
            </div>
            <div className="flex items-center gap-1.5 px-2 py-1 bg-bg-tertiary rounded border border-border">
              <span className="text-text-muted">TP:</span>
              <span className="font-mono text-bull">{activeSignal.take_profit.toFixed(5)}</span>
            </div>
          </div>
        )}
      </div>

      {/* Chart container */}
      <div ref={containerRef} style={{ height }} className="w-full" />

      {/* Legend */}
      {layers.sessionLevels && (
        <div className="flex items-center gap-4 px-3 py-1.5 bg-bg-secondary border-t border-border text-xs">
          <div className="flex items-center gap-1.5">
            <div className="w-4 h-0.5" style={{ borderTop: '1px dashed #f59e0b' }} />
            <span className="text-text-muted">Asian H/L</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-4 h-0.5" style={{ borderTop: '1px dashed #3b82f6' }} />
            <span className="text-text-muted">London H/L</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-2 h-2 rounded-sm bg-bull/40" />
            <span className="text-text-muted">Bullish FVG</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-2 h-2 rounded-sm bg-bear/40" />
            <span className="text-text-muted">Bearish FVG</span>
          </div>
        </div>
      )}
    </div>
  )
}
