import { Link } from 'react-router-dom'
import { FileText, Layers, Play, Trash2 } from 'lucide-react'
import { API_BASE, getToken } from '../services/api'
import { formatDate } from '../utils/format'

export default function PresentationCard({ presentation, onDelete }) {
  const hasThumb = (presentation.thumbnails || []).length > 0
  const thumbUrl = hasThumb
    ? `${API_BASE}/presentations/${presentation.id}/slides/1?token=${getToken()}`
    : null

  return (
    <div className="card group overflow-hidden transition-shadow hover:shadow-lift">
      <Link to={`/presentations/${presentation.id}`} className="block">
        <div className="relative flex h-36 items-center justify-center overflow-hidden bg-brand-soft">
          {thumbUrl ? (
            <img
              src={thumbUrl}
              alt=""
              className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
            />
          ) : (
            <div className="flex flex-col items-center gap-2 text-brand-500">
              <FileText size={26} />
              <span className="text-[11px] uppercase tracking-wide text-brand-400">
                {presentation.fileType || 'file'}
              </span>
            </div>
          )}
        </div>
      </Link>

      <div className="p-4">
        <Link to={`/presentations/${presentation.id}`}>
          <h3 className="truncate text-sm font-semibold text-ink-900 hover:text-brand-700">{presentation.title}</h3>
        </Link>
        <div className="mt-1.5 flex items-center gap-3 text-xs text-ink-400">
          <span className="inline-flex items-center gap-1">
            <Layers size={13} /> {presentation.totalSlides || 0} slides
          </span>
          <span>{formatDate(presentation.uploadedAt)}</span>
        </div>

        <div className="mt-4 flex items-center gap-2">
          <Link to={`/session/new?presentationId=${presentation.id}`} className="btn-primary flex-1 py-2 text-xs">
            <Play size={14} /> Start
          </Link>
          {onDelete && (
            <button
              onClick={() => onDelete(presentation)}
              title="Delete presentation"
              className="btn-ghost px-2.5 py-2 text-ink-400 hover:text-red-600"
            >
              <Trash2 size={15} />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
