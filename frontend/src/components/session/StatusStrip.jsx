import { useEffect, useState } from 'react'
import { Circle, LogOut, Radio } from 'lucide-react'
import { COMMANDS, STATUS_TONE } from '../../utils/constants'
import { formatClock } from '../../utils/format'

const TONE_STYLES = {
  success: 'bg-emerald-500',
  holding: 'bg-brand-500',
  warning: 'bg-amber-500',
  idle: 'bg-ink-300',
}

/**
 * The session screen's only persistent chrome.
 *
 * Confidence reads as a colour pulse, not a number - the numbers appear on
 * hover for debugging. The whole strip fades out while the presenter is idle so
 * the slide keeps the stage.
 */
export default function StatusStrip({ telemetry, lastCommand, connected, elapsed, hidden, onEnd }) {
  const [flash, setFlash] = useState(null)

  useEffect(() => {
    if (!lastCommand?.receivedAt) return undefined
    setFlash(lastCommand)
    const timer = setTimeout(() => setFlash(null), 2200)
    return () => clearTimeout(timer)
  }, [lastCommand?.receivedAt])

  const status = telemetry?.status || 'IDLE'
  const tone = STATUS_TONE[status] || STATUS_TONE.IDLE
  const confidence = Math.round((telemetry?.confidence || 0) * 100)
  const gesture = telemetry?.handDetected ? telemetry.gesture : 'No hand'
  const commandMeta = flash ? COMMANDS[flash.command] : null

  return (
    <div
      className={`pointer-events-auto flex items-center gap-3 rounded-2xl border border-white/10 bg-ink-900/85 px-4 py-2.5 text-sm text-white shadow-lift backdrop-blur transition-all duration-500 md:gap-5 ${
        hidden ? 'pointer-events-none translate-y-3 opacity-0' : 'opacity-100'
      }`}
    >
      {/* Camera */}
      <span className="flex items-center gap-2" title={connected ? 'Telemetry stream connected' : 'Reconnecting…'}>
        <span className="relative flex h-2.5 w-2.5">
          {connected && <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-400 animate-pulse-ring" />}
          <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${connected ? 'bg-emerald-400' : 'bg-amber-400'}`} />
        </span>
        <span className="hidden text-white/70 sm:inline">{connected ? 'Live' : 'Reconnecting'}</span>
      </span>

      <span className="h-4 w-px bg-white/15" />

      {/* Gesture + confidence pulse. Numbers on hover only. */}
      <span className="group flex items-center gap-2" title={`${gesture} · ${confidence}% confidence · ${tone.text}`}>
        <span className={`h-2.5 w-2.5 rounded-full transition-colors ${TONE_STYLES[tone.tone]}`} />
        <span className="font-medium">{gesture.replace(/_/g, ' ').toLowerCase()}</span>
        <span className="hidden text-xs text-white/50 group-hover:inline">{confidence}%</span>
      </span>

      {/* Hold progress */}
      {telemetry?.progress > 0 && telemetry.progress < 1 && (
        <span className="hidden h-1 w-14 overflow-hidden rounded-full bg-white/20 sm:block">
          <span
            className="block h-full rounded-full bg-brand-400 transition-[width] duration-100"
            style={{ width: `${telemetry.progress * 100}%` }}
          />
        </span>
      )}

      <span className="h-4 w-px bg-white/15" />

      {/* Last executed command */}
      <span className="flex min-w-[7.5rem] items-center gap-2">
        {commandMeta ? (
          <>
            <commandMeta.icon size={15} className="text-emerald-400" />
            <span className="animate-fade-in text-white">{commandMeta.label}</span>
          </>
        ) : (
          <>
            <Circle size={9} className="text-white/25" />
            <span className="text-white/45">{tone.text}</span>
          </>
        )}
      </span>

      <span className="h-4 w-px bg-white/15" />

      <span className="flex items-center gap-2 tabular-nums text-white/80">
        <Radio size={14} className="text-white/40" />
        {formatClock(elapsed)}
      </span>

      <button
        onClick={onEnd}
        className="ml-1 inline-flex items-center gap-1.5 rounded-xl border border-white/15 px-3 py-1.5 text-xs font-medium text-white/80 transition-colors hover:border-red-400/60 hover:bg-red-500/15 hover:text-white"
      >
        <LogOut size={14} /> End
      </button>
    </div>
  )
}
