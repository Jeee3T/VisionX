import { useCallback, useState } from 'react'
import { Check, Loader2, Mic, MicOff, X } from 'lucide-react'
import useVoiceCapture from '../../hooks/useVoiceCapture'
import { voiceApi } from '../../services/endpoints'

/**
 * Push-to-talk during a live session.
 *
 * Hold the button, say the command, release. Nothing is recorded between
 * presses. A high-confidence command runs immediately; a middling one appears
 * here as a confirmation the presenter can accept or dismiss; a low-confidence
 * one is discarded silently and never reaches PowerPoint.
 */
export default function VoicePanel({ sessionId, hidden, onCommand }) {
  const [state, setState] = useState('idle') // idle | processing | done
  const [decision, setDecision] = useState(null)
  const [failure, setFailure] = useState(null)

  const send = useCallback(
    async (blob) => {
      setState('processing')
      setFailure(null)
      try {
        const response = await voiceApi.utterance(blob, { sessionId })
        setDecision(response.data)
        setState('done')
        if (response.data.executed && onCommand) onCommand(response.data)
      } catch (err) {
        setFailure(err.message)
        setState('idle')
      }
    },
    [sessionId, onCommand],
  )

  const { supported, recording, level, error, start, stop } = useVoiceCapture({ onUtterance: send })

  const confirm = async () => {
    if (!decision?.transcript) return
    setState('processing')
    try {
      const response = await voiceApi.confirm(decision.transcript, sessionId)
      setDecision(response.data)
      if (onCommand) onCommand(response.data)
    } catch (err) {
      setFailure(err.message)
    } finally {
      setState('done')
    }
  }

  if (!supported) return null

  const busy = state === 'processing'
  const needsConfirmation = decision?.requiresConfirmation && !decision?.executed

  return (
    <div
      className={`flex flex-col items-center gap-2 transition-opacity duration-500 ${
        hidden && !recording && !needsConfirmation ? 'opacity-0' : 'opacity-100'
      }`}
    >
      {/* What VisionX heard and decided. */}
      {(decision || failure) && (
        <div className="max-w-md rounded-2xl border border-white/10 bg-ink-900/85 px-3.5 py-2.5 text-center backdrop-blur">
          {failure ? (
            <p className="text-xs text-amber-300">{failure}</p>
          ) : (
            <>
              <p className="text-xs text-white/50">
                I heard: &ldquo;{decision.transcript || '…'}&rdquo;
              </p>
              <p className="mt-0.5 text-sm font-medium text-white">
                {decision.commandLabel || decision.intentLabel}
                {decision.parameters?.slideNumber ? ` ${decision.parameters.slideNumber}` : ''}
                {decision.parameters?.count > 1 ? ` x${decision.parameters.count}` : ''}
                <span className="ml-2 text-xs font-normal text-white/45">
                  {Math.round(decision.probability * 100)}%
                </span>
              </p>

              {needsConfirmation ? (
                <div className="mt-2 flex justify-center gap-2">
                  <button onClick={confirm} disabled={busy} className="btn-primary px-3 py-1.5 text-xs">
                    <Check size={13} /> Run it
                  </button>
                  <button
                    onClick={() => setDecision(null)}
                    className="btn-ghost px-3 py-1.5 text-xs text-white/70 hover:bg-white/10"
                  >
                    <X size={13} /> Dismiss
                  </button>
                </div>
              ) : (
                <p className="mt-0.5 text-[11px] text-white/40">
                  {decision.executed ? 'Sent to PowerPoint' : decision.message}
                </p>
              )}
            </>
          )}
        </div>
      )}

      {error && <p className="max-w-xs text-center text-[11px] text-amber-300">{error}</p>}

      {/* Push-to-talk. Held, never latched: the microphone is only live while pressed. */}
      <button
        onMouseDown={start}
        onMouseUp={stop}
        onMouseLeave={() => recording && stop()}
        onTouchStart={(event) => {
          event.preventDefault()
          start()
        }}
        onTouchEnd={stop}
        disabled={busy}
        title="Hold to speak a command"
        className={`relative flex h-12 w-12 items-center justify-center rounded-full transition-all ${
          recording
            ? 'bg-rose-500 text-white shadow-lift'
            : 'border border-white/10 bg-ink-900/85 text-white/70 backdrop-blur hover:text-white'
        }`}
      >
        {busy ? (
          <Loader2 size={19} className="animate-spin" />
        ) : recording ? (
          <Mic size={19} />
        ) : (
          <MicOff size={19} />
        )}
        {recording && (
          <span
            className="pointer-events-none absolute inset-0 rounded-full border-2 border-rose-300"
            style={{ transform: `scale(${1 + level * 0.5})`, opacity: 1 - level * 0.6 }}
          />
        )}
      </button>
      <p className="text-[11px] text-white/35">
        {recording ? 'Listening — release to send' : 'Hold to speak'}
      </p>
    </div>
  )
}
