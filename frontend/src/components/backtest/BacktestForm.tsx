import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ictApi } from '../../api/ict'
import type { BacktestRequest, BacktestConfig } from '../../types/ict'
import toast from 'react-hot-toast'

const SYMBOLS = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'NQ', 'ES', 'GC', 'CL']

interface BacktestFormProps {
  onTaskCreated: (taskId: string) => void
}

export default function BacktestForm({ onTaskCreated }: BacktestFormProps) {
  const [symbol, setSymbol] = useState('EURUSD')
  const [startDate, setStartDate] = useState('2023-01-01')
  const [endDate, setEndDate] = useState('2024-01-01')
  const [config, setConfig] = useState<Partial<BacktestConfig>>({
    risk_per_trade: 1.0,
    min_confidence: 0.65,
    fvg_min_size: 5,
    sweep_buffer: 2,
    partial_tp: true,
    partial_tp_ratio: 1.5,
  })

  const mutation = useMutation({
    mutationFn: (req: BacktestRequest) => ictApi.runBacktest(req),
    onSuccess: (data) => {
      toast.success('Backtest started!')
      onTaskCreated(data.task_id)
    },
    onError: () => {
      toast.error('Failed to start backtest')
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    mutation.mutate({ symbol, start_date: startDate, end_date: endDate, config })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      {/* Symbol */}
      <div>
        <label className="label block mb-1">Symbol</label>
        <select
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          className="input"
        >
          {SYMBOLS.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>

      {/* Date Range */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label block mb-1">Start Date</label>
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="input"
          />
        </div>
        <div>
          <label className="label block mb-1">End Date</label>
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="input"
          />
        </div>
      </div>

      <div className="border-t border-border pt-4">
        <h4 className="label mb-3">Config Overrides</h4>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label block mb-1">Risk Per Trade %</label>
              <input
                type="number"
                step="0.1"
                min="0.1"
                max="10"
                value={config.risk_per_trade ?? 1}
                onChange={(e) => setConfig({ ...config, risk_per_trade: parseFloat(e.target.value) })}
                className="input"
              />
            </div>
            <div>
              <label className="label block mb-1">Min Confidence</label>
              <input
                type="number"
                step="0.05"
                min="0"
                max="1"
                value={config.min_confidence ?? 0.65}
                onChange={(e) => setConfig({ ...config, min_confidence: parseFloat(e.target.value) })}
                className="input"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label block mb-1">FVG Min Size (pips)</label>
              <input
                type="number"
                step="1"
                min="1"
                value={config.fvg_min_size ?? 5}
                onChange={(e) => setConfig({ ...config, fvg_min_size: parseInt(e.target.value) })}
                className="input"
              />
            </div>
            <div>
              <label className="label block mb-1">Sweep Buffer (pips)</label>
              <input
                type="number"
                step="1"
                min="0"
                value={config.sweep_buffer ?? 2}
                onChange={(e) => setConfig({ ...config, sweep_buffer: parseInt(e.target.value) })}
                className="input"
              />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <input
              type="checkbox"
              id="partial-tp"
              checked={config.partial_tp ?? true}
              onChange={(e) => setConfig({ ...config, partial_tp: e.target.checked })}
              className="w-4 h-4 accent-accent"
            />
            <label htmlFor="partial-tp" className="text-sm text-text-secondary">Enable Partial Take Profit</label>
          </div>
          {config.partial_tp && (
            <div>
              <label className="label block mb-1">Partial TP Ratio</label>
              <input
                type="number"
                step="0.1"
                min="0.5"
                max="5"
                value={config.partial_tp_ratio ?? 1.5}
                onChange={(e) => setConfig({ ...config, partial_tp_ratio: parseFloat(e.target.value) })}
                className="input"
              />
            </div>
          )}
        </div>
      </div>

      <button
        type="submit"
        disabled={mutation.isPending}
        className="btn-primary w-full"
      >
        {mutation.isPending ? 'Starting...' : 'Run Backtest'}
      </button>
    </form>
  )
}
