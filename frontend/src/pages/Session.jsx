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
} from 'lucide-react'
import StatusStrip from '../components/session/StatusStrip'
import CameraPreview from '../components/session/CameraPreview'
import SlideStage from '../components/session/SlideStage'
import { ErrorState, Loader } from '../components/Feedback'
import useEngineStream from '../hooks/useEngineStream'
import useIdle from '../hooks/useIdle'
import {
  annotationApi,
  engineApi,
  gestureApi,
  presentationApi,
  sessionApi,
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
  const live = phase === 'live'
  const { telemetry, engineState, lastCommand, connected, streamError } = useEngineStream(live)
  const idle = useIdle(3000, telemetry?.executed ? lastCommand?.receivedAt : undefined)

  // --- setup data ----------------------------------------------------------
  useEffect(() => {
    let cancelled = false
    const requests = [gestureApi.get(), engineApi.cameras().catch(() => ({ data: { cameras: [] } }))]
    if (presentationId) requests.push(presentationApi.get(presentationId))

    Promise.all(requests)
      .then(([gestures, cameraList, deck]) => {
        if (cancelled) return
        setBindings({ preferences: gestures.data.preferences, poses: gestures.data.poses })
        setCameras(cameraList.data.cameras || [])
        setCameraIndex((cameraList.data.cameras || [])[0] ?? 0)
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

  const loadStrokes = useCallback(() => {
    if (!presentationId) return
    annotationApi
      .forSlide(presentationId, slide)
      .then((response) => setStrokes(response.data.annotations))
      .catch(() => setStrokes([]))
  }, [presentationId, slide])

  useEffect(() => {
    if (live) loadStrokes()
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
    } catch (err) {
      setStartError(err)
      setPhase('setup')
    }
  }

  const end = async () => {
    if (!session) return
    setEnding(true)
    try {
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

  // Never leave the camera running if the presenter navigates away.
  useEffect(() => {
    return () => {
      if (startedAt.current) engineApi.stop().catch(() => {})
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
                ? `${presentation.totalSlides || 0} slides · gestures drive PowerPoint on this machine`
                : 'No deck selected — gestures will control whatever is on screen.'}
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

            <div className="mt-6 rounded-xl bg-ink-50 p-4 text-xs leading-relaxed text-ink-500">
              Open your slideshow in PowerPoint (F5) on this machine before starting. VisionX sends real key
              presses, so whichever window has focus receives the commands.
            </div>

            <button onClick={start} disabled={starting} className="btn-primary mt-6 w-full py-3.5">
              {starting ? <Loader2 size={18} className="animate-spin" /> : <Play size={18} />}
              {starting ? 'Starting camera…' : 'Start session'}
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
          <button
            onClick={toggleFullscreen}
            title="Toggle fullscreen"
            className="rounded-xl px-3 py-2 text-white/70 transition-colors hover:bg-white/10 hover:text-white"
          >
            {fullscreen ? <Minimize2 size={17} /> : <Maximize2 size={17} />}
          </button>
        </div>

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
          {presentation?.totalSlides ? ` of ${presentation.totalSlides}` : ''} · keyboard fallback:{' '}
          <ChevronLeft size={11} className="inline" /> <ChevronRight size={11} className="inline" /> P A E
          {ending ? ' · ending…' : ''}
        </p>
      </div>
    </div>
  )
}
