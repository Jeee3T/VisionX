import { NavLink } from 'react-router-dom'
import {
  BarChart3,
  Hand,
  History,
  LayoutDashboard,
  Library,
  Upload,
  UserRound,
  X,
} from 'lucide-react'

const LINKS = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/presentations', label: 'Library', icon: Library },
  { to: '/upload', label: 'Upload', icon: Upload },
  { to: '/gestures', label: 'Gestures', icon: Hand },
  { to: '/history', label: 'History', icon: History },
  { to: '/analytics', label: 'Analytics', icon: BarChart3 },
  { to: '/profile', label: 'Profile', icon: UserRound },
]

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-2">
      <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-gradient text-sm font-bold text-white shadow-lift">
        VX
      </span>
      <div className="leading-tight">
        <p className="text-sm font-semibold text-ink-900">VisionX</p>
        <p className="text-[11px] text-ink-400">Gesture presenter</p>
      </div>
    </div>
  )
}

function Links({ onNavigate }) {
  return (
    <nav className="mt-6 flex flex-col gap-1">
      {LINKS.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          onClick={onNavigate}
          className={({ isActive }) =>
            `group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors ${
              isActive
                ? 'bg-brand-50 text-brand-700'
                : 'text-ink-600 hover:bg-ink-100/70 hover:text-ink-900'
            }`
          }
        >
          {({ isActive }) => (
            <>
              <Icon size={18} className={isActive ? 'text-brand-600' : 'text-ink-400 group-hover:text-ink-600'} />
              <span className="lg:inline">{label}</span>
            </>
          )}
        </NavLink>
      ))}
    </nav>
  )
}

export default function Sidebar({ mobileOpen, onClose }) {
  return (
    <>
      {/* Desktop / tablet: fixed rail */}
      <aside className="hidden w-60 shrink-0 border-r border-ink-200/70 bg-white px-3 py-5 md:block">
        <Brand />
        <Links />
      </aside>

      {/* Mobile: drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 md:hidden">
          <div className="absolute inset-0 bg-ink-900/40 backdrop-blur-sm" onClick={onClose} />
          <aside className="absolute inset-y-0 left-0 w-64 animate-fade-in bg-white px-3 py-5 shadow-lift">
            <div className="flex items-center justify-between">
              <Brand />
              <button onClick={onClose} className="rounded-lg p-2 text-ink-500 hover:bg-ink-100">
                <X size={18} />
              </button>
            </div>
            <Links onNavigate={onClose} />
          </aside>
        </div>
      )}
    </>
  )
}
