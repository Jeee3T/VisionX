import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, Ear, Loader2, Mic, MicOff, X } from 'lucide-react'
import useContinuousVoice from '../../hooks/useContinuousVoice'
import { voiceApi } from '../../services/endpoints'

/**
 * Continuous voice control during a live session.
 *
 * The presenter turns the microphone on once and then never touches the web app
 * again. VisionX listens the whole time and acts only on what is addressed to it:
 *
 *     [Listening] -> "Vision" -> [Command] -> "go to next slide" -> "OK" -> run
 *                 <- back to listening
 *
 * Everything said between commands is ordinary speech and does nothing. A
 * completed command still goes through the same trained intent model as before,
 * so a middling-confidence one appears here as a confirmation rather than moving
 * the deck on its own.
 */
const WAKE_WORD = 'Vision'
const TERMINATOR = 'OK'

export default function VoicePanel({ sessionId, hidden, onCommand }) {
  const [decision, setDecision] = useState(null)
  const [wake, setWake] = useState(null)
  const [failure, setFailure] = useState(null)
  const [confirming, setConfirming] = useState(false)
  const [wanted, setWanted] = useState(false)

  // The last decision is cleared a few seconds after it lands so the overlay does
  // not sit on top of the slide for the rest of the talk.
  const clearTimer = useRef(null)
  useEffect(() => () => clearTimeout(clearTimer.current), [])

  const send = useCallback(
    async (blob) => {
      try {
        const response = await voiceApi.stream(blob, { sessionId })
        const data = response.data
        setFailure(null)
        setWake(data.wake || null)

        // Only a completed command produces a decision worth showing.
        if (data.wake?.action === 'EXECUTE' || data.executed || data.requiresConfirmation) {
          setDecision(data)
          clearTimeout(clearTimer.current)
          clearTimer.current = setTimeout(() => setDecision(null), 6000)
          if (data.executed && onCommand) onCommand(data)
        }
      } catch (err) {
        setFailure(err.message)
      }
    },
    [sessionId, onCommand],
  )

  const { supported, listening, level, busy, error, dropped, start, stop } = useContinuousVoice({
    onSegment: send,
    enabled: wanted,
  })

  const toggle = () => {
    if (listening) {
      setWanted(false)
      stop()
      voiceApi.resetWake().catch(() => {})
    } else {
      setWanted(true)
      start()
    }
  }

  const confirm = async () => {
    if (!decision?.transcript) return
    setConfirming(true)
    try {
      const response = await voiceApi.confirm(decision.transcript, sessionId)
      setDecision(response.data)
      if (onCommand) onCommand(response.data)
    } catch (err) {
      setFailure(err.message)
    } finally {
      setConfirming(false)
    }
  }

  if (!supported) return null

  const capturing = wake?.state === 'CAPTURING'
  const needsConfirmation = decision?.requiresConfirmation && !decision?.executed
  const showing = decision || failure || capturing

  return (
    <div
      className={`flex flex-col items-center gap-2 transition-opacity duration-500 ${
        hidden && !showing && !listening ? 'opacity-0' : 'opacity-100'
      }`}
    >
      {/* Mid-command: show what has been captured so far, so a presenter who
          forgets to say "OK" can see that VisionX is still waiting. */}
      {capturing && !decision && (
        <div className="max-w-md rounded-2xl border border-brand-400/30 bg-ink-900/85 px-3.5 py-2 text-center backdrop-blur">
          <p className="text-xs text-brand-300">
            Listening for your command — say &ldquo;{TERMINATOR}&rdquo; to run it
          </p>
          {wake?.buffered && <p className="mt-0.5 text-sm text-white">{wake.buffered}</p>}
        </div>
      )}

      {/* What VisionX heard and decided. */}
      {(decision || failure) && (
        <div className="max-w-md rounded-2xl border border-white/10 bg-ink-900/85 px-3.5 py-2.5 text-center backdrop-blur">
          {failure ? (
            <p className="text-xs text-amber-300">{failure}</p>
          ) : (
            <>
              <p className="text-xs text-white/50">
                I heard: &ldquo;{decision.wake?.command || decision.transcript || '…'}&rdquo;
              </p>
              <p className="mt-0.5 text-sm font-medium text-white">
                {decision.commandLabel || decision.intentLabel || decision.message}
                {decision.parameters?.slideNumber ? ` ${decision.parameters.slideNumber}` : ''}
                {decision.parameters?.count > 1 ? ` x${decision.parameters.count}` : ''}
                {decision.probability != null && (
                  <span className="ml-2 text-xs font-normal text-white/45">
                    {Math.round(decision.probability * 100)}%
                  </span>
                )}
              </p>

              {needsConfirmation ? (
                <div className="mt-2 flex justify-center gap-2">
                  <button onClick={confirm} disabled={confirming} className="btn-primary px-3 py-1.5 text-xs">
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
                  {decision.executed ? 'Done' : decision.message}
                </p>
              )}
            </>
          )}
        </div>
      )}

      {error && <p className="max-w-xs text-center text-[11px] text-amber-300">{error}</p>}

      {/* Transcription is falling behind the microphone. Say so, rather than
          quietly missing commands and leaving the presenter to guess. */}
      {dropped > 0 && !error && (
        <p className="max-w-xs text-center text-[11px] text-amber-300/80">
          Speech-to-text is running behind — {dropped} segment{dropped === 1 ? '' : 's'} skipped.
          A smaller Whisper model will keep up better.
        </p>
      )}

      {/* One switch for the whole talk. Not push-to-talk: the microphone stays
          open and the wake word decides what counts as a command. */}
      <button
        onClick={toggle}
        title={listening ? 'Stop listening' : `Listen continuously for "${WAKE_WORD} … ${TERMINATOR}"`}
        aria-pressed={listening}
        className={`relative flex h-12 w-12 items-center justify-center rounded-full transition-all ${
          capturing
            ? 'bg-brand-500 text-white shadow-lift'
            : listening
              ? 'bg-emerald-500/90 text-white shadow-lift'
              : 'border border-white/10 bg-ink-900/85 text-white/70 backdrop-blur hover:text-white'
        }`}
      >
        {busy ? (
          <Loader2 size={19} className="animate-spin" />
        ) : capturing ? (
          <Mic size={19} />
        ) : listening ? (
          <Ear size={19} />
        ) : (
          <MicOff size={19} />
        )}
        {listening && (
          <span
            className="pointer-events-none absolute inset-0 rounded-full border-2 border-white/50"
            style={{ transform: `scale(${1 + level * 0.5})`, opacity: 1 - level * 0.6 }}
          />
        )}
      </button>

      <p className="text-center text-[11px] text-white/35">
        {capturing
          ? `Command mode — end with "${TERMINATOR}"`
          : listening
            ? `Listening — say "${WAKE_WORD} … ${TERMINATOR}"`
            : 'Turn on voice control'}
      </p>
    </div>
  )
}
