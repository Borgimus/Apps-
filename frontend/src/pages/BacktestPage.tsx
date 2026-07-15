import { useState, useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import BacktestForm from '../components/backtest/BacktestForm'
import BacktestMetricsPanel from '../components/backtest/BacktestMetrics'
import EquityCurve from '../components/backtest/EquityCurve'
import MonthlyReturns from '../components/backtest/MonthlyReturns'
import TradeHistoryTable from '../components/backtest/TradeHistoryTable'
import { ictApi } from '../api/ict'
import type { BacktestResults, BacktestMetrics, MonthlyReturn } from '../types/ict'

function ProgressBar({ progress }: { progress: number }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-sm">
        <span className="text-text-secondary">Running backtest...</span>
        <span className="font-mono text-accent">{Math.round(progress * 100)}%</span>
      </div>
      <div className="w-full h-2 bg-bg-tertiary rounded-full overflow-hidden">
        <div
          className="h-full bg-accent rounded-full transition-all duration-300"
          style={{ width: `${progress * 100}%` }}
        />
      </div>
    </div>
  )
}

export default function BacktestPage() {
  const [taskId, setTaskId] = useState<string | null>(null)
  const [results, setResults] = useState<BacktestResults | null>(null)
  const [polling, setPolling] = useState(false)

  const { data: backtestData } = useQuery({
    queryKey: ['backtest', taskId],
    queryFn: () => ictApi.getBacktestResult(taskId!),
    enabled: !!taskId && polling,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (!status) return 2000
      if (status === 'completed' || status === 'failed') return false
      return 2000
    },
  })

  useEffect(() => {
    if (backtestData?.status === 'completed') {
      setResults(backtestData)
      setPolling(false)
    } else if (backtestData?.status === 'failed') {
      setPolling(false)
    }
  }, [backtestData])

  const handleTaskCreated = (id: string) => {
    setTaskId(id)
    setResults(null)
    setPolling(true)
  }

  const isRunning = polling && backtestData?.status === 'running'
  const hasFailed = backtestData?.status === 'failed'

  // Adapt flat results into BacktestMetrics shape for the metrics panel
  const metrics = useMemo<BacktestMetrics | null>(() => {
    if (!results) return null
    return {
      win_rate: results.win_rate,
      avg_rr: results.avg_rr,
      profit_factor: results.profit_factor,
      expectancy: results.expectancy,
      max_drawdown: results.max_drawdown,
      total_return: results.total_return,
      monthly_return: results.monthly_return,
      sharpe_ratio: results.sharpe_ratio,
      total_trades: results.total_trades,
      winning_trades: results.winning_trades,
      losing_trades: results.losing_trades,
      long_win_rate: results.long_win_rate,
      short_win_rate: results.short_win_rate,
      total_pnl: results.total_pnl,
      trade_duration_avg: results.trade_duration_avg ?? 0,
      monthly_pnl: results.monthly_pnl,
    }
  }, [results])

  // Transform monthly_pnl dict {YYYY-MM: dollars} → MonthlyReturn[] for chart
  const monthlyReturns = useMemo<MonthlyReturn[]>(() => {
    if (!results?.monthly_pnl) return []
    const pnl = results.monthly_pnl
    return Object.entries(pnl)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([month, dollars]) => ({
        month,
        return: dollars / 100000,
        trades: results.trades.filter((t) => t.entry_time.startsWith(month)).length,
      }))
  }, [results])

  // Build equity curve from trade list
  const equityCurve = useMemo(() => {
    if (!results?.trades.length) return []
    let equity = 100000
    let peak = equity
    return results.trades.map((t) => {
      equity += t.pnl
      if (equity > peak) peak = equity
      return {
        date: t.entry_time.slice(0, 10),
        equity,
        drawdown: equity < peak ? (equity - peak) / peak : 0,
      }
    })
  }, [results])

  return (
    <div className="flex flex-col lg:flex-row gap-4 min-h-0">
      {/* Left: Form */}
      <div className="w-full lg:w-72 xl:w-80 shrink-0">
        <div className="card sticky top-0">
          <h2 className="text-sm font-semibold text-text-primary mb-4">Backtest Configuration</h2>
          <BacktestForm onTaskCreated={handleTaskCreated} />
        </div>
      </div>

      {/* Right: Results */}
      <div className="flex-1 min-w-0 space-y-4">
        {!taskId && !results && (
          <div className="card flex flex-col items-center justify-center py-20 text-center">
            <div className="text-4xl mb-4">📊</div>
            <h3 className="text-base font-semibold text-text-primary mb-2">Run a Backtest</h3>
            <p className="text-sm text-text-muted max-w-sm">
              Configure your parameters on the left and click "Run Backtest" to analyze the ICT
              Liquidity Sweep & FVG Reversal strategy on historical data.
            </p>
          </div>
        )}

        {isRunning && (
          <div className="card">
            <ProgressBar progress={backtestData?.progress ?? 0} />
          </div>
        )}

        {hasFailed && (
          <div className="card border-bear/30 bg-bear/5">
            <div className="text-bear text-sm font-medium mb-1">Backtest Failed</div>
            <div className="text-text-muted text-xs">{backtestData?.error ?? 'Unknown error'}</div>
          </div>
        )}

        {results && metrics && (
          <>
            {/* Metrics */}
            <div className="card">
              <BacktestMetricsPanel metrics={metrics} />
            </div>

            {/* Charts Row */}
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              <div className="card">
                <EquityCurve data={equityCurve} />
              </div>
              <div className="card">
                <MonthlyReturns data={monthlyReturns} />
              </div>
            </div>

            {/* Trade History */}
            <div className="card">
              <TradeHistoryTable trades={results.trades} />
            </div>
          </>
        )}
      </div>
    </div>
  )
}
