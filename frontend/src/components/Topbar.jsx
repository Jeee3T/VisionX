import { useNavigate } from 'react-router-dom'
import { LogOut, Menu, Play } from 'lucide-react'
import { useAuth } from '../context/AuthContext'
import { initials } from '../utils/format'

export default function Topbar({ onMenu }) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const signOut = async () => {
    await logout()
    navigate('/login')
  }

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-ink-200/70 bg-white/85 px-4 backdrop-blur md:px-8">
      <button onClick={onMenu} className="rounded-lg p-2 text-ink-600 hover:bg-ink-100 md:hidden">
        <Menu size={20} />
      </button>

      <div className="flex-1" />

      <button onClick={() => navigate('/presentations')} className="btn-primary hidden sm:inline-flex">
        <Play size={16} /> Start presenting
      </button>

      <div className="flex items-center gap-2.5 rounded-xl border border-ink-200 py-1.5 pl-1.5 pr-3">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-gradient text-xs font-semibold text-white">
          {initials(user?.name)}
        </span>
        <div className="hidden leading-tight sm:block">
          <p className="max-w-[10rem] truncate text-xs font-semibold text-ink-800">{user?.name}</p>
          <p className="max-w-[10rem] truncate text-[11px] text-ink-400">{user?.email}</p>
        </div>
      </div>

      <button onClick={signOut} title="Sign out" className="rounded-lg p-2 text-ink-500 hover:bg-ink-100 hover:text-red-600">
        <LogOut size={18} />
      </button>
    </header>
  )
}
