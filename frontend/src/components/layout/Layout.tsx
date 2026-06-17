import { Outlet } from 'react-router-dom'
import { useState } from 'react'
import Sidebar from './Sidebar'
import TopBar from './TopBar'

export default function Layout() {
  const [symbol, setSymbol] = useState('EURUSD')
  const [timeframe, setTimeframe] = useState('1m')

  return (
    <div className="flex h-screen overflow-hidden bg-bg-primary">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0">
        <TopBar
          symbol={symbol}
          setSymbol={setSymbol}
          timeframe={timeframe}
          setTimeframe={setTimeframe}
        />
        <main className="flex-1 overflow-auto p-4">
          <Outlet context={{ symbol, timeframe }} />
        </main>
      </div>
    </div>
  )
}
