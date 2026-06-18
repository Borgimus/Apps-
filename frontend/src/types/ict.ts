export type Direction = 'long' | 'short';
export type SignalStatus = 'active' | 'pending' | 'filled' | 'stopped' | 'target_hit' | 'cancelled';
export type SweepType = 'asian_high' | 'asian_low' | 'london_high' | 'london_low' | 'swing_high' | 'swing_low';
export type MarketStructureType = 'swing_high' | 'swing_low' | 'bos_bullish' | 'bos_bearish' | 'choch_bullish' | 'choch_bearish';
export type SessionName = 'asian' | 'london' | 'new_york';
export type BacktestStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface SessionLevels {
  asian_high: number;
  asian_low: number;
  london_high: number;
  london_low: number;
  timestamp: string;
}

export interface ICTSignal {
  id: string;
  symbol: string;
  direction: Direction;
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  fvg_upper: number;
  fvg_lower: number;
  sweep_type: SweepType;
  confidence: number;
  timestamp: string;
  status: SignalStatus;
}

export interface MarketStructurePoint {
  type: MarketStructureType;
  price: number;
  timestamp: string;
}

export interface ScannerResult {
  symbol: string;
  signal: Direction | null;
  confidence: number;
  session_levels: SessionLevels;
  active_fvgs: FVGZone[];
}

export interface FVGZone {
  upper: number;
  lower: number;
  type: 'bullish' | 'bearish';
  timestamp: string;
  filled: boolean;
}

export interface BacktestConfig {
  risk_per_trade: number;
  min_confidence: number;
  use_asian_session: boolean;
  use_london_session: boolean;
  max_spread: number;
  fvg_min_size: number;
  sweep_buffer: number;
  partial_tp: boolean;
  partial_tp_ratio: number;
}

export interface BacktestRequest {
  symbol: string;
  start_date: string;
  end_date: string;
  config: Partial<BacktestConfig>;
}

export interface BacktestMetrics {
  win_rate: number;
  avg_rr: number;
  profit_factor: number;
  expectancy: number;
  max_drawdown: number;
  total_return: number;
  monthly_return: number;
  sharpe_ratio: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  long_win_rate: number;
  short_win_rate: number;
  total_pnl: number;
  trade_duration_avg: number;
  monthly_pnl: Record<string, number>;
}

export interface BacktestTrade {
  symbol: string;
  direction: Direction;
  entry_price: number;
  exit_price: number | null;
  stop_loss: number;
  take_profit: number;
  position_size: number;
  risk_amount: number;
  pnl: number;
  rr_achieved: number;
  entry_time: string;
  exit_time: string | null;
  trade_duration_minutes: number;
  exit_reason: 'sl' | 'tp' | 'eod' | 'signal';
  fvg_type: string;
  sweep_type: string;
}

// Flat response from GET /api/ict/backtest/{task_id}
export interface BacktestResults {
  task_id: string;
  status: BacktestStatus;
  progress?: number;        // 0–1 while status === 'running'
  symbol: string;
  start_date: string | null;
  end_date: string | null;
  // inline metrics (mirrors ICTBacktestResultResponse)
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  long_win_rate: number;
  short_win_rate: number;
  avg_rr: number;
  profit_factor: number;
  expectancy: number;
  total_pnl: number;
  total_return: number;
  monthly_return: number;
  max_drawdown: number;
  sharpe_ratio: number;
  trade_duration_avg: number;
  monthly_pnl: Record<string, number>;
  trades: BacktestTrade[];
  error: string | null;
}

export interface EquityPoint {
  date: string;
  equity: number;
  drawdown: number;
}

export interface MonthlyReturn {
  month: string;
  return: number;
  trades: number;
}

// POST /api/ict/backtest returns just a task_id
export interface BacktestTaskCreated {
  task_id: string;
}

// Alias — the polling response IS the results object
export type BacktestResponse = BacktestResults;

export interface StrategyConfig {
  // Session settings
  asian_session_start: string;
  asian_session_end: string;
  london_session_start: string;
  london_session_end: string;
  ny_session_start: string;
  ny_session_end: string;
  // Risk settings
  risk_per_trade: number;
  max_daily_trades: number;
  max_concurrent_trades: number;
  // FVG settings
  fvg_min_size: number;
  fvg_max_age_bars: number;
  // Sweep settings
  sweep_buffer: number;
  min_sweep_distance: number;
  // Filter settings
  min_confidence: number;
  require_session_alignment: boolean;
  // Partial TP
  partial_tp: boolean;
  partial_tp_ratio: number;
  partial_tp_size: number;
  // Alerts
  alert_on_signal: boolean;
  alert_on_fill: boolean;
  alert_webhook_url: string;
}

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export interface WebSocketMessage {
  type: 'signal' | 'signal_update' | 'heartbeat' | 'error';
  data: ICTSignal | { id: string; status: SignalStatus } | null;
  timestamp: string;
}
