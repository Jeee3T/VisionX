import { useCallback, useEffect, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { BarChart3 } from 'lucide-react'
import PageHeader from '../components/PageHeader'
import { EmptyState, ErrorState, Loader } from '../components/Feedback'
import { analyticsApi } from '../services/endpoints'
import { CHART_COLOURS } from '../utils/constants'
import { formatDate } from '../utils/format'

export default function Analytics() {
  const [gestures, setGestures] = useState(null)
  const [decks, setDecks] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    Promise.all([analyticsApi.gestures(), analyticsApi.presentations()])
      .then(([gestureResponse, deckResponse]) => {
        setGestures(gestureResponse.data)
        setDecks(deckResponse.data.presentations)
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(load, [load])

  if (loading) return <Loader label="Crunching your session data…" />
  if (error) return <ErrorState message={error} onRetry={load} />

  const hasData = gestures.total > 0 || decks.length > 0

  if (!hasData) {
    return (
      <div className="animate-fade-in">
        <PageHeader title="Analytics" subtitle="Aggregated from your real presentation sessions." />
        <EmptyState
          icon={BarChart3}
          title="Nothing to analyse yet"
          description="Run a gesture-controlled session and this page fills itself in — gesture mix, session length and per-deck usage."
        />
      </div>
    )
  }

  const timeline = (gestures.timeline || []).map((point) => ({
    ...point,
    label: formatDate(point.date).replace(/,.*/, ''),
  }))

  return (
    <div className="animate-fade-in">
      <PageHeader title="Analytics" subtitle="Aggregated from your real presentation sessions." />

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="card p-5">
          <h2 className="mb-4 text-sm font-semibold text-ink-800">Gesture mix</h2>
          {gestures.gestures.length === 0 ? (
            <p className="py-20 text-center text-sm text-ink-400">No gestures recorded yet.</p>
          ) : (
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={gestures.gestures}
                    dataKey="count"
                    nameKey="label"
                    innerRadius={54}
                    outerRadius={88}
                    paddingAngle={3}
                  >
                    {gestures.gestures.map((entry, index) => (
                      <Cell key={entry.command} fill={CHART_COLOURS[index % CHART_COLOURS.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12 }}
                    formatter={(value, name, entry) => [`${value} (${entry.payload.share}%)`, name]}
                  />
                  <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        <div className="card p-5">
          <h2 className="mb-4 text-sm font-semibold text-ink-800">Commands per session</h2>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={timeline} margin={{ top: 4, right: 8, left: -22, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12 }} />
                <Line type="monotone" dataKey="commands" stroke="#6366f1" strokeWidth={2} dot={{ r: 3 }} />
                <Line type="monotone" dataKey="minutes" stroke="#8b5cf6" strokeWidth={2} dot={{ r: 3 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      <div className="card mt-4 p-5">
        <h2 className="mb-4 text-sm font-semibold text-ink-800">Most-presented decks</h2>
        {decks.length === 0 ? (
          <p className="py-16 text-center text-sm text-ink-400">No presentation sessions recorded yet.</p>
        ) : (
          <>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={decks} margin={{ top: 4, right: 8, left: -22, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                  <XAxis dataKey="title" tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                  <Tooltip cursor={{ fill: '#f8fafc' }} contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12 }} />
                  <Bar dataKey="sessions" fill="#6366f1" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>

            <div className="mt-5 overflow-x-auto">
              <table className="w-full min-w-[34rem] text-sm">
                <thead>
                  <tr className="border-b border-ink-200/70 text-left text-xs uppercase tracking-wide text-ink-500">
                    <th className="py-2.5 pr-4 font-medium">Presentation</th>
                    <th className="py-2.5 pr-4 font-medium">Sessions</th>
                    <th className="py-2.5 pr-4 font-medium">Minutes</th>
                    <th className="py-2.5 pr-4 font-medium">Slides</th>
                    <th className="py-2.5 font-medium">Last used</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ink-100">
                  {decks.map((deck) => (
                    <tr key={deck.presentationId}>
                      <td className="py-3 pr-4 font-medium text-ink-800">{deck.title}</td>
                      <td className="py-3 pr-4 text-ink-600">{deck.sessions}</td>
                      <td className="py-3 pr-4 text-ink-600">{deck.minutes}</td>
                      <td className="py-3 pr-4 text-ink-600">{deck.slidesNavigated}</td>
                      <td className="py-3 text-ink-500">{formatDate(deck.lastUsed)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
