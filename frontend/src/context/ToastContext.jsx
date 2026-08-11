import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react'

const ToastContext = createContext(null)

const TONES = {
  success: { icon: CheckCircle2, ring: 'border-emerald-200', tint: 'bg-emerald-50 text-emerald-700' },
  error: { icon: AlertCircle, ring: 'border-red-200', tint: 'bg-red-50 text-red-700' },
  info: { icon: Info, ring: 'border-brand-200', tint: 'bg-brand-50 text-brand-700' },
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])

  const dismiss = useCallback((id) => setToasts((list) => list.filter((t) => t.id !== id)), [])

  const push = useCallback(
    (message, tone = 'info', timeout = 4000) => {
      const id = Math.random().toString(36).slice(2)
      setToasts((list) => [...list, { id, message, tone }])
      if (timeout) setTimeout(() => dismiss(id), timeout)
      return id
    },
    [dismiss],
  )

  const value = useMemo(
    () => ({
      toast: push,
      success: (message) => push(message, 'success'),
      error: (message) => push(message, 'error', 6000),
      info: (message) => push(message, 'info'),
    }),
    [push],
  )

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[100] flex w-full max-w-sm flex-col gap-2">
        {toasts.map((item) => {
          const tone = TONES[item.tone] || TONES.info
          const Icon = tone.icon
          return (
            <div
              key={item.id}
              className={`pointer-events-auto flex animate-fade-in items-start gap-3 rounded-xl border ${tone.ring} bg-white p-3.5 shadow-lift`}
            >
              <span className={`rounded-lg p-1.5 ${tone.tint}`}>
                <Icon size={16} />
              </span>
              <p className="flex-1 pt-0.5 text-sm text-ink-700">{item.message}</p>
              <button onClick={() => dismiss(item.id)} className="rounded-md p-1 text-ink-400 hover:bg-ink-100">
                <X size={14} />
              </button>
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}

export const useToast = () => {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast must be used inside <ToastProvider>')
  return context
}
