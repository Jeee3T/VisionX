import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { History as HistoryIcon, Play } from 'lucide-react'
import PageHeader from '../components/PageHeader'
import { EmptyState, ErrorState, Loader } from '../components/Feedback'
import { sessionApi } from '../services/endpoints'
import { COMMANDS } from '../utils/constants'
import { formatDateTime, formatDuration } from '../utils/format'

const STATUS_STYLES = {
  COMPLETED: 'bg-emerald-50 text-emerald-700',
  ACTIVE: 'bg-brand-50 text-brand-700',
  READY: 'bg-ink-100 text-ink-600',
}

export default function History() {
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    sessionApi
      .list(100)
      .then((response) => setSessions(response.data.sessions))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(load, [load])

  if (loading) return <Loader label="Loading your session history…" />
  if (error) return <ErrorState message={error} onRetry={load} />

  return (
    <div className="animate-fade-in">
      <PageHeader title="Session history" subtitle="Every presentation session you have run with VisionX." />

      {sessions.length === 0 ? (
        <EmptyState
          icon={HistoryIcon}
          title="No sessions yet"
          description="Start a gesture-controlled session and it will appear here with its duration, slides and gesture usage."
          action={
            <Link to="/presentations" className="btn-primary">
              <Play size={16} /> Start a session
            </Link>
          }
        />
      ) : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[42rem] text-sm">
              <thead>
                <tr className="border-b border-ink-200/70 bg-ink-50/60 text-left text-xs uppercase tracking-wide text-ink-500">
                  <th className="px-5 py-3 font-medium">Presentation</th>
                  <th className="px-5 py-3 font-medium">Started</th>
                  <th className="px-5 py-3 font-medium">Duration</th>
                  <th className="px-5 py-3 font-medium">Slides</th>
                  <th className="px-5 py-3 font-medium">Gestures</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-100">
                {sessions.map((session) => {
                  const counts = session.gestureCounts || {}
                  const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0]
                  return (
                    <tr key={session.id} className="transition-colors hover:bg-ink-50/60">
                      <td className="px-5 py-3.5">
                        {session.presentationId ? (
                          <Link
                            to={`/presentations/${session.presentationId}`}
                            className="font-medium text-ink-800 hover:text-brand-700"
                          >
                            {session.presentationTitle || 'Untitled'}
                          </Link>
                        ) : (
                          <span className="text-ink-600">{session.presentationTitle || 'Free session'}</span>
                        )}
                      </td>
                      <td className="px-5 py-3.5 text-ink-500">{formatDateTime(session.startTime)}</td>
                      <td className="px-5 py-3.5 text-ink-600">{formatDuration(session.duration)}</td>
                      <td className="px-5 py-3.5 text-ink-600">{session.slidesNavigated || 0}</td>
                      <td className="px-5 py-3.5">
                        <span className="text-ink-600">{session.commandsFired || 0}</span>
                        {top && (
                          <span className="ml-2 text-xs text-ink-400">
                            mostly {COMMANDS[top[0]]?.label?.toLowerCase() || top[0]}
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-3.5">
                        <span className={`chip ${STATUS_STYLES[session.status] || STATUS_STYLES.READY}`}>
                          {session.status}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
