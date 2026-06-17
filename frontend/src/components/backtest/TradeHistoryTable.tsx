import { useState } from 'react'
import type { BacktestTrade } from '../../types/ict'
import { DirectionBadge } from '../signals/SignalBadge'

interface TradeHistoryTableProps {
  trades: BacktestTrade[]
}

type SortKey = keyof BacktestTrade
type SortDir = 'asc' | 'desc'

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleString([], {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return ts
  }
}

function formatPrice(p: number): string {
  return p > 100 ? p.toFixed(2) : p.toFixed(5)
}

export default function TradeHistoryTable({ trades }: TradeHistoryTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('entry_time')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [page, setPage] = useState(0)
  const PAGE_SIZE = 20

  const sorted = [...trades].sort((a, b) => {
    const av = a[sortKey]
    const bv = b[sortKey]
    if (typeof av === 'number' && typeof bv === 'number') {
      return sortDir === 'asc' ? av - bv : bv - av
    }
    return sortDir === 'asc'
      ? String(av).localeCompare(String(bv))
      : String(bv).localeCompare(String(av))
  })

  const pages = Math.ceil(sorted.length / PAGE_SIZE)
  const visible = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
    setPage(0)
  }

  const SortIcon = ({ col }: { col: SortKey }) => (
    <span className="ml-1 text-text-muted">
      {sortKey === col ? (sortDir === 'asc' ? '↑' : '↓') : '⇅'}
    </span>
  )

  const winCount = trades.filter((t) => t.pnl > 0).length
  const totalPnl = trades.reduce((sum, t) => sum + t.pnl, 0)

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">Trade History</h3>
        <div className="flex items-center gap-4 text-xs text-text-muted">
          <span>{trades.length} trades</span>
          <span className="text-bull">{winCount} wins</span>
          <span className={totalPnl >= 0 ? 'text-bull' : 'text-bear'}>
            {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)} total
          </span>
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-bg-tertiary border-b border-border">
              {([
                ['entry_time', 'Entry Time'],
                ['symbol', 'Symbol'],
                ['direction', 'Dir'],
                ['entry_price', 'Entry'],
                ['exit_price', 'Exit'],
                ['pnl', 'P&L'],
                ['rr_achieved', 'R:R'],
                ['exit_reason', 'Exit'],
                ['trade_duration_minutes', 'Duration'],
              ] as [SortKey, string][]).map(([key, label]) => (
                <th
                  key={key}
                  onClick={() => handleSort(key)}
                  className="text-left px-3 py-2.5 font-medium text-text-secondary uppercase tracking-wide cursor-pointer hover:text-text-primary transition-colors select-none whitespace-nowrap"
                >
                  {label}
                  <SortIcon col={key} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((trade, idx) => (
              <tr key={`${trade.symbol}-${trade.entry_time}-${idx}`} className="border-b border-border hover:bg-bg-tertiary transition-colors">
                <td className="px-3 py-2.5 text-text-muted whitespace-nowrap">{formatTime(trade.entry_time)}</td>
                <td className="px-3 py-2.5 font-mono font-semibold text-text-primary">{trade.symbol}</td>
                <td className="px-3 py-2.5"><DirectionBadge direction={trade.direction} /></td>
                <td className="px-3 py-2.5 font-mono text-text-secondary">{formatPrice(trade.entry_price)}</td>
                <td className="px-3 py-2.5 font-mono text-text-secondary">
                  {trade.exit_price != null ? formatPrice(trade.exit_price) : '—'}
                </td>
                <td className={`px-3 py-2.5 font-mono font-semibold ${trade.pnl >= 0 ? 'text-bull' : 'text-bear'}`}>
                  {trade.pnl >= 0 ? '+' : ''}${trade.pnl.toFixed(2)}
                </td>
                <td className="px-3 py-2.5 font-mono text-text-primary">
                  {trade.rr_achieved > 0 ? `1:${trade.rr_achieved.toFixed(2)}` : trade.rr_achieved.toFixed(2)}
                </td>
                <td className="px-3 py-2.5">
                  <span className={`px-1.5 py-0.5 rounded text-xs capitalize ${
                    trade.exit_reason === 'tp' ? 'bg-bull/20 text-bull' :
                    trade.exit_reason === 'sl' ? 'bg-bear/20 text-bear' :
                    'bg-bg-tertiary text-text-muted'
                  }`}>
                    {trade.exit_reason === 'tp' ? 'Target' : trade.exit_reason === 'sl' ? 'Stop' : trade.exit_reason}
                  </span>
                </td>
                <td className="px-3 py-2.5 text-text-muted">
                  {trade.trade_duration_minutes >= 60
                    ? `${(trade.trade_duration_minutes / 60).toFixed(1)}h`
                    : `${trade.trade_duration_minutes}m`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div className="flex items-center justify-between text-xs">
          <span className="text-text-muted">
            Page {page + 1} of {pages} ({trades.length} total)
          </span>
          <div className="flex gap-1">
            <button
              onClick={() => setPage(Math.max(0, page - 1))}
              disabled={page === 0}
              className="px-2 py-1 rounded bg-bg-tertiary text-text-secondary hover:bg-border disabled:opacity-40 transition-colors"
            >
              ← Prev
            </button>
            <button
              onClick={() => setPage(Math.min(pages - 1, page + 1))}
              disabled={page === pages - 1}
              className="px-2 py-1 rounded bg-bg-tertiary text-text-secondary hover:bg-border disabled:opacity-40 transition-colors"
            >
              Next →
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
