import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { StrategyConfig } from '../types/ict';

const defaultConfig: StrategyConfig = {
  asian_session_start: '00:00',
  asian_session_end: '08:00',
  london_session_start: '08:00',
  london_session_end: '16:00',
  ny_session_start: '13:00',
  ny_session_end: '21:00',
  risk_per_trade: 1.0,
  max_daily_trades: 5,
  max_concurrent_trades: 2,
  fvg_min_size: 5,
  fvg_max_age_bars: 50,
  sweep_buffer: 2,
  min_sweep_distance: 10,
  min_confidence: 0.65,
  require_session_alignment: true,
  partial_tp: true,
  partial_tp_ratio: 1.5,
  partial_tp_size: 50,
  alert_on_signal: true,
  alert_on_fill: true,
  alert_webhook_url: '',
};

interface ConfigStore {
  config: StrategyConfig;
  isDirty: boolean;
  setConfig: (config: StrategyConfig) => void;
  updateConfig: (updates: Partial<StrategyConfig>) => void;
  resetConfig: () => void;
  markClean: () => void;
}

export const useConfigStore = create<ConfigStore>()(
  persist(
    (set) => ({
      config: defaultConfig,
      isDirty: false,

      setConfig: (config) => set({ config, isDirty: false }),

      updateConfig: (updates) =>
        set((state) => ({
          config: { ...state.config, ...updates },
          isDirty: true,
        })),

      resetConfig: () => set({ config: defaultConfig, isDirty: false }),

      markClean: () => set({ isDirty: false }),
    }),
    {
      name: 'ict-config',
      partialize: (state) => ({ config: state.config }),
    }
  )
);
