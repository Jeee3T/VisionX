import { useCallback, useEffect, useState } from 'react'
import { Hand, RotateCcw, Save } from 'lucide-react'
import PageHeader from '../components/PageHeader'
import PersonalizationCard from '../components/PersonalizationCard'
import { ErrorState, Loader } from '../components/Feedback'
import { gestureApi, personalizationApi } from '../services/endpoints'
import { useToast } from '../context/ToastContext'
import { COMMANDS, COMMAND_ORDER } from '../utils/constants'

/** Small finger diagram so a pose name is never the only cue. */
function PoseGlyph({ fingers = [] }) {
  const names = ['T', 'I', 'M', 'R', 'P']
  return (
    <span className="flex items-end gap-[3px]">
      {names.map((name, index) => (
        <span
          key={name}
          title={name}
          className={`w-[7px] rounded-sm transition-all ${
            fingers[index] ? 'h-4 bg-brand-500' : 'h-2 bg-ink-300'
          }`}
        />
      ))}
    </span>
  )
}

export default function GestureSettings() {
  const toast = useToast()
  const [data, setData] = useState(null)
  const [draft, setDraft] = useState({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [personalization, setPersonalization] = useState(null)
  const [personalizationSaving, setPersonalizationSaving] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    Promise.all([
      gestureApi.get(),
      // Personalization is additive: if it fails the bindings screen still works.
      personalizationApi.get().catch(() => null),
    ])
      .then(([response, personalizationResponse]) => {
        setData(response.data)
        setDraft(response.data.preferences)
        if (personalizationResponse) setPersonalization(personalizationResponse.data)
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  const updatePersonalization = async (patch) => {
    setPersonalizationSaving(true)
    try {
      const response = await personalizationApi.update(patch)
      setPersonalization(response.data)
    } catch (err) {
      toast.error(err.message)
    } finally {
      setPersonalizationSaving(false)
    }
  }

  const deletePersonalization = async (everything) => {
    const question = everything
      ? 'Delete your personalized model AND every recording you have made? Your presentations, sessions and pose bindings are not affected.'
      : 'Delete your personalized model? VisionX will go back to the geometric recognizer.'
    if (!window.confirm(question)) return
    setPersonalizationSaving(true)
    try {
      const call = everything ? personalizationApi.deleteAll : personalizationApi.deleteModel
      const response = await call()
      toast.success(response.message)
      setPersonalization((await personalizationApi.get()).data)
    } catch (err) {
      toast.error(err.message)
    } finally {
      setPersonalizationSaving(false)
    }
  }

  useEffect(load, [load])

  if (loading) return <Loader label="Loading your gesture map…" />
  if (error) return <ErrorState message={error} onRetry={load} />

  const poses = data.poses
  const usedPoses = COMMAND_ORDER.map((command) => draft[COMMANDS[command].field])
  const duplicates = usedPoses.filter((pose, index) => pose && usedPoses.indexOf(pose) !== index)
  const dirty = COMMAND_ORDER.some(
    (command) => draft[COMMANDS[command].field] !== data.preferences[COMMANDS[command].field],
  )

  const save = async () => {
    setSaving(true)
    try {
      const payload = Object.fromEntries(
        COMMAND_ORDER.map((command) => [COMMANDS[command].field, draft[COMMANDS[command].field]]),
      )
      const response = await gestureApi.update(payload)
      setData(response.data)
      setDraft(response.data.preferences)
      toast.success('Gesture mapping saved. It applies to your next session immediately.')
    } catch (err) {
      toast.error(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Gesture settings"
        subtitle="Bind each command to the hand pose that feels natural to you."
        actions={
          <>
            <button onClick={() => setDraft({ ...draft, ...data.defaults })} className="btn-secondary">
              <RotateCcw size={15} /> Reset to defaults
            </button>
            <button onClick={save} disabled={saving || !dirty || duplicates.length > 0} className="btn-primary">
              <Save size={16} /> {saving ? 'Saving…' : 'Save mapping'}
            </button>
          </>
        }
      />

      {duplicates.length > 0 && (
        <div className="mb-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Each pose can drive only one command. Change the duplicated pose before saving.
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-5 lg:col-span-2">
        <div className="card divide-y divide-ink-100">
          {COMMAND_ORDER.map((command) => {
            const meta = COMMANDS[command]
            const selected = draft[meta.field]
            const pose = poses.find((p) => p.name === selected)
            const isDuplicate = duplicates.includes(selected)

            return (
              <div key={command} className="flex flex-col gap-3 p-5 sm:flex-row sm:items-center">
                <span className={`rounded-xl bg-ink-50 p-2.5 ${meta.colour}`}>
                  <meta.icon size={19} />
                </span>

                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-ink-900">{meta.label}</p>
                  <p className="mt-0.5 text-xs text-ink-400">{pose?.description || 'Choose a hand pose'}</p>
                </div>

                <div className="flex items-center gap-3">
                  <PoseGlyph fingers={pose?.fingers} />
                  <select
                    className={`input w-52 ${isDuplicate ? 'border-amber-400 ring-2 ring-amber-100' : ''}`}
                    value={selected || ''}
                    onChange={(e) => setDraft({ ...draft, [meta.field]: e.target.value })}
                  >
                    {poses.map((option) => (
                      <option key={option.name} value={option.name}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            )
          })}
        </div>

        {personalization && (
          <PersonalizationCard
            settings={personalization.settings}
            gesture={personalization.gesture}
            saving={personalizationSaving}
            onToggle={updatePersonalization}
            onDeleteModel={() => deletePersonalization(false)}
            onDeleteAll={() => deletePersonalization(true)}
          />
        )}
        </div>

        <div className="card h-fit p-5">
          <span className="inline-flex rounded-xl bg-brand-50 p-2.5 text-brand-600">
            <Hand size={19} />
          </span>
          <h2 className="mt-3.5 text-sm font-semibold text-ink-800">How recognition works</h2>
          <ul className="mt-3 space-y-2.5 text-xs leading-relaxed text-ink-500">
            <li>
              Each frame is classified into one of {poses.length} poses from the extension state of your five fingers.
            </li>
            <li>
              A command fires only when the confidence clears your gate, the pose holds for several frames, and a
              neutral state has occurred since the last identical command.
            </li>
            <li>
              Any pose you leave unbound acts as that neutral state — an open palm between two &ldquo;next slide&rdquo;
              gestures is what lets the second one through.
            </li>
          </ul>

          <div className="mt-5 rounded-xl bg-ink-50 p-3.5">
            <p className="text-xs font-medium text-ink-700">Unbound poses (safe to rest in)</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {poses
                .filter((pose) => !usedPoses.includes(pose.name))
                .map((pose) => (
                  <span key={pose.name} className="chip bg-white text-ink-500 shadow-card">
                    {pose.label}
                  </span>
                ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
