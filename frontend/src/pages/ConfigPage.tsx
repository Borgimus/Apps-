import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { ictApi } from '../api/ict'
import { useConfigStore } from '../store/configStore'
import StrategyConfigPanel from '../components/config/StrategyConfig'
import SessionConfig from '../components/config/SessionConfig'
import RiskConfig from '../components/config/RiskConfig'
import AlertConfig from '../components/config/AlertConfig'
import toast from 'react-hot-toast'
import type { StrategyConfig } from '../types/ict'

const TABS = ['Strategy', 'Sessions', 'Risk', 'Alerts'] as const
type Tab = typeof TABS[number]

export default function ConfigPage() {
  const [activeTab, setActiveTab] = useState<Tab>('Strategy')
  const { config, updateConfig, setConfig, isDirty, markClean } = useConfigStore()

  // Fetch remote config on mount
  useQuery({
    queryKey: ['config'],
    queryFn: async () => {
      const remoteConfig = await ictApi.getConfig()
      setConfig(remoteConfig)
      return remoteConfig
    },
    retry: 1,
    staleTime: Infinity,
  })

  const saveMutation = useMutation({
    mutationFn: (cfg: StrategyConfig) => ictApi.updateConfig(cfg),
    onSuccess: (data) => {
      setConfig(data)
      markClean()
      toast.success('Configuration saved')
    },
    onError: () => {
      toast.error('Failed to save configuration')
    },
  })

  const handleSave = () => {
    saveMutation.mutate(config)
  }

  const handleChange = (updates: Partial<StrategyConfig>) => {
    updateConfig(updates)
  }

  return (
    <div className="max-w-4xl space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-text-primary">Strategy Configuration</h1>
          <p className="text-sm text-text-muted mt-0.5">
            Configure ICT Liquidity Sweep & FVG Reversal strategy parameters
          </p>
        </div>
        <div className="flex items-center gap-3">
          {isDirty && (
            <span className="text-xs text-asian px-2 py-1 bg-asian/10 rounded border border-asian/30">
              Unsaved changes
            </span>
          )}
          <button
            onClick={handleSave}
            disabled={!isDirty || saveMutation.isPending}
            className="btn-primary"
          >
            {saveMutation.isPending ? 'Saving...' : 'Save Config'}
          </button>
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="card p-0 overflow-hidden">
        <div className="flex border-b border-border">
          {TABS.map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-6 py-3 text-sm font-medium transition-colors ${
                activeTab === tab
                  ? 'text-accent border-b-2 border-accent bg-accent/5'
                  : 'text-text-muted hover:text-text-primary'
              }`}
            >
              {tab}
            </button>
          ))}
        </div>

        <div className="p-6">
          {activeTab === 'Strategy' && (
            <StrategyConfigPanel config={config} onChange={handleChange} />
          )}
          {activeTab === 'Sessions' && (
            <SessionConfig config={config} onChange={handleChange} />
          )}
          {activeTab === 'Risk' && (
            <RiskConfig config={config} onChange={handleChange} />
          )}
          {activeTab === 'Alerts' && (
            <AlertConfig config={config} onChange={handleChange} />
          )}
        </div>
      </div>

      {/* Config Preview */}
      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">
            Current Config (JSON Preview)
          </h3>
        </div>
        <pre className="text-xs font-mono text-text-muted overflow-auto max-h-48 p-3 bg-bg-tertiary rounded border border-border">
          {JSON.stringify(config, null, 2)}
        </pre>
      </div>
    </div>
  )
}
