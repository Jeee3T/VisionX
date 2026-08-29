import { useCallback, useEffect, useState } from 'react'
import {
  AudioLines,
  Check,
  Info,
  Mic,
  MicOff,
  Send,
  Trash2,
  TriangleAlert,
} from 'lucide-react'
import PageHeader from '../components/PageHeader'
import { ErrorState, Loader } from '../components/Feedback'
import { personalizationApi, voiceApi } from '../services/endpoints'
import { useToast } from '../context/ToastContext'
import { VOICE_BANDS } from '../utils/constants'

function BandChip({ band }) {
  const meta = VOICE_BANDS[band] || VOICE_BANDS.REJECT
  const tone =
    meta.tone === 'success'
      ? 'bg-emerald-50 text-emerald-700'
      : meta.tone === 'warning'
        ? 'bg-amber-50 text-amber-700'
        : 'bg-ink-100 text-ink-600'
  return <span className={`chip ${tone}`}>{meta.label}</span>
}

/**
 * Voice settings, the command reference, and a text console for trying the
 * intent model without speaking. The console posts with execute:false, so it
 * classifies without touching the slideshow.
 */
export default function VoiceAssistant() {
  const toast = useToast()
  const [status, setStatus] = useState(null)
  const [settings, setSettings] = useState(null)
  const [catalogue, setCatalogue] = useState([])
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [text, setText] = useState('')
  const [result, setResult] = useState(null)
  const [testing, setTesting] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([
      voiceApi.status(),
      personalizationApi.get(),
      voiceApi.commands(),
      voiceApi.history(25).catch(() => ({ data: { commands: [] } })),
    ])
      .then(([statusResponse, settingsResponse, catalogueResponse, historyResponse]) => {
        setStatus(statusResponse.data)
        setSettings(settingsResponse.data.settings)
        setCatalogue(catalogueResponse.data.intents)
        setHistory(historyResponse.data.commands)
        setError(null)
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(load, [load])

  const update = async (patch) => {
    setSaving(true)
    try {
      const response = await personalizationApi.update(patch)
      setSettings(response.data.settings)
      setStatus((await voiceApi.status()).data)
    } catch (err) {
      toast.error(err.message)
    } finally {
      setSaving(false)
    }
  }

  const test = async (event) => {
    event.preventDefault()
    if (!text.trim()) return
    setTesting(true)
    try {
      // execute:false — this classifies the sentence, it never runs the command.
      const response = await voiceApi.interpret(text, { execute: false })
      setResult(response.data)
    } catch (err) {
      toast.error(err.message)
    } finally {
      setTesting(false)
    }
  }

  const clearHistory = async () => {
    if (!window.confirm('Delete your stored voice command history?')) return
    try {
      const response = await voiceApi.clearHistory()
      setHistory([])
      toast.success(`Deleted ${response.data.deleted} entries.`)
    } catch (err) {
      toast.error(err.message)
    }
  }

  if (loading) return <Loader label="Loading the voice assistant…" />
  if (error) return <ErrorState message={error} onRetry={load} />

  const ready = status.ready
  const model = status.intentModel || {}
  const metrics = model.metrics?.test

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Voice assistant"
        subtitle="Speak a command and VisionX runs it through the same dispatcher your gestures use."
      />

      {status.blockers?.length > 0 && (
        <div className="mb-5 rounded-xl border border-amber-200 bg-amber-50 p-4">
          <p className="flex items-center gap-2 text-sm font-medium text-amber-900">
            <TriangleAlert size={16} /> Voice control is not ready yet
          </p>
          <ul className="mt-2 list-inside list-disc space-y-1 text-xs text-amber-800">
            {status.blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
          {status.canInterpretText && (
            <p className="mt-2 text-xs text-amber-800">
              The intent model still works on typed text — try it in the console below.
            </p>
          )}
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-5 lg:col-span-2">
          {/* --- settings --- */}
          <div className="card p-5">
            <div className="flex items-start gap-3">
              <span className={`rounded-xl p-2.5 ${ready ? 'bg-brand-50 text-brand-600' : 'bg-ink-100 text-ink-500'}`}>
                {ready ? <Mic size={19} /> : <MicOff size={19} />}
              </span>
              <div className="min-w-0 flex-1">
                <h2 className="text-sm font-semibold text-ink-900">Voice control</h2>
                <p className="mt-1 text-xs leading-relaxed text-ink-500">
                  Speech is transcribed locally by Whisper on this machine, classified by a
                  VisionX-trained intent model, and dispatched as an ordinary VisionX command.
                  Audio is never stored or sent anywhere.
                </p>
              </div>
            </div>

            <div className="mt-5 space-y-3">
              <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-ink-200 p-3.5">
                <input
                  type="checkbox"
                  checked={!!settings.voiceEnabled}
                  disabled={saving}
                  onChange={(e) => update({ voiceEnabled: e.target.checked })}
                  className="mt-0.5 h-4 w-4 shrink-0 accent-brand-600"
                />
                <span>
                  <span className="block text-sm font-medium text-ink-800">
                    Enable the voice assistant
                  </span>
                  <span className="mt-0.5 block text-xs text-ink-500">
                    Adds push-to-talk to the session screen. Gesture control is unaffected either way.
                  </span>
                </span>
              </label>

              <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-ink-200 p-3.5">
                <input
                  type="checkbox"
                  checked={!!settings.voiceTranscriptRetention}
                  disabled={saving}
                  onChange={(e) => update({ voiceTranscriptRetention: e.target.checked })}
                  className="mt-0.5 h-4 w-4 shrink-0 accent-brand-600"
                />
                <span>
                  <span className="block text-sm font-medium text-ink-800">Keep transcripts</span>
                  <span className="mt-0.5 block text-xs text-ink-500">
                    Stores the recognised text alongside each command so you can see what VisionX
                    heard. Turn it off and only the intent, confidence and outcome are recorded.
                    Raw audio is never stored either way.
                  </span>
                </span>
              </label>
            </div>

            {/* Confidence bands: the safety gate from FEATURE B8. */}
            <div className="mt-5 grid gap-5 sm:grid-cols-2">
              {[
                {
                  key: 'voiceExecuteThreshold',
                  label: 'Run automatically above',
                  hint: 'Commands at or above this confidence run without asking.',
                  min: 0.4,
                  max: 0.99,
                },
                {
                  key: 'voiceConfirmThreshold',
                  label: 'Ask for confirmation above',
                  hint: 'Below this, VisionX stays silent and does nothing at all.',
                  min: 0.1,
                  max: 0.95,
                },
              ].map((slider) => (
                <div key={slider.key}>
                  <label className="label" htmlFor={slider.key}>
                    {slider.label}{' '}
                    <span className="font-normal text-ink-400">
                      ({Math.round((settings[slider.key] ?? 0) * 100)}%)
                    </span>
                  </label>
                  <input
                    id={slider.key}
                    type="range"
                    min={slider.min}
                    max={slider.max}
                    step="0.01"
                    value={settings[slider.key] ?? slider.min}
                    disabled={saving}
                    onChange={(e) => setSettings({ ...settings, [slider.key]: Number(e.target.value) })}
                    onMouseUp={(e) => update({ [slider.key]: Number(e.target.value) })}
                    onTouchEnd={(e) => update({ [slider.key]: Number(e.target.value) })}
                    className="mt-2 w-full accent-brand-600"
                  />
                  <p className="mt-1.5 text-xs text-ink-400">{slider.hint}</p>
                </div>
              ))}
            </div>
          </div>

          {/* --- text console --- */}
          <div className="card p-5">
            <h2 className="text-sm font-semibold text-ink-900">Try a command</h2>
            <p className="mt-1 text-xs text-ink-500">
              Type what you would say. This classifies the sentence without running anything, so
              it is safe to experiment mid-presentation.
            </p>

            <form onSubmit={test} className="mt-4 flex gap-2">
              <input
                className="input flex-1"
                placeholder="go to slide seven"
                value={text}
                onChange={(e) => setText(e.target.value)}
                disabled={!status.canInterpretText}
              />
              <button
                type="submit"
                disabled={testing || !text.trim() || !status.canInterpretText}
                className="btn-primary"
              >
                <Send size={15} /> Interpret
              </button>
            </form>

            {result && (
              <div className="mt-4 rounded-xl border border-ink-200 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <BandChip band={result.band} />
                  <span className="text-sm font-semibold text-ink-900">
                    {result.commandLabel || result.intentLabel}
                  </span>
                  {Object.entries(result.parameters || {}).map(([key, value]) => (
                    <span key={key} className="chip bg-brand-50 text-brand-700">
                      {key}: {String(value)}
                    </span>
                  ))}
                  <span className="ml-auto text-xs text-ink-500">
                    {Math.round(result.probability * 100)}% confident
                  </span>
                </div>
                <p className="mt-2 text-xs text-ink-500">{result.message}</p>
                {result.distribution && Object.keys(result.distribution).length > 1 && (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {Object.entries(result.distribution)
                      .sort((a, b) => b[1] - a[1])
                      .slice(0, 4)
                      .map(([intent, probability]) => (
                        <span key={intent} className="chip bg-ink-100 text-[11px] text-ink-600">
                          {intent} {Math.round(probability * 100)}%
                        </span>
                      ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* --- history --- */}
          <div className="card p-5">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-ink-900">Recent voice commands</h2>
              {history.length > 0 && (
                <button onClick={clearHistory} className="btn-ghost py-1.5 text-xs">
                  <Trash2 size={14} /> Clear
                </button>
              )}
            </div>

            {history.length === 0 ? (
              <p className="mt-3 text-xs text-ink-400">No voice commands recorded yet.</p>
            ) : (
              <ul className="mt-3 divide-y divide-ink-100">
                {history.map((entry) => (
                  <li key={entry.id} className="flex items-center gap-3 py-2.5">
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm text-ink-800">
                        {entry.transcript || <em className="text-ink-400">transcript not kept</em>}
                      </span>
                      <span className="text-[11px] text-ink-400">
                        {entry.command || entry.intent} ·{' '}
                        {Math.round((entry.confidence || 0) * 100)}%
                        {entry.error ? ` · ${entry.error}` : ''}
                      </span>
                    </span>
                    {entry.executed ? (
                      <span className="chip bg-emerald-50 text-emerald-700">
                        <Check size={12} /> Ran
                      </span>
                    ) : (
                      <BandChip band={entry.band} />
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {/* --- right rail: model card + command reference --- */}
        <div className="space-y-4">
          <div className="card p-5">
            <span className="inline-flex rounded-xl bg-brand-50 p-2.5 text-brand-600">
              <AudioLines size={19} />
            </span>
            <h2 className="mt-3.5 text-sm font-semibold text-ink-800">What is running</h2>
            <dl className="mt-3 space-y-2.5 text-xs">
              <div className="flex items-start justify-between gap-3">
                <dt className="text-ink-500">Speech-to-text</dt>
                <dd className="text-right font-medium text-ink-800">
                  {Object.entries(status.speechBackends || {})
                    .filter(([, installed]) => installed)
                    .map(([name]) => name)
                    .join(', ') || 'not installed'}
                </dd>
              </div>
              <div className="flex items-start justify-between gap-3">
                <dt className="text-ink-500">Whisper model</dt>
                <dd className="text-right font-medium text-ink-800">{status.whisperModel}</dd>
              </div>
              <div className="flex items-start justify-between gap-3">
                <dt className="text-ink-500">Intent model</dt>
                <dd className="text-right font-medium text-ink-800">
                  {model.modelVersion || 'not trained'}
                </dd>
              </div>
              {metrics && (
                <>
                  <div className="flex items-start justify-between gap-3">
                    <dt className="text-ink-500">Test accuracy</dt>
                    <dd className="text-right font-medium text-ink-800">
                      {Math.round(metrics.accuracy * 100)}%
                    </dd>
                  </div>
                  <div className="flex items-start justify-between gap-3">
                    <dt className="text-ink-500">Macro F1</dt>
                    <dd className="text-right font-medium text-ink-800">
                      {metrics.macroF1.toFixed(3)}
                    </dd>
                  </div>
                </>
              )}
            </dl>
            <p className="mt-3 flex items-start gap-1.5 rounded-xl bg-ink-50 p-3 text-[11px] leading-relaxed text-ink-500">
              <Info size={13} className="mt-0.5 shrink-0" />
              Whisper is a pretrained third-party model. The intent classifier is trained by
              VisionX on its own dataset of hand-written presenter utterances.
            </p>
          </div>

          <div className="card p-5">
            <h2 className="text-sm font-semibold text-ink-800">What you can say</h2>
            <ul className="mt-3 space-y-3">
              {catalogue
                .filter((row) => row.command)
                .map((row) => (
                  <li key={row.intent}>
                    <p className="text-xs font-medium text-ink-700">{row.label}</p>
                    <p className="mt-0.5 text-[11px] italic text-ink-400">
                      &ldquo;{row.examples?.[0]}&rdquo;
                      {row.examples?.[1] ? `, "${row.examples[1]}"` : ''}
                    </p>
                  </li>
                ))}
            </ul>
            <p className="mt-4 rounded-xl bg-ink-50 p-3 text-[11px] leading-relaxed text-ink-500">
              Anything else is classified as <strong>not a command</strong> and ignored, which is
              what lets you talk normally through a whole presentation.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
