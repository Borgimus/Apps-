import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Area,
  AreaChart,
} from 'recharts'
import type { EquityPoint } from '../../types/ict'

interface EquityCurveProps {
  data: EquityPoint[]
  initialEquity?: number
}

const CustomTooltip = ({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number; name: string }>; label?: string }) => {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-bg-secondary border border-border rounded-md p-3 text-xs shadow-xl">
      <div className="text-text-muted mb-1">{label}</div>
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2">
          <div
            className="w-2 h-2 rounded-full"
            style={{ background: p.name === 'equity' ? '#26a69a' : '#ef5350' }}
          />
          <span className="text-text-secondary capitalize">{p.name}:</span>
          <span className="font-mono font-medium text-text-primary">
            {p.name === 'equity' ? `$${p.value.toLocaleString()}` : `${(p.value * 100).toFixed(2)}%`}
          </span>
        </div>
      ))}
    </div>
  )
}

export default function EquityCurve({ data, initialEquity = 10000 }: EquityCurveProps) {
  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-text-muted text-sm">
        No equity curve data
      </div>
    )
  }

  const maxEquity = Math.max(...data.map((d) => d.equity))
  const minEquity = Math.min(...data.map((d) => d.equity))
  const isProfit = data[data.length - 1]?.equity >= initialEquity

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">Equity Curve</h3>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 5, right: 5, left: 0, bottom: 5 }}>
            <defs>
              <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={isProfit ? '#26a69a' : '#ef5350'} stopOpacity={0.3} />
                <stop offset="95%" stopColor={isProfit ? '#26a69a' : '#ef5350'} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
            <XAxis
              dataKey="date"
              tick={{ fill: '#64748b', fontSize: 10 }}
              tickLine={false}
              axisLine={{ stroke: '#2a2d3e' }}
              interval="preserveStartEnd"
            />
            <YAxis
              tick={{ fill: '#64748b', fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              domain={[minEquity * 0.98, maxEquity * 1.02]}
              tickFormatter={(v: number) => `$${(v / 1000).toFixed(1)}k`}
              width={55}
            />
            <Tooltip content={<CustomTooltip />} />
            <ReferenceLine y={initialEquity} stroke="#2a2d3e" strokeDasharray="4 4" />
            <Area
              type="monotone"
              dataKey="equity"
              stroke={isProfit ? '#26a69a' : '#ef5350'}
              strokeWidth={2}
              fill="url(#equityGrad)"
              dot={false}
              activeDot={{ r: 4, fill: isProfit ? '#26a69a' : '#ef5350' }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Drawdown chart */}
      <div className="h-24">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 5, right: 5, left: 0, bottom: 5 }}>
            <defs>
              <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#ef5350" stopOpacity={0.4} />
                <stop offset="95%" stopColor="#ef5350" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
            <XAxis dataKey="date" hide />
            <YAxis
              tick={{ fill: '#64748b', fontSize: 9 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
              width={40}
            />
            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null
                return (
                  <div className="bg-bg-secondary border border-border rounded px-2 py-1 text-xs">
                    <span className="text-bear font-mono">{((payload[0].value as number) * 100).toFixed(2)}% DD</span>
                  </div>
                )
              }}
            />
            <Area
              type="monotone"
              dataKey="drawdown"
              stroke="#ef5350"
              strokeWidth={1.5}
              fill="url(#ddGrad)"
              dot={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="text-xs text-text-muted text-center">Drawdown</div>
    </div>
  )
}
