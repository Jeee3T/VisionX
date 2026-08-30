import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Always-on microphone capture for the "Vision <command> OK" flow.
 *
 * The presenter opens the microphone once, at the start of the talk, and never
 * touches the web app again. The recorder runs continuously and is cut into short
 * segments; each segment is uploaded, transcribed on the server and offered to the
 * wake-word machine. Ordinary speech does nothing.
 *
 * Segmenting rather than streaming, because MediaRecorder's timeslice chunks are
 * not independently decodable - only the first carries the container header, so a
 * chunk on its own is not a file Whisper can open. Each segment is therefore its
 * own complete recording: stop the recorder, start a new one, upload the finished
 * blob. The restart is immediate, so the gap is a few milliseconds.
 *
 * Segments do NOT overlap - the next recorder starts in the previous one's
 * `onstop` - so the server's state machine is what stitches a command back
 * together across a boundary. That is why segments must be uploaded in order and
 * none may be silently dropped: "Vision" / "next slide" / "OK" is three segments,
 * and losing the middle one loses the command.
 */
const SEGMENT_SECONDS = 3
const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']

// Below this peak level the segment is silence: uploading it would cost a Whisper
// pass to transcribe nothing. Measured on the same 0..1 scale as `level`.
const SILENCE_LEVEL = 0.04
const MIN_SEGMENT_BYTES = 1600

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
    clearTimeout(timerRef.current)
    timerRef.current = null
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
    rafRef.current = requestAnimationFrame(meter)
  }, [])

  /** Record one segment, upload it, and immediately start the next. */
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
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' })
      chunksRef.current = []
      const loudEnough = peakRef.current >= SILENCE_LEVEL && blob.size > MIN_SEGMENT_BYTES

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

    timerRef.current = setTimeout(() => {
      if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
    }, SEGMENT_SECONDS * 1000)
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
    segmentSeconds: SEGMENT_SECONDS,
  }
}
