import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/layout/Layout'
import DashboardPage from './pages/DashboardPage'
import ChartPage from './pages/ChartPage'
import ScannerPage from './pages/ScannerPage'
import BacktestPage from './pages/BacktestPage'
import ConfigPage from './pages/ConfigPage'
import { useWebSocket } from './hooks/useWebSocket'

function AppInner() {
  useWebSocket()
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="chart" element={<ChartPage />} />
        <Route path="scanner" element={<ScannerPage />} />
        <Route path="backtest" element={<BacktestPage />} />
        <Route path="config" element={<ConfigPage />} />
      </Route>
    </Routes>
  )
}

export default function App() {
  return <AppInner />
}
