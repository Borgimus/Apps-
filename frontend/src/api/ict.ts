import apiClient from './client';
import type {
  SessionLevels,
  ICTSignal,
  BacktestRequest,
  BacktestResponse,
  ScannerResult,
  StrategyConfig,
  MarketStructurePoint,
} from '../types/ict';

export const ictApi = {
  // Session levels
  getSessionLevels: async (symbol: string): Promise<SessionLevels> => {
    const { data } = await apiClient.get<SessionLevels>(`/api/ict/sessions/${symbol}`);
    return data;
  },

  // Signals
  getSignals: async (): Promise<ICTSignal[]> => {
    const { data } = await apiClient.get<ICTSignal[]>('/api/ict/signals');
    return Array.isArray(data) ? data : [];
  },

  // Backtest
  runBacktest: async (request: BacktestRequest): Promise<{ task_id: string }> => {
    const { data } = await apiClient.post<{ task_id: string }>('/api/ict/backtest', request);
    return data;
  },

  getBacktestResult: async (taskId: string): Promise<BacktestResponse> => {
    const { data } = await apiClient.get<BacktestResponse>(`/api/ict/backtest/${taskId}`);
    return data;
  },

  // Scanner — backend returns { results: [...], symbols_scanned, signals_found, scanned_at }
  getScannerResults: async (): Promise<ScannerResult[]> => {
    const { data } = await apiClient.get<{ results: ScannerResult[] }>('/api/ict/scanner');
    return Array.isArray(data) ? data : (data.results ?? []);
  },

  // Config
  getConfig: async (): Promise<StrategyConfig> => {
    const { data } = await apiClient.get<StrategyConfig>('/api/ict/config');
    return data;
  },

  updateConfig: async (config: Partial<StrategyConfig>): Promise<StrategyConfig> => {
    const { data } = await apiClient.put<StrategyConfig>('/api/ict/config', config);
    return data;
  },

  // OHLCV bars for the chart
  getBars: async (symbol: string, limit = 300): Promise<import('../types/ict').Candle[]> => {
    const { data } = await apiClient.get<import('../types/ict').Candle[]>(
      `/api/ict/bars/${symbol}`,
      { params: { limit } }
    );
    return Array.isArray(data) ? data : [];
  },

  // Market structure
  getMarketStructure: async (symbol: string): Promise<MarketStructurePoint[]> => {
    const { data } = await apiClient.get<MarketStructurePoint[]>(`/api/ict/market-structure/${symbol}`);
    return data;
  },
};
