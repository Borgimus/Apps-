import type { StrategyConfig } from '../../types/ict'

interface AlertConfigProps {
  config: StrategyConfig
  onChange: (updates: Partial<StrategyConfig>) => void
}

export default function AlertConfig({ config, onChange }: AlertConfigProps) {
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-4">Notification Events</h3>
        <div className="space-y-4">
          <div className="flex items-start gap-3">
            <input
              type="checkbox"
              id="alert-signal"
              checked={config.alert_on_signal}
              onChange={(e) => onChange({ alert_on_signal: e.target.checked })}
              className="w-4 h-4 mt-0.5 accent-accent"
            />
            <div>
              <label htmlFor="alert-signal" className="text-sm text-text-primary cursor-pointer font-medium">
                Alert on New Signal
              </label>
              <p className="text-xs text-text-muted mt-0.5">
                Send notification when a new ICT setup is detected
              </p>
            </div>
          </div>
          <div className="flex items-start gap-3">
            <input
              type="checkbox"
              id="alert-fill"
              checked={config.alert_on_fill}
              onChange={(e) => onChange({ alert_on_fill: e.target.checked })}
              className="w-4 h-4 mt-0.5 accent-accent"
            />
            <div>
              <label htmlFor="alert-fill" className="text-sm text-text-primary cursor-pointer font-medium">
                Alert on Order Fill
              </label>
              <p className="text-xs text-text-muted mt-0.5">
                Send notification when an order is filled, stopped, or hits target
              </p>
            </div>
          </div>
        </div>
      </div>

      <div>
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-4">Webhook</h3>
        <div>
          <label className="label block mb-1">Webhook URL</label>
          <input
            type="url"
            placeholder="https://hooks.slack.com/services/..."
            value={config.alert_webhook_url}
            onChange={(e) => onChange({ alert_webhook_url: e.target.value })}
            className="input"
          />
          <p className="text-xs text-text-muted mt-1">
            Optional webhook for Slack, Discord, or custom integrations. Leave empty to disable.
          </p>
        </div>
      </div>

      <div className="card bg-bg-tertiary/50">
        <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wide mb-2">In-App Notifications</h4>
        <p className="text-xs text-text-muted">
          Toast notifications are always active for signal events in the browser.
          The WebSocket feed provides real-time updates without page refresh.
        </p>
      </div>
    </div>
  )
}
