import type { StrategyConfig } from '../../types/ict'

interface SessionConfigProps {
  config: StrategyConfig
  onChange: (updates: Partial<StrategyConfig>) => void
}

interface SessionRowProps {
  name: string
  color: string
  startKey: keyof StrategyConfig
  endKey: keyof StrategyConfig
  config: StrategyConfig
  onChange: (updates: Partial<StrategyConfig>) => void
}

function SessionRow({ name, color, startKey, endKey, config, onChange }: SessionRowProps) {
  return (
    <div className="grid grid-cols-3 gap-4 items-center">
      <div className="flex items-center gap-2">
        <div className={`w-3 h-3 rounded-full ${color}`} />
        <span className="text-sm font-medium text-text-primary">{name}</span>
      </div>
      <div>
        <label className="label block mb-1">Start (UTC)</label>
        <input
          type="time"
          value={config[startKey] as string}
          onChange={(e) => onChange({ [startKey]: e.target.value })}
          className="input"
        />
      </div>
      <div>
        <label className="label block mb-1">End (UTC)</label>
        <input
          type="time"
          value={config[endKey] as string}
          onChange={(e) => onChange({ [endKey]: e.target.value })}
          className="input"
        />
      </div>
    </div>
  )
}

export default function SessionConfig({ config, onChange }: SessionConfigProps) {
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-4">
          Session Time Windows (UTC)
        </h3>
        <div className="space-y-4">
          <SessionRow
            name="Asian Session"
            color="bg-asian"
            startKey="asian_session_start"
            endKey="asian_session_end"
            config={config}
            onChange={onChange}
          />
          <SessionRow
            name="London Session"
            color="bg-london"
            startKey="london_session_start"
            endKey="london_session_end"
            config={config}
            onChange={onChange}
          />
          <SessionRow
            name="New York Session"
            color="bg-accent"
            startKey="ny_session_start"
            endKey="ny_session_end"
            config={config}
            onChange={onChange}
          />
        </div>
      </div>

      <div className="card bg-bg-tertiary/50">
        <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wide mb-2">ICT Session Concepts</h4>
        <ul className="space-y-1.5 text-xs text-text-muted">
          <li className="flex items-start gap-2">
            <span className="text-asian mt-0.5">●</span>
            <span><strong className="text-text-secondary">Asian Session:</strong> Forms initial range. Asian High/Low become liquidity targets for London/NY sessions.</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-london mt-0.5">●</span>
            <span><strong className="text-text-secondary">London Session:</strong> Primary sweep session. Sweeps Asian highs/lows to collect liquidity before reversing.</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-accent mt-0.5">●</span>
            <span><strong className="text-text-secondary">New York Session:</strong> Continuation or secondary sweep. London-NY confluence signals are highest quality.</span>
          </li>
        </ul>
      </div>
    </div>
  )
}
