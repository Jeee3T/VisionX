import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Always-on microphone capture for the "Vision <command> OK" flow.
 *
 * The presenter opens the microphone once, at the start of the talk, and never
 * touches the web app again. The recorder runs continuously and is cut into
 * segments; each segment is uploaded, transcribed on the server and offered to
 * the wake-word machine. Ordinary speech does nothing.
 *
 * ## Why segments end on silence, not on a timer
 *
 * This used to cut a new segment every 3 seconds, and that fixed timer was the
 * single largest source of voice latency in VisionX. It sat *before* every other
 * cost in the pipeline:
 *
 *     presenter says "...OK"
 *          |
 *          |  up to 3 s   <-- waiting for the timer to close the segment
 *          v
 *     upload -> Whisper -> intent -> dispatch
 *
 * The presenter had already finished speaking, VisionX had the audio, and it was
 * waiting for a clock. Worse, it was *unpredictable*: the same command took
 * 0.3 s or 3.3 s depending on where in the window it happened to land.
 *
 * A segment now ends when the presenter stops talking. `END_SILENCE_MS` after
 * the level drops below the speech threshold, the recorder is closed and the
 * segment goes up. Speech that keeps going is still cut at `MAX_SEGMENT_MS` so a
 * presenter mid-sentence never blocks a command that completed earlier in it.
 *
 * That turns "OK" into a full stop the machine can hear: the audio is on its way
 * to Whisper within ~350 ms of the word ending, instead of up to 3 s later.
 *
 * ## Ordering
 *
 * Segments do NOT overlap - the next recorder starts in the previous one's
 * `onstop` - so the server's state machine is what stitches a command back
 * together across a boundary. Segments must therefore be uploaded in order and
 * none may be silently dropped: "Vision" / "next slide" / "OK" can be three
 * segments, and losing the middle one loses the command.
 */
const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']

// Below this peak level the segment is silence: uploading it would cost a Whisper
// pass to transcribe nothing. Measured on the same 0..1 scale as `level`.
const SILENCE_LEVEL = 0.04
const MIN_SEGMENT_BYTES = 1600

// --- endpointing ------------------------------------------------------------
// How long the level must stay below SILENCE_LEVEL before the segment is
// considered finished. Long enough to survive the pause between words in "go to
// next slide", short enough that "OK" is on its way to Whisper almost at once.
const END_SILENCE_MS = 350
// Speech shorter than this is a cough, a door, a chair. Such a segment is still
// CLOSED promptly - holding the recorder open would delay the next real command -
// but it is not uploaded: an almost-empty Whisper pass costs as much as a real
// one and is where Whisper invents text.
//
// 150 ms, not more: "OK" on its own is a legitimate segment of roughly 250 ms,
// and dropping it would lose the command it terminates.
const MIN_SPEECH_MS = 150
// A hard ceiling for continuous speech. A presenter who talks without pausing
// still gets cut here, so a command completed early in a long sentence is not
// held hostage by the rest of it. Also bounds what one Whisper pass has to chew.
const MAX_SEGMENT_MS = 2500
// While nobody is speaking the recorder is simply restarted rather than left
// accumulating silence, so a segment never opens with 20 seconds of nothing in
// front of the words.
const MAX_IDLE_MS = 4000
// How often the endpointer looks at the level. The meter runs on rAF (~60 Hz),
// which is more than enough resolution; this is just the decision interval.
const ENDPOINT_TICK_MS = 50

// Transcription is slower than capture, so segments queue behind one another.
// The queue is bounded: if the server falls this far behind, the presenter has
// spoken ~15 seconds we have not processed and catching up is hopeless — better
// to drop the oldest and stay near the present than to replay stale audio.
const MAX_QUEUED_SEGMENTS = 5

export default function useContinuousVoice({ onSegment, enabled = false } = {}) {
  const [listening, setListening] = useState(false)
  const [permission, setPermission] = useState('unknown') // unknown | granted | denied
  const [level, setLevel] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const streamRef = useRef(null)
  const recorderRef = useRef(null)
  const chunksRef = useRef([])
  const timerRef = useRef(null)
  const analyserRef = useRef(null)
  const audioContextRef = useRef(null)
  const rafRef = useRef(null)
  const runningRef = useRef(false)
  const peakRef = useRef(0)
  const onSegmentRef = useRef(onSegment)
  const inFlightRef = useRef(false)
  const startingRef = useRef(false)
  const queueRef = useRef([])
  const [dropped, setDropped] = useState(0)

  // --- endpointer state ----------------------------------------------------
  // Written on the rAF meter and read on the endpoint tick, never in render, so
  // they are refs: a state update per audio frame would re-render 60 times a
  // second and is exactly the kind of work that makes a laptop drop camera frames.
  const speechStartedRef = useRef(0)   // when speech began in this segment, 0 = not yet
  const lastLoudRef = useRef(0)        // the most recent frame above SILENCE_LEVEL
  const spokeForRef = useRef(0)        // how long speech lasted, measured at close
  const closingRef = useRef(false)     // this segment is already being closed

  // Kept in a ref so restarting the recorder every few seconds does not need to
  // re-create the whole capture pipeline when the callback identity changes.
  useEffect(() => {
    onSegmentRef.current = onSegment
  }, [onSegment])

  const supported =
    typeof window !== 'undefined' &&
    !!navigator?.mediaDevices?.getUserMedia &&
    typeof window.MediaRecorder !== 'undefined'

  const drain = useCallback(async () => {
    // One upload at a time, in order: the server's wake machine is stateful, so
    // segments arriving out of order would interleave two halves of a command.
    if (inFlightRef.current) return
    inFlightRef.current = true
    setBusy(true)
    try {
      // Re-checked each pass: segments recorded while the previous upload was in
      // flight are picked up here rather than starting a second drain.
      while (queueRef.current.length) {
        // The user turned listening off while this was queued. Uploading now
        // could execute a command after they stopped — including after they
        // navigated away from the session — so abandon what is left.
        if (!runningRef.current || !onSegmentRef.current) {
          queueRef.current = []
          break
        }
        const blob = queueRef.current.shift()
        try {
          await onSegmentRef.current(blob)
        } catch {
          /* the caller surfaces its own errors; keep the queue moving */
        }
      }
    } finally {
      inFlightRef.current = false
      setBusy(false)
    }
  }, [])

  const teardown = useCallback(() => {
    runningRef.current = false
    // Also cancels a start() that is still awaiting getUserMedia, so the stream
    // it eventually receives is stopped rather than left running.
    startingRef.current = false
    queueRef.current = []
    clearInterval(timerRef.current)
    timerRef.current = null
    closingRef.current = false
    speechStartedRef.current = 0
    cancelAnimationFrame(rafRef.current)
    rafRef.current = null
    try {
      if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
    } catch {
      /* the recorder was already gone */
    }
    recorderRef.current = null
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    analyserRef.current = null
    audioContextRef.current?.close().catch(() => {})
    audioContextRef.current = null
    setLevel(0)
    setListening(false)
  }, [])

  const meter = useCallback(() => {
    const analyser = analyserRef.current
    if (!analyser) return
    const buffer = new Uint8Array(analyser.frequencyBinCount)
    analyser.getByteTimeDomainData(buffer)
    let peak = 0
    for (let i = 0; i < buffer.length; i += 1) peak = Math.max(peak, Math.abs(buffer[i] - 128))
    const value = Math.min(1, peak / 64)
    setLevel(value)
    // Remembered across the segment so a quiet segment that contained one loud
    // word is still uploaded.
    peakRef.current = Math.max(peakRef.current, value)

    if (value >= SILENCE_LEVEL) {
      const now = performance.now()
      lastLoudRef.current = now
      // The first loud frame of this segment is where speech began. Used to
      // refuse segments too short to be a word, and to decide whether the
      // segment contained speech at all.
      if (!speechStartedRef.current) speechStartedRef.current = now
    }
    rafRef.current = requestAnimationFrame(meter)
  }, [])

  /** Record one segment, upload it, and immediately start the next.
   *
   * The segment ends when the presenter stops talking, not on a timer - see the
   * module comment. `closeSegment` is idempotent because three independent
   * conditions can reach it (silence, the length ceiling, the idle ceiling) and
   * they can all be true in the same tick.
   */
  const recordSegment = useCallback(() => {
    const stream = streamRef.current
    if (!runningRef.current || !stream) return

    let recorder
    try {
      const mimeType = MIME_CANDIDATES.find((type) => MediaRecorder.isTypeSupported(type))
      recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
    } catch (err) {
      setError(`Could not start the recorder: ${err?.message || err}`)
      teardown()
      return
    }

    recorderRef.current = recorder
    chunksRef.current = []
    peakRef.current = 0

    recorder.ondataavailable = (event) => {
      if (event.data?.size) chunksRef.current.push(event.data)
    }

    recorder.onstop = () => {
      clearInterval(timerRef.current)
      timerRef.current = null
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' })
      chunksRef.current = []
      // Four ways a segment can be worth nothing: no speech at all, speech too
      // brief to be a word, a loudest moment that was still silence, or too few
      // bytes to hold one. Each costs a Whisper pass to learn nothing.
      const loudEnough =
        speechStartedRef.current > 0 &&
        spokeForRef.current >= MIN_SPEECH_MS &&
        peakRef.current >= SILENCE_LEVEL &&
        blob.size > MIN_SEGMENT_BYTES

      // Start the next segment before awaiting the upload, so listening never
      // pauses while the server transcribes.
      if (runningRef.current) recordSegment()

      // Nothing is uploaded once listening has been turned off, so a command
      // cannot execute after the presenter stopped or navigated away.
      if (!loudEnough || !runningRef.current || !onSegmentRef.current) return

      // Queue rather than drop: dropping a segment mid-command loses the whole
      // command, because "Vision" / "next slide" / "OK" is three of them.
      queueRef.current.push(blob)
      while (queueRef.current.length > MAX_QUEUED_SEGMENTS) {
        queueRef.current.shift()
        setDropped((count) => count + 1)
      }
      drain()
    }

    try {
      recorder.start()
    } catch (err) {
      setError(`Could not start the recorder: ${err?.message || err}`)
      teardown()
      return
    }

    // A fresh segment: nothing spoken in it yet, and the silence clock starts now.
    const openedAt = performance.now()
    speechStartedRef.current = 0
    lastLoudRef.current = 0
    spokeForRef.current = 0
    closingRef.current = false

    const closeSegment = () => {
      if (closingRef.current) return
      closingRef.current = true
      // Measured here rather than in `onstop`: by the time the recorder's stop
      // event fires, the meter has been running through the trailing silence and
      // `lastLoud` still points at the last real speech - but the next segment's
      // reset may already have run if the recorder stopped slowly.
      spokeForRef.current = speechStartedRef.current
        ? lastLoudRef.current - speechStartedRef.current
        : 0
      clearInterval(timerRef.current)
      timerRef.current = null
      if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
    }

    timerRef.current = setInterval(() => {
      if (!runningRef.current) return closeSegment()
      const now = performance.now()
      const spoke = speechStartedRef.current > 0

      if (!spoke) {
        // Nothing has been said in this segment. Recycle the recorder rather than
        // let it accumulate silence, so when the presenter does speak, the words
        // are near the front of a short segment instead of buried in a long one.
        if (now - openedAt >= MAX_IDLE_MS) closeSegment()
        return
      }

      // Speech, then quiet for long enough to be a full stop. This is the path
      // that "Vision next slide OK" takes, and it fires ~350 ms after "OK".
      //
      // No minimum length is applied here on purpose. Whether the segment was
      // long enough to be worth transcribing is an *upload* question, decided in
      // `onstop`; holding the recorder open to answer it would delay the next
      // real command by exactly as long as the noise that triggered it.
      if (now - lastLoudRef.current >= END_SILENCE_MS) {
        closeSegment()
        return
      }

      // Still talking. Cut anyway at the ceiling so a command that completed
      // early in a long sentence is not held hostage by the rest of it.
      if (now - openedAt >= MAX_SEGMENT_MS) closeSegment()
    }, ENDPOINT_TICK_MS)
  }, [teardown, drain])

  const start = useCallback(async () => {
    if (!supported) {
      setError('This browser cannot record audio. Use the typed command box instead.')
      return
    }
    // Claimed SYNCHRONOUSLY, before the await. `getUserMedia` takes long enough
    // for a second click to land (the button still reads "off" until it
    // resolves), and two streams would give two independent recorder chains
    // uploading interleaved segments - which corrupts the server's stateful wake
    // machine, since "Vision"/"next slide"/"OK" would arrive out of order.
    if (runningRef.current || startingRef.current) return
    startingRef.current = true

    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      })

      // Torn down while the permission prompt was open - the presenter navigated
      // away or stopped listening. `teardown` ran with `streamRef.current` still
      // null, so it stopped nothing: this stream must be released here, or the
      // microphone stays live and keeps executing commands after they stopped.
      if (!startingRef.current) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }

      streamRef.current = stream
      setPermission('granted')

      const AudioContextClass = window.AudioContext || window.webkitAudioContext
      if (AudioContextClass) {
        const context = new AudioContextClass()
        // Autoplay policies can hand back a suspended context even after a user
        // gesture; without this the meter reads a flat zero and every segment
        // looks like silence.
        if (context.state === 'suspended') await context.resume().catch(() => {})
        const analyser = context.createAnalyser()
        analyser.fftSize = 512
        context.createMediaStreamSource(stream).connect(analyser)
        audioContextRef.current = context
        analyserRef.current = analyser
        rafRef.current = requestAnimationFrame(meter)
      }

      runningRef.current = true
      startingRef.current = false
      setListening(true)
      recordSegment()
    } catch (err) {
      setPermission(err?.name === 'NotAllowedError' ? 'denied' : 'unknown')
      setError(
        err?.name === 'NotAllowedError'
          ? 'Microphone access was blocked. Allow it in your browser settings, then reload, to use continuous voice control.'
          : `Could not open the microphone: ${err?.message || err}`,
      )
      teardown()
    }
  }, [supported, meter, recordSegment, teardown])

  const stop = useCallback(() => teardown(), [teardown])

  // The caller decides when listening should be on; the hook keeps the recorder
  // matched to that. Nothing auto-starts, because opening a microphone always
  // needs a user gesture.
  useEffect(() => {
    if (!enabled && runningRef.current) teardown()
  }, [enabled, teardown])

  useEffect(() => teardown, [teardown])

  return {
    supported,
    listening,
    permission,
    level,
    busy,
    error,
    // How many segments were discarded because transcription could not keep up.
    // Surfaced so a machine too slow for continuous listening says so rather than
    // just missing commands.
    dropped,
    start,
    stop,
    // The endpointer's shape, for a UI that wants to explain the behaviour.
    // There is no fixed segment length any more: a segment is as long as the
    // presenter talks, bounded by MAX_SEGMENT_MS.
    endSilenceMs: END_SILENCE_MS,
    maxSegmentMs: MAX_SEGMENT_MS,
  }
}
