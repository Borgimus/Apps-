import { NavLink } from 'react-router-dom'
import { useSignalStore } from '../../store/signalStore'

const NAV_ITEMS = [
  { to: '/dashboard', label: 'Dashboard', icon: '⬛' },
  { to: '/chart', label: 'Chart', icon: '📈' },
  { to: '/scanner', label: 'Scanner', icon: '🔍' },
  { to: '/backtest', label: 'Backtest', icon: '📊' },
  { to: '/config', label: 'Config', icon: '⚙️' },
]

export default function Sidebar() {
  const { signals, wsConnected } = useSignalStore()
  const activeCount = signals.filter((s) => s.status === 'active').length

  return (
    <aside className="w-16 lg:w-56 bg-bg-secondary border-r border-border flex flex-col h-full shrink-0">
      {/* Logo */}
      <div className="h-14 flex items-center px-3 lg:px-4 border-b border-border">
        <div className="w-8 h-8 rounded bg-accent flex items-center justify-center text-white font-bold text-sm shrink-0">
          ICT
        </div>
        <div className="hidden lg:block ml-3">
          <div className="text-sm font-semibold text-text-primary">ICT Strategy</div>
          <div className="text-xs text-text-muted">Liquidity Sweep</div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 p-2 space-y-1">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `flex items-center gap-3 px-2 py-2.5 rounded-md text-sm transition-colors ${
                isActive
                  ? 'bg-accent/20 text-accent font-medium'
                  : 'text-text-secondary hover:bg-bg-tertiary hover:text-text-primary'
              }`
            }
          >
            <span className="text-base w-5 text-center shrink-0">{item.icon}</span>
            <span className="hidden lg:block">{item.label}</span>
            {item.label === 'Dashboard' && activeCount > 0 && (
              <span className="hidden lg:flex ml-auto bg-accent text-white text-xs rounded-full w-5 h-5 items-center justify-center">
                {activeCount}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      {/* WS Status */}
      <div className="p-3 border-t border-border">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${wsConnected ? 'bg-bull animate-pulse' : 'bg-bear'}`} />
          <span className="hidden lg:block text-xs text-text-muted">
            {wsConnected ? 'Live Feed' : 'Disconnected'}
          </span>
        </div>
      </div>
    </aside>
  )
}
