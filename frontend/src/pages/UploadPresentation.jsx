import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileText, Loader2, UploadCloud, X } from 'lucide-react'
import PageHeader from '../components/PageHeader'
import { presentationApi } from '../services/endpoints'
import { useToast } from '../context/ToastContext'
import { formatBytes } from '../utils/format'

const ACCEPTED = ['.pdf', '.pptx', '.ppt']
const MAX_MB = 50

export default function UploadPresentation() {
  const navigate = useNavigate()
  const toast = useToast()
  const inputRef = useRef(null)
  const [file, setFile] = useState(null)
  const [title, setTitle] = useState('')
  const [dragging, setDragging] = useState(false)
  const [progress, setProgress] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const pick = (selected) => {
    if (!selected) return
    const extension = `.${selected.name.split('.').pop().toLowerCase()}`
    if (!ACCEPTED.includes(extension)) {
      setError(`Unsupported file type. Accepted: ${ACCEPTED.join(', ')}`)
      return
    }
    if (selected.size > MAX_MB * 1024 * 1024) {
      setError(`That file is ${formatBytes(selected.size)} — the limit is ${MAX_MB} MB.`)
      return
    }
    setError('')
    setFile(selected)
    if (!title) setTitle(selected.name.replace(/\.[^.]+$/, ''))
  }

  const submit = async (event) => {
    event.preventDefault()
    if (!file) return setError('Choose a presentation file first.')

    const formData = new FormData()
    formData.append('file', file)
    formData.append('title', title)

    setBusy(true)
    setError('')
    try {
      const response = await presentationApi.upload(formData, setProgress)
      toast.success('Presentation uploaded.')
      navigate(`/presentations/${response.data.presentation.id}`)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
      setProgress(0)
    }
  }

  return (
    <div className="animate-fade-in">
      <PageHeader title="Upload a presentation" subtitle="PDF and PowerPoint files up to 50 MB." />

      <form onSubmit={submit} className="grid gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <div
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragging(false)
              pick(e.dataTransfer.files?.[0])
            }}
            onClick={() => inputRef.current?.click()}
            className={`card flex cursor-pointer flex-col items-center justify-center gap-3 px-6 py-16 text-center transition-colors ${
              dragging ? 'border-brand-400 bg-brand-50/60' : 'hover:border-brand-300 hover:bg-brand-50/30'
            }`}
          >
            <span className="rounded-2xl bg-brand-50 p-4 text-brand-600">
              <UploadCloud size={26} />
            </span>
            <div>
              <p className="text-sm font-semibold text-ink-800">
                Drop your file here, or <span className="text-brand-600">browse</span>
              </p>
              <p className="mt-1 text-xs text-ink-400">PDF, PPTX or PPT · up to {MAX_MB} MB</p>
            </div>
            <input
              ref={inputRef}
              type="file"
              accept={ACCEPTED.join(',')}
              className="hidden"
              onChange={(e) => pick(e.target.files?.[0])}
            />
          </div>

          {file && (
            <div className="card mt-4 flex items-center gap-3 p-4">
              <span className="rounded-xl bg-brand-50 p-2.5 text-brand-600">
                <FileText size={18} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-ink-800">{file.name}</p>
                <p className="text-xs text-ink-400">{formatBytes(file.size)}</p>
              </div>
              <button
                type="button"
                onClick={() => setFile(null)}
                className="rounded-lg p-2 text-ink-400 hover:bg-ink-100 hover:text-red-600"
              >
                <X size={16} />
              </button>
            </div>
          )}

          {busy && progress > 0 && (
            <div className="mt-4">
              <div className="h-1.5 overflow-hidden rounded-full bg-ink-100">
                <div className="h-full rounded-full bg-brand-gradient transition-[width]" style={{ width: `${progress}%` }} />
              </div>
              <p className="mt-1.5 text-xs text-ink-400">Uploading… {progress}%</p>
            </div>
          )}
        </div>

        <div className="card h-fit p-5">
          <label className="label" htmlFor="title">Title</label>
          <input
            id="title"
            className="input"
            placeholder="Quarterly review"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <p className="mt-2 text-xs text-ink-400">
            Slide count and previews are read from the file itself after upload.
          </p>

          {error && (
            <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3.5 py-2.5 text-xs text-red-700">
              {error}
            </div>
          )}

          <button type="submit" disabled={busy || !file} className="btn-primary mt-5 w-full py-3">
            {busy ? <Loader2 size={17} className="animate-spin" /> : <UploadCloud size={17} />}
            {busy ? 'Uploading…' : 'Upload presentation'}
          </button>
        </div>
      </form>
    </div>
  )
}
