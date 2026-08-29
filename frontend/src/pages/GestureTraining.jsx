import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  ArrowLeft,
  Brain,
  Camera,
  CameraOff,
  Check,
  CircleDot,
  Loader2,
  Play,
  RotateCcw,
  Square,
  TriangleAlert,
} from 'lucide-react'
import PageHeader from '../components/PageHeader'
import { ErrorState, Loader } from '../components/Feedback'
import CameraPreview from '../components/session/CameraPreview'
import useEngineStream from '../hooks/useEngineStream'
import { personalizationApi } from '../services/endpoints'
import { useToast } from '../context/ToastContext'
import { NULL_GESTURE_CLASS } from '../utils/constants'

/** Small finger diagram, matching the one on the gesture settings screen. */
function PoseGlyph({ fingers }) {
  if (!fingers) return <span className="text-[11px] font-medium text-ink-400">any</span>
  return (
    <span className="flex items-end gap-[3px]">
      {['T', 'I', 'M', 'R', 'P'].map((name, index) => (
        <span
          key={name}
          title={name}
          className={`w-[7px] rounded-sm ${fingers[index] ? 'h-4 bg-brand-500' : 'h-2 bg-ink-300'}`}
        />
      ))}
    </span>
  )
}

/**
 * Guided enrolment.
 *
 * The camera runs in ENROLLMENT mode: the engine tracks hands and collects
 * labelled frames but dispatches nothing, so nothing can reach PowerPoint while
 * the presenter is training. Each recording is a short continuous capture, and
 * several recordings per gesture matter more than one long one — recordings are
 * the unit the training split uses, so more of them means a more honest score.
 */
export default function GestureTraining() {
  const toast = useToast()
  const navigate = useNavigate()

  const [plan, setPlan] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [cameraOn, setCameraOn] = useState(false)
  const [starting, setStarting] = useState(false)
  const [active, setActive] = useState(null) // label currently being recorded
  const [capture, setCapture] = useState(null)
  const [training, setTraining] = useState(null)
  const [busy, setBusy] = useState(false)

  const cameraOnRef = useRef(false)
  const { engineState } = useEngineStream(cameraOn)

  const load = useCallback(
    () =>
      personalizationApi
        .plan()
        .then((response) => setPlan(response.data))
        .catch((err) => setError(err.message))
        .finally(() => setLoading(false)),
    [],
  )

  useEffect(() => {
    load()
    personalizationApi.trainStatus().then((r) => setTraining(r.data)).catch(() => {})
  }, [load])

  // The camera must never be left running when the presenter navigates away.
  useEffect(() => {
    cameraOnRef.current = cameraOn
  }, [cameraOn])
  useEffect(
    () => () => {
      if (cameraOnRef.current) personalizationApi.stopCamera().catch(() => {})
    },
    [],
  )

  // Live capture progress arrives on the same SSE channel a session uses.
  useEffect(() => {
    if (!engineState) return
    if (engineState.enrollment) setCapture(engineState.enrollment)
  }, [engineState])

  // While a recording is running, poll for progress (SSE covers it, this is the floor).
  useEffect(() => {
    if (!active) return undefined
    const timer = setInterval(() => {
      personalizationApi
        .recordingStatus()
        .then((r) => setCapture(r.data.capture))
        .catch(() => {})
    }, 400)
    return () => clearInterval(timer)
  }, [active])

  // Poll training status while a background training run is in flight.
  useEffect(() => {
    if (training?.status !== 'RUNNING') return undefined
    const timer = setInterval(() => {
      personalizationApi
        .trainStatus()
        .then((r) => {
          setTraining(r.data)
          if (r.data.status === 'DONE') toast.success(r.data.message)
          if (r.data.status === 'FAILED') toast.error(r.data.message)
        })
        .catch(() => {})
    }, 1500)
    return () => clearInterval(timer)
  }, [training?.status, toast])

  const startCamera = async () => {
    setStarting(true)
    try {
      const response = await personalizationApi.startCamera()
      setPlan(response.data.plan)
      setCameraOn(true)
    } catch (err) {
      toast.error(err.message)
    } finally {
      setStarting(false)
    }
  }

  const stopCamera = async () => {
    try {
      await personalizationApi.stopCamera()
    } catch {
      /* already stopped */
    }
    setCameraOn(false)
    setActive(null)
    setCapture(null)
  }

  const record = async (label) => {
    setBusy(true)
    try {
      const response = await personalizationApi.startRecording(label)
      setActive(label)
      setCapture(response.data)
    } catch (err) {
      toast.error(err.message)
    } finally {
      setBusy(false)
    }
  }

  const finish = async () => {
    setBusy(true)
    try {
      const response = await personalizationApi.finishRecording()
      setPlan(response.data.plan)
      toast.success(`Saved ${response.data.frames} frames.`)
    } catch (err) {
      toast.error(err.message)
    } finally {
      setActive(null)
      setCapture(null)
      setBusy(false)
    }
  }

  const cancel = async () => {
    await personalizationApi.cancelRecording().catch(() => {})
    setActive(null)
    setCapture(null)
  }

  const train = async () => {
    setBusy(true)
    try {
      const response = await personalizationApi.train()
      setTraining(response.data)
      toast.info('Training started — this runs in the background.')
    } catch (err) {
      toast.error(err.message)
    } finally {
      setBusy(false)
    }
  }

  const reset = async () => {
    if (!window.confirm('Delete every recording you have made? Your model is kept.')) return
    setBusy(true)
    try {
      const response = await personalizationApi.deleteRecordings()
      setPlan(response.data.plan)
      toast.success('Recordings deleted.')
    } catch (err) {
      toast.error(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <Loader label="Loading your training plan…" />
  if (error) return <ErrorState message={error} onRetry={load} />

  const complete = capture?.complete
  const running = training?.status === 'RUNNING'

  return (
    <div className="animate-fade-in">
      <Link
        to="/gestures"
        className="mb-4 inline-flex items-center gap-1.5 text-sm text-ink-500 hover:text-ink-800"
      >
        <ArrowLeft size={15} /> Back to gesture settings
      </Link>

      <PageHeader
        title="Train my gestures"
        subtitle="Record each pose a few times so VisionX learns your hands, not an average hand."
        actions={
          cameraOn ? (
            <button onClick={stopCamera} className="btn-secondary">
              <CameraOff size={15} /> Stop camera
            </button>
          ) : (
            <button onClick={startCamera} disabled={starting} className="btn-primary">
              {starting ? <Loader2 size={16} className="animate-spin" /> : <Camera size={16} />}
              {starting ? 'Opening camera…' : 'Start camera'}
            </button>
          )
        }
      />

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          {!cameraOn && (
            <div className="card flex items-start gap-3 p-5 text-sm text-ink-600">
              <Camera size={18} className="mt-0.5 shrink-0 text-ink-400" />
              <p>
                Start the camera to begin. Nothing is dispatched to PowerPoint while you train —
                the engine runs in enrolment mode and only collects labelled frames.
              </p>
            </div>
          )}

          <div className="card divide-y divide-ink-100">
            {plan.steps.map((step) => {
              const isActive = active === step.label
              const isNull = step.label === NULL_GESTURE_CLASS
              return (
                <div key={step.label} className="p-5">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
                    <span
                      className={`rounded-xl p-2.5 ${
                        step.complete ? 'bg-emerald-50 text-emerald-600' : 'bg-ink-50 text-ink-500'
                      }`}
                    >
                      {step.complete ? <Check size={18} /> : <CircleDot size={18} />}
                    </span>

                    <div className="min-w-0 flex-1">
                      <p className="flex items-center gap-2 text-sm font-semibold text-ink-900">
                        {step.title}
                        {isNull && (
                          <span className="chip bg-amber-50 text-amber-700">Null class</span>
                        )}
                      </p>
                      <p className="mt-0.5 text-xs leading-relaxed text-ink-500">{step.prompt}</p>
                    </div>

                    <div className="flex items-center gap-3">
                      <PoseGlyph fingers={step.fingers} />
                      <span className="chip bg-ink-100 text-ink-600">
                        {step.recordingsCollected}/{step.recordingsNeeded}
                      </span>
                      {isActive ? (
                        <button
                          onClick={complete ? finish : cancel}
                          disabled={busy}
                          className={complete ? 'btn-primary py-2 text-xs' : 'btn-secondary py-2 text-xs'}
                        >
                          {complete ? <Check size={14} /> : <Square size={14} />}
                          {complete ? 'Save' : 'Cancel'}
                        </button>
                      ) : (
                        <button
                          onClick={() => record(step.label)}
                          disabled={!cameraOn || busy || !!active}
                          className="btn-secondary py-2 text-xs"
                        >
                          <Play size={14} /> Record
                        </button>
                      )}
                    </div>
                  </div>

                  {isActive && capture && (
                    <div className="mt-4 rounded-xl bg-ink-50 p-3.5">
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-medium text-ink-700">
                          {capture.accepted} / {capture.targetFrames} frames
                        </span>
                        <span className="text-ink-500">
                          {capture.rejected > 0 && `${capture.rejected} rejected`}
                        </span>
                      </div>
                      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-200">
                        <div
                          className="h-full rounded-full bg-brand-gradient transition-all duration-200"
                          style={{ width: `${Math.round((capture.progress || 0) * 100)}%` }}
                        />
                      </div>
                      {capture.lastRejection && (
                        <p className="mt-2 text-[11px] text-amber-700">{capture.lastRejection}</p>
                      )}
                      {complete && (
                        <p className="mt-2 text-[11px] font-medium text-emerald-700">
                          Enough frames — save this recording, then record it again from a
                          slightly different distance or angle.
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        {/* Right rail: preview, progress and the train button. */}
        <div className="space-y-4">
          {cameraOn && (
            <div className="card overflow-hidden p-3">
              <CameraPreview running={cameraOn} />
              <p className="mt-2 px-1 text-[11px] text-ink-400">
                Keep your whole hand in frame and well lit. Frames that are too dark, too far
                away, or identical to the previous one are rejected automatically.
              </p>
            </div>
          )}

          <div className="card p-5">
            <h2 className="text-sm font-semibold text-ink-800">Progress</h2>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-ink-100">
              <div
                className="h-full rounded-full bg-brand-gradient transition-all"
                style={{ width: `${Math.round(plan.progress * 100)}%` }}
              />
            </div>
            <p className="mt-2 text-xs text-ink-500">
              {plan.totalRecordingsCollected} of {plan.totalRecordingsNeeded} recordings ·{' '}
              {plan.framesPerRecording} frames each
            </p>

            {!plan.readyToTrain && (
              <div className="mt-4 flex items-start gap-2 rounded-xl bg-ink-50 p-3 text-[11px] text-ink-500">
                <TriangleAlert size={14} className="mt-0.5 shrink-0 text-amber-500" />
                <span>
                  Training needs at least two recordings for at least three gestures, including
                  the null class — without it the model has no way to stay quiet while you
                  gesture naturally.
                </span>
              </div>
            )}

            {training?.status === 'DONE' && (
              <p className="mt-4 rounded-xl bg-emerald-50 p-3 text-[11px] text-emerald-800">
                {training.message}
              </p>
            )}
            {training?.status === 'FAILED' && (
              <p className="mt-4 rounded-xl bg-amber-50 p-3 text-[11px] text-amber-800">
                {training.message}
              </p>
            )}

            <button
              onClick={train}
              disabled={busy || running || !plan.readyToTrain}
              className="btn-primary mt-4 w-full"
            >
              {running ? <Loader2 size={16} className="animate-spin" /> : <Brain size={16} />}
              {running ? 'Training…' : 'Train my model'}
            </button>

            <div className="mt-2 flex gap-2">
              <button onClick={reset} disabled={busy} className="btn-ghost flex-1 py-2 text-xs">
                <RotateCcw size={14} /> Clear recordings
              </button>
              <button
                onClick={() => navigate('/gestures')}
                className="btn-ghost flex-1 py-2 text-xs"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
