import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Push-to-talk microphone capture.
 *
 * Deliberately NOT an always-on recorder. Recording starts when the presenter
 * asks for it and stops on release or at MAX_SECONDS, so the microphone is never
 * quietly listening through a whole talk. Each recording is one short utterance,
 * uploaded once, transcribed on the server and discarded.
 */
const MAX_SECONDS = 8
const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']

export default function useVoiceCapture({ onUtterance } = {}) {
  const [recording, setRecording] = useState(false)
  const [permission, setPermission] = useState('unknown') // unknown | granted | denied
  const [level, setLevel] = useState(0)
  const [error, setError] = useState(null)

  const streamRef = useRef(null)
  const recorderRef = useRef(null)
  const chunksRef = useRef([])
  const timerRef = useRef(null)
  const analyserRef = useRef(null)
  const audioContextRef = useRef(null)
  const rafRef = useRef(null)

  const supported =
    typeof window !== 'undefined' &&
    !!navigator?.mediaDevices?.getUserMedia &&
    typeof window.MediaRecorder !== 'undefined'

  const cleanup = useCallback(() => {
    clearTimeout(timerRef.current)
    cancelAnimationFrame(rafRef.current)
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    recorderRef.current = null
    analyserRef.current = null
    audioContextRef.current?.close().catch(() => {})
    audioContextRef.current = null
    setLevel(0)
  }, [])

  useEffect(() => cleanup, [cleanup])

  const meter = useCallback(() => {
    const analyser = analyserRef.current
    if (!analyser) return
    const buffer = new Uint8Array(analyser.frequencyBinCount)
    analyser.getByteTimeDomainData(buffer)
    let peak = 0
    for (let i = 0; i < buffer.length; i += 1) peak = Math.max(peak, Math.abs(buffer[i] - 128))
    setLevel(Math.min(1, peak / 64))
    rafRef.current = requestAnimationFrame(meter)
  }, [])

  const stop = useCallback(() => {
    clearTimeout(timerRef.current)
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
    setRecording(false)
  }, [])

  const start = useCallback(async () => {
    if (!supported) {
      setError('This browser cannot record audio. Use the typed command box instead.')
      return
    }
    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      })
      streamRef.current = stream
      setPermission('granted')

      const AudioContextClass = window.AudioContext || window.webkitAudioContext
      if (AudioContextClass) {
        const context = new AudioContextClass()
        const analyser = context.createAnalyser()
        analyser.fftSize = 512
        context.createMediaStreamSource(stream).connect(analyser)
        audioContextRef.current = context
        analyserRef.current = analyser
        rafRef.current = requestAnimationFrame(meter)
      }

      const mimeType = MIME_CANDIDATES.find((type) => MediaRecorder.isTypeSupported(type))
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      chunksRef.current = []

      recorder.ondataavailable = (event) => {
        if (event.data?.size) chunksRef.current.push(event.data)
      }
      recorder.onstop = async () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' })
        chunksRef.current = []
        cleanup()
        // A blob under ~1 KB is silence or a mis-tap, not an utterance.
        if (blob.size > 1024 && onUtterance) await onUtterance(blob)
      }

      recorderRef.current = recorder
      recorder.start()
      setRecording(true)
      timerRef.current = setTimeout(stop, MAX_SECONDS * 1000)
    } catch (err) {
      setPermission(err?.name === 'NotAllowedError' ? 'denied' : 'unknown')
      setError(
        err?.name === 'NotAllowedError'
          ? 'Microphone access was blocked. Allow it in your browser settings to use voice commands.'
          : `Could not open the microphone: ${err?.message || err}`,
      )
      cleanup()
      setRecording(false)
    }
  }, [supported, meter, cleanup, onUtterance, stop])

  return { supported, recording, permission, level, error, start, stop, maxSeconds: MAX_SECONDS }
}
