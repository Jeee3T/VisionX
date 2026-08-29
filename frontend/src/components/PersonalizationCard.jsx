import { Link } from 'react-router-dom'
import { Brain, CheckCircle2, CircleSlash, Cpu, Trash2, TriangleAlert } from 'lucide-react'

/**
 * The "Personalized gestures" panel on the Gesture settings screen.
 *
 * It is deliberately explicit about which recognizer is active, because the two
 * behave differently: the geometric one derives a confidence from finger
 * geometry, the personalized one reports a real class probability.
 */
export default function PersonalizationCard({
  settings,
  gesture,
  saving,
  onToggle,
  onDeleteModel,
  onDeleteAll,
}) {
  const model = gesture?.model || {}
  const dataset = gesture?.dataset || {}
  const consent = !!settings?.gestureLearningConsent
  const enabled = !!settings?.gesturePersonalizationEnabled
  const active = enabled && model.available

  const metrics = model.metrics?.test
  const samples = dataset.sampleCount || 0
  const recordings = dataset.recordingCount || 0

  return (
    <div className="card p-5">
      <div className="flex items-start gap-3">
        <span className={`rounded-xl p-2.5 ${active ? 'bg-brand-50 text-brand-600' : 'bg-ink-100 text-ink-500'}`}>
          <Brain size={19} />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-ink-900">Personalized recognition</h2>
          <p className="mt-1 text-xs leading-relaxed text-ink-500">
            Train a small model on your own hands. Until you do, VisionX uses the built-in
            geometric recognizer — which never goes away and is always the fallback.
          </p>
        </div>
        <span
          className={`chip shrink-0 ${
            active ? 'bg-emerald-50 text-emerald-700' : 'bg-ink-100 text-ink-600'
          }`}
        >
          {active ? <Cpu size={13} /> : <CircleSlash size={13} />}
          {active ? 'Personalized' : 'Geometric'}
        </span>
      </div>

      {model.error && (
        <div className="mt-4 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
          <TriangleAlert size={15} className="mt-0.5 shrink-0" />
          <span>{model.error} VisionX fell back to the geometric recognizer.</span>
        </div>
      )}

      {/* Consent gates collection; the enable switch gates use. */}
      <div className="mt-5 space-y-3">
        <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-ink-200 p-3.5">
          <input
            type="checkbox"
            checked={consent}
            disabled={saving}
            onChange={(e) => onToggle({ gestureLearningConsent: e.target.checked })}
            className="mt-0.5 h-4 w-4 shrink-0 accent-brand-600"
          />
          <span className="min-w-0">
            <span className="block text-sm font-medium text-ink-800">
              Allow gesture learning / personalization
            </span>
            <span className="mt-0.5 block text-xs text-ink-500">
              Lets VisionX record hand landmark positions from your camera while you train.
              Landmarks are coordinates, never images. Turning this off stops all collection
              immediately; it does not delete what is already stored.
            </span>
          </span>
        </label>

        <label
          className={`flex items-start gap-3 rounded-xl border border-ink-200 p-3.5 ${
            model.available ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'
          }`}
        >
          <input
            type="checkbox"
            checked={enabled}
            disabled={saving || !model.available}
            onChange={(e) => onToggle({ gesturePersonalizationEnabled: e.target.checked })}
            className="mt-0.5 h-4 w-4 shrink-0 accent-brand-600"
          />
          <span className="min-w-0">
            <span className="block text-sm font-medium text-ink-800">
              Use my personalized model in sessions
            </span>
            <span className="mt-0.5 block text-xs text-ink-500">
              {model.available
                ? 'Sessions will use your trained model, with the geometric recognizer as the fallback.'
                : 'Available once you have trained a model.'}
            </span>
          </span>
        </label>
      </div>

      {/* What has been collected and what was learned from it. */}
      <dl className="mt-5 grid grid-cols-3 gap-2.5">
        {[
          { label: 'Recordings', value: recordings },
          { label: 'Frames', value: samples.toLocaleString() },
          {
            label: 'Test accuracy',
            value: metrics?.accuracy != null ? `${Math.round(metrics.accuracy * 100)}%` : '—',
          },
        ].map((item) => (
          <div key={item.label} className="rounded-xl bg-ink-50 px-3 py-2.5">
            <dt className="text-[11px] text-ink-500">{item.label}</dt>
            <dd className="mt-0.5 text-sm font-semibold text-ink-900">{item.value}</dd>
          </div>
        ))}
      </dl>

      {model.available && (
        <div className="mt-3 space-y-1 rounded-xl bg-ink-50 p-3 text-[11px] text-ink-500">
          <p className="flex items-center gap-1.5">
            <CheckCircle2 size={12} className="text-emerald-600" />
            <span className="font-medium text-ink-700">{model.modelVersion}</span>
            <span>· {model.runtime} runtime</span>
            {model.onnx && <span>· ONNX</span>}
          </p>
          {model.synthetic && (
            <p className="text-amber-700">
              Trained on synthetic data — replace it by enrolling your own gestures.
            </p>
          )}
          {metrics?.falseCommandRate && (
            <p>
              False command rate on held-out data:{' '}
              {(metrics.falseCommandRate.fromNull * 100).toFixed(1)}% of non-command frames.
            </p>
          )}
        </div>
      )}

      <div className="mt-5 flex flex-wrap gap-2">
        <Link
          to="/gestures/train"
          className={consent ? 'btn-primary' : 'btn-primary pointer-events-none opacity-50'}
        >
          <Brain size={16} /> {model.available ? 'Retrain my gestures' : 'Train my gestures'}
        </Link>
        {model.available && (
          <button onClick={onDeleteModel} className="btn-secondary" disabled={saving}>
            Delete model
          </button>
        )}
        {(model.available || recordings > 0) && (
          <button onClick={onDeleteAll} className="btn-danger" disabled={saving}>
            <Trash2 size={15} /> Delete all learning data
          </button>
        )}
      </div>

      {!consent && (
        <p className="mt-2.5 text-xs text-ink-400">
          Turn on gesture learning above to start training.
        </p>
      )}
    </div>
  )
}
