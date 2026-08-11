import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Library as LibraryIcon, Search, Upload } from 'lucide-react'
import PageHeader from '../components/PageHeader'
import PresentationCard from '../components/PresentationCard'
import ConfirmDialog from '../components/ConfirmDialog'
import { EmptyState, ErrorState, Loader } from '../components/Feedback'
import { presentationApi } from '../services/endpoints'
import { useToast } from '../context/ToastContext'

export default function Library() {
  const toast = useToast()
  const [items, setItems] = useState([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [pendingDelete, setPendingDelete] = useState(null)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback((term = '') => {
    setLoading(true)
    setError(null)
    presentationApi
      .list(term)
      .then((response) => setItems(response.data.presentations))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    const timer = setTimeout(() => load(search), search ? 300 : 0)
    return () => clearTimeout(timer)
  }, [search, load])

  const confirmDelete = async () => {
    setDeleting(true)
    try {
      await presentationApi.remove(pendingDelete.id)
      toast.success(`"${pendingDelete.title}" deleted.`)
      setPendingDelete(null)
      load(search)
    } catch (err) {
      toast.error(err.message)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Presentation library"
        subtitle="Pick a deck and start a gesture-controlled session."
        actions={
          <Link to="/upload" className="btn-primary">
            <Upload size={16} /> Upload
          </Link>
        }
      />

      <div className="relative mb-5 max-w-sm">
        <Search size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-400" />
        <input
          className="input pl-10"
          placeholder="Search by title…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {loading ? (
        <Loader label="Loading your library…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => load(search)} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={LibraryIcon}
          title={search ? 'No matches' : 'No presentations yet'}
          description={
            search
              ? `Nothing in your library matches "${search}".`
              : 'Upload a PDF or PowerPoint file to get started. VisionX reads the slide count and renders previews automatically.'
          }
          action={
            search ? (
              <button className="btn-secondary" onClick={() => setSearch('')}>
                Clear search
              </button>
            ) : (
              <Link to="/upload" className="btn-primary">
                <Upload size={16} /> Upload your first presentation
              </Link>
            )
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {items.map((presentation) => (
            <PresentationCard key={presentation.id} presentation={presentation} onDelete={setPendingDelete} />
          ))}
        </div>
      )}

      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title="Delete this presentation?"
        message={`"${pendingDelete?.title}" and its slide previews and annotations will be removed. Session history is kept.`}
        confirmLabel="Delete"
        busy={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  )
}
