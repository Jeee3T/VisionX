import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'

export function Loader({ label = 'Loading…', className = '' }) {
  return (
    <div className={`flex items-center justify-center gap-2.5 py-16 text-sm text-ink-500 ${className}`}>
      <Loader2 size={18} className="animate-spin text-brand-500" />
      {label}
    </div>
  )
}

/** Calm, actionable error surface - never a raw stack trace or error dump. */
export function ErrorState({ title = 'Something went wrong', message, onRetry }) {
  return (
    <div className="card flex flex-col items-center gap-3 p-10 text-center">
      <span className="rounded-xl bg-amber-50 p-3 text-amber-600">
        <AlertTriangle size={22} />
      </span>
      <div>
        <h3 className="text-base font-semibold text-ink-800">{title}</h3>
        {message && <p className="mt-1 max-w-md text-sm text-ink-500">{message}</p>}
      </div>
      {onRetry && (
        <button onClick={onRetry} className="btn-secondary mt-1">
          <RefreshCw size={15} /> Try again
        </button>
      )}
    </div>
  )
}

export function EmptyState({ icon: Icon, title, description, action }) {
  return (
    <div className="card flex flex-col items-center gap-4 px-6 py-14 text-center">
      <div className="relative">
        <div className="absolute inset-0 rounded-3xl bg-brand-gradient opacity-10 blur-xl" />
        <span className="relative flex h-16 w-16 items-center justify-center rounded-3xl bg-brand-soft text-brand-600">
          {Icon && <Icon size={26} />}
        </span>
      </div>
      <div>
        <h3 className="text-base font-semibold text-ink-800">{title}</h3>
        {description && <p className="mx-auto mt-1.5 max-w-sm text-sm text-ink-500">{description}</p>}
      </div>
      {action}
    </div>
  )
}
