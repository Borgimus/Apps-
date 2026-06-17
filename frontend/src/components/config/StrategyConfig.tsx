import type { StrategyConfig } from '../../types/ict'

interface StrategyConfigProps {
  config: StrategyConfig
  onChange: (updates: Partial<StrategyConfig>) => void
}

export default function StrategyConfigPanel({ config, onChange }: StrategyConfigProps) {
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-4">FVG Settings</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label block mb-1">FVG Minimum Size (pips)</label>
            <input
              type="number"
              step="1"
              min="1"
              value={config.fvg_min_size}
              onChange={(e) => onChange({ fvg_min_size: parseFloat(e.target.value) })}
              className="input"
            />
            <p className="text-xs text-text-muted mt-1">Minimum gap size to qualify as FVG</p>
          </div>
          <div>
            <label className="label block mb-1">FVG Max Age (bars)</label>
            <input
              type="number"
              step="1"
              min="1"
              value={config.fvg_max_age_bars}
              onChange={(e) => onChange({ fvg_max_age_bars: parseInt(e.target.value) })}
              className="input"
            />
            <p className="text-xs text-text-muted mt-1">Ignore FVGs older than this</p>
          </div>
        </div>
      </div>

      <div>
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-4">Sweep Settings</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label block mb-1">Sweep Buffer (pips)</label>
            <input
              type="number"
              step="0.5"
              min="0"
              value={config.sweep_buffer}
              onChange={(e) => onChange({ sweep_buffer: parseFloat(e.target.value) })}
              className="input"
            />
            <p className="text-xs text-text-muted mt-1">Tolerance for liquidity sweep detection</p>
          </div>
          <div>
            <label className="label block mb-1">Min Sweep Distance (pips)</label>
            <input
              type="number"
              step="1"
              min="1"
              value={config.min_sweep_distance}
              onChange={(e) => onChange({ min_sweep_distance: parseFloat(e.target.value) })}
              className="input"
            />
            <p className="text-xs text-text-muted mt-1">Minimum distance for a valid sweep</p>
          </div>
        </div>
      </div>

      <div>
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-4">Filter Settings</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label block mb-1">Min Confidence (0-1)</label>
            <input
              type="number"
              step="0.05"
              min="0"
              max="1"
              value={config.min_confidence}
              onChange={(e) => onChange({ min_confidence: parseFloat(e.target.value) })}
              className="input"
            />
            <p className="text-xs text-text-muted mt-1">Minimum confidence score to generate signal</p>
          </div>
          <div className="flex items-start gap-3 pt-6">
            <input
              type="checkbox"
              id="session-align"
              checked={config.require_session_alignment}
              onChange={(e) => onChange({ require_session_alignment: e.target.checked })}
              className="w-4 h-4 mt-0.5 accent-accent"
            />
            <div>
              <label htmlFor="session-align" className="text-sm text-text-primary cursor-pointer">
                Require Session Alignment
              </label>
              <p className="text-xs text-text-muted mt-0.5">Only trade during aligned sessions</p>
            </div>
          </div>
        </div>
      </div>

      <div>
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-4">Partial Take Profit</h3>
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <input
              type="checkbox"
              id="partial-tp-strategy"
              checked={config.partial_tp}
              onChange={(e) => onChange({ partial_tp: e.target.checked })}
              className="w-4 h-4 accent-accent"
            />
            <label htmlFor="partial-tp-strategy" className="text-sm text-text-primary cursor-pointer">
              Enable Partial Take Profit
            </label>
          </div>
          {config.partial_tp && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 ml-7">
              <div>
                <label className="label block mb-1">Partial TP Ratio</label>
                <input
                  type="number"
                  step="0.1"
                  min="0.5"
                  max="5"
                  value={config.partial_tp_ratio}
                  onChange={(e) => onChange({ partial_tp_ratio: parseFloat(e.target.value) })}
                  className="input"
                />
              </div>
              <div>
                <label className="label block mb-1">Partial TP Size (%)</label>
                <input
                  type="number"
                  step="5"
                  min="10"
                  max="90"
                  value={config.partial_tp_size}
                  onChange={(e) => onChange({ partial_tp_size: parseInt(e.target.value) })}
                  className="input"
                />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
