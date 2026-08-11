import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Hand, History, Layers, PenLine, Play, Presentation, Settings2, Upload } from 'lucide-react'
import PageHeader from '../components/PageHeader'
import StatCard from '../components/StatCard'
import PresentationCard from '../components/PresentationCard'
import { EmptyState, ErrorState, Loader } from '../components/Feedback'
import { analyticsApi } from '../services/endpoints'
import { useAuth } from '../context/AuthContext'
import { CHART_COLOURS, COMMANDS } from '../utils/constants'
import { formatDuration } from '../utils/format'

const QUICK_ACTIONS = [
  { to: '/upload', label: 'Upload', icon: Upload },
  { to: '/presentations', label: 'Start a session', icon: Play },
  { to: '/gestures', label: 'Configure gestures', icon: Settings2 },
  { to: '/history', label: 'View history', icon: History },
]

export default function Dashboard() {
  const { user } = useAuth()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    analyticsApi
      .dashboard()
      .then((response) => setData(response.data))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(load, [load])

  if (loading) return <Loader label="Loading your dashboard…" />
  if (error) return <ErrorState message={error} onRetry={load} />

  const { stats, sessionsOverTime, gestureBreakdown, recentPresentations } = data

  return (
    <div className="animate-fade-in">
      <PageHeader
        title={`Welcome back, ${(user?.name || '').split(' ')[0] || 'presenter'}`}
        subtitle="Everything below is computed from your real session history."
        actions={
          <>
            <Link to="/upload" className="btn-secondary">
              <Upload size={16} /> Upload
            </Link>
            <Link to="/presentations" className="btn-primary">
              <Play size={16} /> Start presenting
            </Link>
          </>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard icon={Presentation} label="Presentations" value={stats.presentations} tone="brand" />
        <StatCard
          icon={Play}
          label="Sessions"
          value={stats.sessions}
          hint={`${formatDuration(stats.totalMinutes * 60)} presenting time`}
          tone="violet"
        />
        <StatCard icon={Hand} label="Gestures used" value={stats.gesturesUsed} tone="sky" />
        <StatCard
          icon={PenLine}
          label="Annotations"
          value={stats.annotations}
          hint={`${stats.slidesNavigated} slides navigated`}
          tone="amber"
        />
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-5">
        <div className="card p-5 lg:col-span-3">
          <div className="mb-4 flex items-baseline justify-between">
            <h2 className="text-sm font-semibold text-ink-800">Sessions over time</h2>
            <span className="text-xs text-ink-400">Last 14 days</span>
          </div>
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={sessionsOverTime} margin={{ top: 4, right: 4, left: -22, bottom: 0 }}>
                <defs>
                  <linearGradient id="sessionFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#6366f1" stopOpacity={0.28} />
                    <stop offset="100%" stopColor="#6366f1" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12 }}
                  formatter={(value, name) => [value, name === 'sessions' ? 'Sessions' : 'Minutes']}
                />
                <Area type="monotone" dataKey="sessions" stroke="#6366f1" strokeWidth={2} fill="url(#sessionFill)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="card p-5 lg:col-span-2">
          <h2 className="mb-4 text-sm font-semibold text-ink-800">Gesture usage</h2>
          {gestureBreakdown.length === 0 ? (
            <p className="py-16 text-center text-sm text-ink-400">
              No gestures recorded yet — run a session to fill this in.
            </p>
          ) : (
            <div className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={gestureBreakdown} layout="vertical" margin={{ top: 0, right: 12, left: 10, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" horizontal={false} />
                  <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                  <YAxis
                    type="category"
                    dataKey="command"
                    width={92}
                    tick={{ fontSize: 11, fill: '#64748b' }}
                    axisLine={false}
                    tickLine={false}
                    tickFormatter={(value) => COMMANDS[value]?.label || value}
                  />
                  <Tooltip
                    cursor={{ fill: '#f1f5f9' }}
                    contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12 }}
                    formatter={(value) => [value, 'Times used']}
                    labelFormatter={(value) => COMMANDS[value]?.label || value}
                  />
                  <Bar dataKey="count" radius={[0, 6, 6, 0]}>
                    {gestureBreakdown.map((entry, index) => (
                      <Cell key={entry.command} fill={CHART_COLOURS[index % CHART_COLOURS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      <div className="mt-6">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-ink-800">Recent presentations</h2>
          <Link to="/presentations" className="text-xs font-medium text-brand-600 hover:text-brand-700">
            View library
          </Link>
        </div>

        {recentPresentations.length === 0 ? (
          <EmptyState
            icon={Layers}
            title="No presentations yet"
            description="Upload a PDF or PowerPoint file and VisionX will read its slide count and render previews."
            action={
              <Link to="/upload" className="btn-primary">
                <Upload size={16} /> Upload your first presentation
              </Link>
            }
          />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {recentPresentations.map((presentation) => (
              <PresentationCard key={presentation.id} presentation={presentation} />
            ))}
          </div>
        )}
      </div>

      <div className="mt-6 flex flex-wrap gap-2">
        {QUICK_ACTIONS.map(({ to, label, icon: Icon }) => (
          <Link key={to} to={to} className="btn-secondary">
            <Icon size={15} /> {label}
          </Link>
        ))}
      </div>
    </div>
  )
}
