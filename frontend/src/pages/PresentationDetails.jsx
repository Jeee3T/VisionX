import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Check, Clock, Layers, PenLine, Pencil, Play, Trash2 } from 'lucide-react'
import PageHeader from '../components/PageHeader'
import ConfirmDialog from '../components/ConfirmDialog'
import { ErrorState, Loader } from '../components/Feedback'
import { presentationApi } from '../services/endpoints'
import { API_BASE, getToken } from '../services/api'
import { useToast } from '../context/ToastContext'
import { formatDate, formatDateTime, formatDuration } from '../utils/format'

export default function PresentationDetails() {
  const { id } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [presentation, setPresentation] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    presentationApi
      .get(id)
      .then((response) => {
        setPresentation(response.data.presentation)
        setTitle(response.data.presentation.title)
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [id])

  useEffect(load, [load])

  const saveTitle = async () => {
    try {
      const response = await presentationApi.update(id, { title })
      setPresentation(response.data.presentation)
      setEditing(false)
      toast.success('Title updated.')
    } catch (err) {
      toast.error(err.message)
    }
  }

  const remove = async () => {
    setDeleting(true)
    try {
      await presentationApi.remove(id)
      toast.success('Presentation deleted.')
      navigate('/presentations')
    } catch (err) {
      toast.error(err.message)
      setDeleting(false)
    }
  }

  if (loading) return <Loader />
  if (error) return <ErrorState message={error} onRetry={load} />

  const thumbnails = presentation.thumbnails || []

  return (
    <div className="animate-fade-in">
      <Link to="/presentations" className="mb-4 inline-flex items-center gap-1.5 text-sm text-ink-500 hover:text-ink-800">
        <ArrowLeft size={15} /> Back to library
      </Link>

      <PageHeader
        title={
          editing ? (
            <span className="flex items-center gap-2">
              <input className="input max-w-sm" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
              <button onClick={saveTitle} className="btn-primary px-3 py-2">
                <Check size={15} />
              </button>
            </span>
          ) : (
            <span className="flex items-center gap-2">
              {presentation.title}
              <button onClick={() => setEditing(true)} className="rounded-lg p-1.5 text-ink-400 hover:bg-ink-100">
                <Pencil size={15} />
              </button>
            </span>
          )
        }
        subtitle={`${presentation.fileName} · uploaded ${formatDate(presentation.uploadedAt)}`}
        actions={
          <>
            <button onClick={() => setConfirming(true)} className="btn-danger">
              <Trash2 size={15} /> Delete
            </button>
            <Link to={`/session/new?presentationId=${presentation.id}`} className="btn-primary">
              <Play size={16} /> Start session
            </Link>
          </>
        }
      />

      <div className="grid gap-4 sm:grid-cols-3">
        <div className="card flex items-center gap-3 p-4">
          <span className="rounded-xl bg-brand-50 p-2.5 text-brand-600"><Layers size={18} /></span>
          <div>
            <p className="text-xs text-ink-500">Slides</p>
            <p className="text-lg font-semibold text-ink-900">{presentation.totalSlides || '—'}</p>
          </div>
        </div>
        <div className="card flex items-center gap-3 p-4">
          <span className="rounded-xl bg-violet-50 p-2.5 text-violet-600"><Clock size={18} /></span>
          <div>
            <p className="text-xs text-ink-500">Sessions</p>
            <p className="text-lg font-semibold text-ink-900">{(presentation.recentSessions || []).length}</p>
          </div>
        </div>
        <div className="card flex items-center gap-3 p-4">
          <span className="rounded-xl bg-amber-50 p-2.5 text-amber-600"><PenLine size={18} /></span>
          <div>
            <p className="text-xs text-ink-500">Annotations</p>
            <p className="text-lg font-semibold text-ink-900">{presentation.annotationCount || 0}</p>
          </div>
        </div>
      </div>

      {!presentation.fileExists && (
        <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          The stored file is missing from the server. Re-upload it before your next session.
        </div>
      )}

      <div className="mt-6 grid gap-5 lg:grid-cols-3">
        <div className="card p-5 lg:col-span-2">
          <h2 className="mb-4 text-sm font-semibold text-ink-800">Slide previews</h2>
          {thumbnails.length === 0 ? (
            <p className="py-10 text-center text-sm text-ink-400">
              No previews for this file. PowerPoint files need Microsoft PowerPoint installed on the server to
              render previews — gesture control works either way.
            </p>
          ) : (
            <div className="grid max-h-[26rem] grid-cols-2 gap-3 overflow-y-auto pr-1 sm:grid-cols-3">
              {thumbnails.map((_, index) => (
                <div key={index} className="overflow-hidden rounded-xl border border-ink-200">
                  <img
                    src={`${API_BASE}/presentations/${presentation.id}/slides/${index + 1}?token=${getToken()}`}
                    alt={`Slide ${index + 1}`}
                    loading="lazy"
                    className="block aspect-video w-full object-cover"
                  />
                  <p className="bg-ink-50 px-2 py-1 text-[11px] text-ink-500">Slide {index + 1}</p>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card p-5">
          <h2 className="mb-4 text-sm font-semibold text-ink-800">Recent sessions</h2>
          {(presentation.recentSessions || []).length === 0 ? (
            <p className="py-10 text-center text-sm text-ink-400">No sessions yet for this presentation.</p>
          ) : (
            <ul className="divide-y divide-ink-100">
              {presentation.recentSessions.map((session) => (
                <li key={session.id} className="flex items-center justify-between py-3 text-sm">
                  <div>
                    <p className="text-ink-800">{formatDateTime(session.startTime)}</p>
                    <p className="text-xs text-ink-400">
                      {session.slidesNavigated || 0} slides · {session.annotationsMade || 0} annotations
                    </p>
                  </div>
                  <span className="chip bg-ink-100 text-ink-600">{formatDuration(session.duration)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={confirming}
        title="Delete this presentation?"
        message="The file, its previews and its annotations will be removed. Session history is kept."
        confirmLabel="Delete"
        busy={deleting}
        onConfirm={remove}
        onCancel={() => setConfirming(false)}
      />
    </div>
  )
}
