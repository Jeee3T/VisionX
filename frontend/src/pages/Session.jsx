import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft,
  CameraOff,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Maximize2,
  Minimize2,
  MousePointer2,
  PenLine,
  Play,
  RefreshCw,
  ExternalLink,
} from 'lucide-react'
import StatusStrip from '../components/session/StatusStrip'
import CameraPreview from '../components/session/CameraPreview'
import SlideStage from '../components/session/SlideStage'
import VoicePanel from '../components/session/VoicePanel'
import { ErrorState, Loader } from '../components/Feedback'
import useEngineStream from '../hooks/useEngineStream'
import useIdle from '../hooks/useIdle'
import {
  annotationApi,
  engineApi,
  gestureApi,
  personalizationApi,
  presentationApi,
  sessionApi,
  voiceApi,
} from '../services/endpoints'
import { useToast } from '../context/ToastContext'
import { COMMANDS, COMMAND_ORDER } from '../utils/constants'
import { formatDuration, poseLabel } from '../utils/format'

const IDLE_HINT_AFTER = 8 // seconds without a hand before the gentle hint appears

export default function Session() {
  const [params] = useSearchParams()
  const presentationId = params.get('presentationId')
  const navigate = useNavigate()
  const toast = useToast()

  const [phase, setPhase] = useState('setup') // setup | starting | live | summary
  const [presentation, setPresentation] = useState(null)
  const [bindings, setBindings] = useState(null)
  const [cameras, setCameras] = useState([])
  const [cameraIndex, setCameraIndex] = useState(0)
  const [threshold, setThreshold] = useState(0.72)
  const [session, setSession] = useState(null)
  const [voice, setVoice] = useState(null)
  const [personalization, setPersonalization] = useState(null)
  const [startError, setStartError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)

  const [slide, setSlide] = useState(1)
  const [mode, setMode] = useState('IDLE')
  const [strokes, setStrokes] = useState([])
  const [elapsed, setElapsed] = useState(0)
  const [summary, setSummary] = useState(null)
  const [fullscreen, setFullscreen] = useState(false)
  const [ending, setEnding] = useState(false)

  const startedAt = useRef(null)
  // The presentation window this session opened. Held so the same window is
  // focused rather than a second one opened, and so it can be closed when the
  // session ends - a presentation window outliving its session would keep
  // showing a slide nothing is driving any more.
  const presentationWindow = useRef(null)
  const live = phase === 'live'
  const { telemetry, engineState, lastCommand, connected, streamError } = useEngineStream(live)
  const idle = useIdle(3000, telemetry?.executed ? lastCommand?.receivedAt : undefined)

  /**
   * Open (or re-focus) the dedicated presentation window.
   *
   * A real window rather than a fullscreen tab: the presenter needs the control
   * window on their laptop and the deck on the projector at the same time, which
   * a fullscreen tab in the same window cannot do. `noopener` is deliberately
   * NOT set - the handle is what lets the session close the window when it ends.
   */
  const openPresentationWindow = useCallback(() => {
    const existing = presentationWindow.current
    if (existing && !existing.closed) {
      existing.focus()
      return existing
    }
    const url = `/present?presentationId=${presentationId || ''}`
    const opened = window.open(
      url,
      'visionx-presentation',
      'popup=yes,width=1280,height=720,menubar=no,toolbar=no,location=no,status=no',
    )
    if (!opened) {
      toast.error(
        'Your browser blocked the presentation window. Allow pop-ups for VisionX, then click "Presentation window".',
      )
      return null
    }
    presentationWindow.current = opened
    return opened
  }, [presentationId, toast])

  // --- setup data ----------------------------------------------------------
  useEffect(() => {
    let cancelled = false
    const requests = [
      gestureApi.get(),
      engineApi.cameras().catch(() => ({ data: { cameras: [] } })),
      // Voice and personalization are additive - a failure here must not stop a session.
      voiceApi.status().catch(() => null),
      personalizationApi.get().catch(() => null),
    ]
    if (presentationId) requests.push(presentationApi.get(presentationId))

    Promise.all(requests)
      .then(([gestures, cameraList, voiceStatus, personalizationState, deck]) => {
        if (cancelled) return
        setBindings({ preferences: gestures.data.preferences, poses: gestures.data.poses })
        setCameras(cameraList.data.cameras || [])
        setCameraIndex((cameraList.data.cameras || [])[0] ?? 0)
        if (voiceStatus) setVoice(voiceStatus.data)
        if (personalizationState) setPersonalization(personalizationState.data)
        if (deck) setPresentation(deck.data.presentation)
      })
      .catch((err) => !cancelled && setLoadError(err.message))
      .finally(() => !cancelled && setLoading(false))

    return () => {
      cancelled = true
    }
  }, [presentationId])

  // --- live state from telemetry ------------------------------------------
  useEffect(() => {
    if (telemetry?.mode) setMode(telemetry.mode)
  }, [telemetry?.mode])

  useEffect(() => {
    if (lastCommand?.currentSlide) setSlide(lastCommand.currentSlide)
  }, [lastCommand?.currentSlide, lastCommand?.receivedAt])

  useEffect(() => {
    if (!live) return undefined
    const timer = setInterval(() => {
      setElapsed(startedAt.current ? Math.round((Date.now() - startedAt.current) / 1000) : 0)
    }, 1000)
    return () => clearInterval(timer)
  }, [live])

  // The slide on screen, readable from an async callback. Two ink fetches can be
  // in flight at once when the presenter moves quickly, and they can complete out
  // of order - without this guard slide 2's ink lands on slide 3.
  const showingSlide = useRef(slide)
  showingSlide.current = slide

  const loadStrokes = useCallback(() => {
    if (!presentationId) return
    const forSlide = slide
    annotationApi
      .forSlide(presentationId, forSlide)
      .then((response) => {
        if (showingSlide.current !== forSlide) return
        setStrokes(response.data.annotations)
      })
      .catch(() => {
        if (showingSlide.current !== forSlide) return
        setStrokes([])
      })
  }, [presentationId, slide])

  useEffect(() => {
    if (!live) return
    // Cleared first: otherwise the new slide is briefly shown carrying the
    // previous slide's annotations, for as long as the fetch takes.
    setStrokes([])
    loadStrokes()
  }, [live, loadStrokes])

  // Refresh persisted ink when the engine saves or clears it.
  useEffect(() => {
    if (live && lastCommand?.command === 'CLEAR_ANNOTATION') loadStrokes()
  }, [live, lastCommand?.receivedAt, lastCommand?.command, loadStrokes])

  // --- keyboard fallback (same dispatch path as gestures) ------------------
  useEffect(() => {
    if (!live) return undefined
    const onKey = (event) => {
      const map = {
        ArrowRight: 'NEXT_SLIDE',
        ArrowLeft: 'PREVIOUS_SLIDE',
        p: 'VIRTUAL_POINTER',
        a: 'ANNOTATION_MODE',
        e: 'CLEAR_ANNOTATION',
      }
      const command = map[event.key]
      if (!command) return
      event.preventDefault()
      engineApi.command(command).catch((err) => toast.error(err.message))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [live, toast])

  // --- start / end ---------------------------------------------------------
  const start = async () => {
    setPhase('starting')
    setStartError(null)
    try {
      const created = await sessionApi.create(presentationId || null)
      const newSession = created.data.session
      setSession(newSession)

      await engineApi.start(newSession.id, {
        cameraIndex,
        confidenceThreshold: Number(threshold),
      })

      startedAt.current = Date.now()
      setSlide(1)
      setElapsed(0)
      setPhase('live')

      // Only when there is something to present. A free session has no deck, so
      // the window would open on the projector showing "No presentation
      // selected" - worse than not opening it, and the presenter would have to
      // close it by hand.
      //
      // Opened from inside the click that started the session, so the browser
      // treats it as user-initiated and does not block it. Opening it later,
      // from an effect, is what pop-up blockers exist to stop.
      if (presentationId) openPresentationWindow()
    } catch (err) {
      setStartError(err)
      setPhase('setup')
    }
  }

  const end = async () => {
    if (!session) return
    setEnding(true)
    try {
      // Closed first: the summary screen must not leave a dead deck on the
      // projector while the presenter reads their stats.
      if (presentationWindow.current && !presentationWindow.current.closed) {
        presentationWindow.current.close()
      }
      presentationWindow.current = null
      const response = await sessionApi.complete(session.id, {
        slidesNavigated: Math.max(0, slide - 1),
      })
      setSummary(response.data.session)
      setPhase('summary')
    } catch (err) {
      toast.error(err.message)
    } finally {
      setEnding(false)
    }
  }

  // Never leave the camera running - or a presentation window open - if the
  // presenter navigates away.
  useEffect(() => {
    return () => {
      if (startedAt.current) engineApi.stop().catch(() => {})
      if (presentationWindow.current && !presentationWindow.current.closed) {
        presentationWindow.current.close()
      }
    }
  }, [])

  const toggleFullscreen = async () => {
    try {
      if (!document.fullscreenElement) {
        await document.documentElement.requestFullscreen()
        setFullscreen(true)
      } else {
        await document.exitFullscreen()
        setFullscreen(false)
      }
    } catch {
      toast.info('Your browser blocked fullscreen mode.')
    }
  }

  const runCommand = (command) => engineApi.command(command).catch((err) => toast.error(err.message))

  const saveMouseStroke = async (stroke) => {
    if (!presentationId) return
    try {
      await annotationApi.create({
        presentationId,
        slideNumber: slide,
        sessionId: session?.id,
        annotationData: stroke,
      })
      loadStrokes()
    } catch (err) {
      toast.error(err.message)
    }
  }

  const hint = useMemo(() => {
    if (!telemetry) return null
    if (telemetry.lowLight) return { type: 'light', message: 'The room looks dark — more light improves detection.' }
    if (!telemetry.handDetected && (telemetry.idleSeconds ?? 0) > IDLE_HINT_AFTER) {
      return { type: 'hand', message: 'Show your hand to the camera to take control.' }
    }
    return null
  }, [telemetry])

  if (loading) return <Loader label="Preparing your session…" />
  if (loadError) return <ErrorState message={loadError} onRetry={() => window.location.reload()} />

  // ---------------------------------------------------------------- SETUP --
  if (phase === 'setup' || phase === 'starting') {
    const starting = phase === 'starting'
    return (
      <div className="mx-auto max-w-3xl animate-fade-in">
        <Link to="/presentations" className="mb-4 inline-flex items-center gap-1.5 text-sm text-ink-500 hover:text-ink-800">
          <ArrowLeft size={15} /> Back to library
        </Link>

        <div className="card overflow-hidden">
          <div className="bg-brand-gradient px-6 py-7 text-white">
            <p className="text-xs uppercase tracking-[0.18em] text-white/60">Presentation session</p>
            <h1 className="mt-1.5 text-2xl font-semibold">{presentation?.title || 'Free session'}</h1>
            <p className="mt-1 text-sm text-white/75">
              {presentation
                ? `${presentation.totalSlides || 0} slides · VisionX presents them in their own window`
                : 'No deck selected — start a session to control a presentation you open later.'}
            </p>
          </div>

          <div className="p-6">
            {startError && (
              <div className="mb-5 flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4">
                <span className="rounded-lg bg-amber-100 p-2 text-amber-700">
                  <CameraOff size={17} />
                </span>
                <div className="flex-1">
                  <p className="text-sm font-medium text-amber-900">
                    {startError.code === 'CAMERA_UNAVAILABLE' ? 'No camera available' : 'Could not start the session'}
                  </p>
                  <p className="mt-1 text-sm text-amber-800">{startError.message}</p>
                  <button onClick={start} className="btn-secondary mt-3 py-2 text-xs">
                    <RefreshCw size={14} /> Try again
                  </button>
                </div>
              </div>
            )}

            <div className="grid gap-5 sm:grid-cols-2">
              <div>
                <label className="label" htmlFor="camera">Camera</label>
                <select
                  id="camera"
                  className="input"
                  value={cameraIndex}
                  onChange={(e) => setCameraIndex(Number(e.target.value))}
                >
                  {(cameras.length ? cameras : [0]).map((index) => (
                    <option key={index} value={index}>
                      Camera {index}
                      {index === 0 ? ' (default)' : ''}
                    </option>
                  ))}
                </select>
                <p className="mt-1.5 text-xs text-ink-400">
                  {cameras.length ? `${cameras.length} camera(s) detected.` : 'No camera detected yet — plug one in and reload.'}
                </p>
              </div>

              <div>
                <label className="label" htmlFor="threshold">
                  Confidence gate <span className="font-normal text-ink-400">({Math.round(threshold * 100)}%)</span>
                </label>
                <input
                  id="threshold"
                  type="range"
                  min="0.5"
                  max="0.95"
                  step="0.01"
                  value={threshold}
                  onChange={(e) => setThreshold(Number(e.target.value))}
                  className="mt-3 w-full accent-brand-600"
                />
                <p className="mt-1.5 text-xs text-ink-400">
                  Higher is stricter: fewer accidental commands, more deliberate gestures.
                </p>
              </div>
            </div>

            <div className="mt-6">
              <p className="mb-2.5 text-sm font-semibold text-ink-800">Your gesture bindings</p>
              <div className="grid gap-2 sm:grid-cols-2">
                {COMMAND_ORDER.map((command) => {
                  const meta = COMMANDS[command]
                  const pose = bindings?.preferences?.[meta.field]
                  return (
                    <div key={command} className="flex items-center gap-3 rounded-xl border border-ink-200 px-3 py-2.5">
                      <meta.icon size={16} className={meta.colour} />
                      <span className="flex-1 text-sm text-ink-700">{meta.label}</span>
                      <span className="chip bg-ink-100 text-ink-600">{poseLabel(bindings?.poses || [], pose)}</span>
                    </div>
                  )
                })}
              </div>
              <Link to="/gestures" className="mt-2.5 inline-block text-xs font-medium text-brand-600 hover:text-brand-700">
                Change bindings
              </Link>
            </div>

            <div className="mt-6 grid gap-2 sm:grid-cols-2">
              <div className="rounded-xl border border-ink-200 px-3 py-2.5">
                <p className="text-[11px] uppercase tracking-wide text-ink-400">Recognition</p>
                <p className="mt-0.5 text-sm font-medium text-ink-800">
                  {personalization?.settings?.gesturePersonalizationEnabled &&
                  personalization?.gesture?.model?.available
                    ? 'Your personalized model'
                    : 'Built-in geometric recognizer'}
                </p>
                <Link to="/gestures" className="mt-0.5 inline-block text-xs text-brand-600 hover:text-brand-700">
                  {personalization?.gesture?.model?.available ? 'Change' : 'Train your own'}
                </Link>
              </div>
              <div className="rounded-xl border border-ink-200 px-3 py-2.5">
                <p className="text-[11px] uppercase tracking-wide text-ink-400">Voice control</p>
                <p className="mt-0.5 text-sm font-medium text-ink-800">
                  {voice?.ready ? 'Push-to-talk armed' : 'Off'}
                </p>
                <Link to="/voice" className="mt-0.5 inline-block text-xs text-brand-600 hover:text-brand-700">
                  {voice?.ready ? 'Voice settings' : 'Set up voice'}
                </Link>
              </div>
            </div>

            <div className="mt-4 rounded-xl bg-ink-50 p-4 text-xs leading-relaxed text-ink-500">
              VisionX opens a separate presentation window and shows your slides there — drag it to your
              projector or second screen. Nothing is typed into another application, so PowerPoint does not
              need to be open and no other window can steal your commands. Keep this window on your laptop:
              it is your camera preview and controls. Allow pop-ups for VisionX so the presentation window
              can open.
            </div>

            <button onClick={start} disabled={starting} className="btn-primary mt-6 w-full py-3.5">
              {starting ? <Loader2 size={18} className="animate-spin" /> : <Play size={18} />}
              {starting ? 'Starting camera…' : 'Start presentation'}
            </button>
          </div>
        </div>
      </div>
    )
  }

  // -------------------------------------------------------------- SUMMARY --
  if (phase === 'summary') {
    return (
      <div className="mx-auto max-w-lg animate-fade-in">
        <div className="card p-8 text-center">
          <h1 className="text-xl font-semibold text-ink-900">Session complete</h1>
          <p className="mt-1.5 text-sm text-ink-500">{presentation?.title || 'Free session'}</p>

          <div className="mt-7 grid grid-cols-3 gap-3">
            {[
              { label: 'Duration', value: formatDuration(summary?.duration || elapsed) },
              { label: 'Slides', value: summary?.slidesNavigated ?? 0 },
              { label: 'Gestures', value: summary?.commandsFired ?? 0 },
            ].map((item) => (
              <div key={item.label} className="rounded-xl bg-ink-50 px-3 py-4">
                <p className="text-lg font-semibold text-ink-900">{item.value}</p>
                <p className="mt-0.5 text-xs text-ink-500">{item.label}</p>
              </div>
            ))}
          </div>

          <div className="mt-7 flex justify-center gap-2">
            <button onClick={() => navigate('/history')} className="btn-secondary">
              View history
            </button>
            <button onClick={() => navigate('/dashboard')} className="btn-primary">
              Back to dashboard
            </button>
          </div>
        </div>
      </div>
    )
  }

  // ----------------------------------------------------------------- LIVE --
  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-ink-900 p-3 sm:p-4">
      <div className="relative min-h-0 flex-1">
        <SlideStage
          presentation={presentation}
          slide={slide}
          mode={mode}
          pointer={telemetry?.pointer || null}
          savedStrokes={strokes}
          onStrokeComplete={saveMouseStroke}
          hint={hint}
        />

        {/* Camera thumbnail - corner, small, collapsible */}
        <div className="absolute right-3 top-3 z-20">
          <CameraPreview running={engineState?.state !== 'STOPPED'} hidden={idle} />
        </div>

        {/* Mode badge - only when a mode is actually engaged */}
        {mode !== 'IDLE' && (
          <div className="absolute left-3 top-3 z-20 animate-fade-in">
            <span className="chip bg-black/60 text-white backdrop-blur">
              {mode === 'POINTER' ? <MousePointer2 size={13} /> : <PenLine size={13} />}
              {mode === 'POINTER' ? 'Pointer' : 'Annotating'}
            </span>
          </div>
        )}

        {(streamError || engineState?.state === 'ERROR') && (
          <div className="absolute inset-x-0 top-3 z-20 mx-auto w-fit">
            <span className="chip bg-amber-500/90 text-white">
              <CameraOff size={13} /> {streamError || engineState?.error || 'Camera problem'}
            </span>
          </div>
        )}
      </div>

      {/* Peripheral controls */}
      <div className="mt-3 flex flex-col items-center gap-2.5">
        <div
          className={`flex items-center gap-1.5 rounded-2xl border border-white/10 bg-ink-900/85 p-1.5 backdrop-blur transition-opacity duration-500 ${
            idle ? 'opacity-0' : 'opacity-100'
          }`}
        >
          {COMMAND_ORDER.map((command) => {
            const meta = COMMANDS[command]
            return (
              <button
                key={command}
                onClick={() => runCommand(command)}
                title={meta.label}
                className="rounded-xl px-3 py-2 text-white/70 transition-colors hover:bg-white/10 hover:text-white"
              >
                <meta.icon size={17} />
              </button>
            )
          })}
          <span className="mx-1 h-5 w-px bg-white/15" />
          {/* Re-opens the presentation window if the presenter closed it, or if
              the browser blocked the pop-up when the session started. Disabled
              for a free session, which has no deck to show. */}
          <button
            onClick={openPresentationWindow}
            disabled={!presentationId}
            title={
              presentationId
                ? 'Presentation window'
                : 'This session has no presentation to show'
            }
            className="rounded-xl px-3 py-2 text-white/70 transition-colors hover:bg-white/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent"
          >
            <ExternalLink size={17} />
          </button>
          <button
            onClick={toggleFullscreen}
            title="Toggle fullscreen"
            className="rounded-xl px-3 py-2 text-white/70 transition-colors hover:bg-white/10 hover:text-white"
          >
            {fullscreen ? <Minimize2 size={17} /> : <Maximize2 size={17} />}
          </button>
        </div>

        {voice?.ready && (
          <VoicePanel
            sessionId={session?.id}
            hidden={idle}
            onCommand={(result) => {
              // A voice command lands in the same dispatcher, so the slide it
              // reports is authoritative for the stage.
              if (result?.result?.currentSlide) setSlide(result.result.currentSlide)
            }}
          />
        )}

        <StatusStrip
          telemetry={telemetry}
          lastCommand={lastCommand}
          connected={connected}
          elapsed={elapsed}
          hidden={idle}
          onEnd={end}
        />

        <p className={`text-[11px] text-white/35 transition-opacity duration-500 ${idle ? 'opacity-0' : 'opacity-100'}`}>
          Slide {slide}
          {presentation?.totalSlides ? ` of ${presentation.totalSlides}` : ''}
          {engineState?.recognizer?.personalized ? ' · personalized model' : ''}
          {voice?.ready ? ' · voice on' : ''} · keyboard fallback:{' '}
          <ChevronLeft size={11} className="inline" /> <ChevronRight size={11} className="inline" /> P A E
          {ending ? ' · ending…' : ''}
        </p>
      </div>
    </div>
  )
}
