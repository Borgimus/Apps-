import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import type { MonthlyReturn } from '../../types/ict'

interface MonthlyReturnsProps {
  data: MonthlyReturn[]
}

export default function MonthlyReturns({ data }: MonthlyReturnsProps) {
  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-36 text-text-muted text-sm">
        No monthly return data
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">Monthly Returns</h3>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 5, right: 5, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" vertical={false} />
            <XAxis
              dataKey="month"
              tick={{ fill: '#64748b', fontSize: 10 }}
              tickLine={false}
              axisLine={{ stroke: '#2a2d3e' }}
            />
            <YAxis
              tick={{ fill: '#64748b', fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
              width={40}
            />
            <Tooltip
              content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null
                const value = payload[0].value as number
                const trades = (payload[0].payload as MonthlyReturn).trades
                return (
                  <div className="bg-bg-secondary border border-border rounded-md p-3 text-xs shadow-xl">
                    <div className="text-text-muted mb-1">{label}</div>
                    <div className={`font-mono font-semibold ${value >= 0 ? 'text-bull' : 'text-bear'}`}>
                      {(value * 100).toFixed(2)}%
                    </div>
                    <div className="text-text-muted mt-0.5">{trades} trades</div>
                  </div>
                )
              }}
            />
            <Bar dataKey="return" radius={[2, 2, 0, 0]}>
              {data.map((entry, index) => (
                <Cell key={index} fill={entry.return >= 0 ? '#26a69a' : '#ef5350'} opacity={0.8} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
