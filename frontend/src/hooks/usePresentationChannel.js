import { useEffect, useRef, useState } from 'react'
import { streamUrl } from '../services/api'
import { engineApi } from '../services/endpoints'

/**
 * The presentation window's live channel.
 *
 * One SSE connection carries everything the window needs, but the two kinds of
 * traffic on it are handled very differently, and that difference is the whole
 * point of this hook:
 *
 *   commands / state / ink events   ->  React state    (a few per second)
 *   pointer positions               ->  a ref + rAF    (camera frame rate)
 *
 * Pointer positions deliberately never touch React state. Re-rendering the whole
 * presentation on every fingertip sample is how a pointer ends up lagging, and
 * `setState` at 30 Hz on the same machine that is running MediaPipe is real work
 * taken away from the camera loop. Subscribers read `pointerRef.current` inside
 * their own animation frame instead.
 *
 * `pointerRef` holds the *interpolated* position. The server sends discrete
 * samples; the display runs at whatever the monitor does. Interpolating between
 * the last two samples means the dot moves continuously rather than stepping
 * once per network event, which is what makes it look like it is following a
 * hand rather than being teleported after it.
 */

// How fast the rendered position closes on the newest sample, per 60 Hz frame.
// High enough that the dot is never visibly behind the fingertip; low enough
// that a single noisy sample does not make it twitch.
const FOLLOW = 0.45

// After this long with no sample the hand has gone. Long enough to ride out a
// dropped frame or two, short enough that the dot does not linger on screen
// after the presenter lowers their hand.
const POINTER_TIMEOUT_MS = 700

export default function usePresentationChannel({ enabled = true } = {}) {
  const [connected, setConnected] = useState(false)
  const [state, setState] = useState({
    slide: 1,
    totalSlides: 0,
    mode: 'IDLE',
    pointerActive: false,
    annotationActive: false,
    blankScreen: null,
    running: false,
    // False after END_PRESENTATION, true again after START_PRESENTATION. The
    // window stays open either way - a misheard "end presentation" mid-talk
    // must be recoverable, and closing the window is not.
    presenting: true,
  })
  const [lastCommand, setLastCommand] = useState(null)
  // Ink events are consumed by the canvas, which needs each one exactly once -
  // so they arrive as a counter-stamped object rather than a list that would
  // grow for the length of the talk.
  const [inkEvent, setInkEvent] = useState(null)

  // --- the pointer, deliberately outside React ------------------------------
  const pointerRef = useRef({ x: 0.5, y: 0.5, drawing: false, visible: false })
  const targetRef = useRef(null)
  const rafRef = useRef(null)
  const sourceRef = useRef(null)
  const inkSeq = useRef(0)

  // Seed from the session's current state before the first event arrives.
  //
  // The stream only replays the most recent *telemetry*, which carries no slide
  // number - so a presentation window opened mid-talk, or reloaded after a
  // crash, would show slide 1 until the presenter happened to change slides.
  // Asking once on mount is what makes it open on the slide they are actually on.
  useEffect(() => {
    if (!enabled) return undefined
    let cancelled = false
    engineApi
      .status()
      .then((response) => {
        const data = response.data
        if (cancelled || !data) return
        setState((previous) => ({
          ...previous,
          slide: data.currentSlide ?? previous.slide,
          totalSlides: data.totalSlides ?? previous.totalSlides,
          mode: data.mode ?? previous.mode,
          pointerActive: data.pointerActive ?? previous.pointerActive,
          annotationActive: data.annotationActive ?? previous.annotationActive,
          blankScreen: 'blankScreen' in data ? data.blankScreen : previous.blankScreen,
          running: data.running ?? previous.running,
        }))
      })
      .catch(() => {
        /* No session yet - the window waits for the stream to tell it otherwise. */
      })
    return () => {
      cancelled = true
    }
  }, [enabled])

  useEffect(() => {
    if (!enabled) return undefined

    const source = new EventSource(streamUrl('/engine/stream'))
    sourceRef.current = source

    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false) // EventSource reconnects on its own

    source.onmessage = (event) => {
      let payload
      try {
        payload = JSON.parse(event.data)
      } catch {
        return
      }

      switch (payload.type) {
        case 'pointer':
          // The hot path: a ref write and nothing else. No state, no render.
          targetRef.current = {
            x: payload.x,
            y: payload.y,
            drawing: !!payload.drawing,
            at: performance.now(),
          }
          break

        case 'command':
          setLastCommand({ ...payload, receivedAt: Date.now() })
          setState((previous) => ({
            ...previous,
            slide: payload.currentSlide ?? previous.slide,
            totalSlides: payload.totalSlides ?? previous.totalSlides,
            pointerActive: payload.pointerActive ?? previous.pointerActive,
            annotationActive: payload.annotationActive ?? previous.annotationActive,
            blankScreen: 'blankScreen' in payload ? payload.blankScreen : previous.blankScreen,
          }))
          break

        case 'telemetry':
          // Only the mode: everything else in telemetry belongs to the control
          // window, and the presentation must not re-render 12 times a second
          // for a frame counter the audience cannot see.
          setState((previous) =>
            payload.mode === previous.mode ? previous : { ...previous, mode: payload.mode },
          )
          break

        case 'state':
          setState((previous) => ({
            ...previous,
            slide: payload.currentSlide ?? previous.slide,
            totalSlides: payload.totalSlides ?? previous.totalSlides,
            mode: payload.mode ?? previous.mode,
            pointerActive: payload.pointerActive ?? previous.pointerActive,
            annotationActive: payload.annotationActive ?? previous.annotationActive,
            blankScreen: 'blankScreen' in payload ? payload.blankScreen : previous.blankScreen,
            running: payload.running ?? previous.running,
          }))
          break

        case 'slide':
          setState((previous) => ({ ...previous, slide: payload.currentSlide ?? previous.slide }))
          break

        case 'presentation':
          if (payload.action === 'END' || payload.action === 'START') {
            setState((previous) => ({ ...previous, presenting: payload.action === 'START' }))
          }
          break

        case 'ink':
          inkSeq.current += 1
          setInkEvent({ ...payload, seq: inkSeq.current })
          break

        case 'annotations_cleared':
          inkSeq.current += 1
          setInkEvent({ type: 'ink', action: 'CLEAR', slide: payload.slide, seq: inkSeq.current })
          break

        default:
          break
      }
    }

    return () => {
      source.close()
      sourceRef.current = null
      setConnected(false)
    }
  }, [enabled])

  // --- interpolation loop ---------------------------------------------------
  // Runs for the life of the window, independent of the network. A sample that
  // arrives between two frames is picked up by the next one; a sample that never
  // arrives simply leaves the dot where it was until it times out.
  useEffect(() => {
    if (!enabled) return undefined

    const step = () => {
      const target = targetRef.current
      const current = pointerRef.current
      if (target) {
        const stale = performance.now() - target.at > POINTER_TIMEOUT_MS
        if (stale) {
          current.visible = false
          current.drawing = false
        } else {
          if (!current.visible) {
            // First sample after the hand reappears: jump rather than sweep in
            // from wherever it was last seen, which would draw a line across the
            // slide the presenter never made.
            current.x = target.x
            current.y = target.y
          } else {
            current.x += (target.x - current.x) * FOLLOW
            current.y += (target.y - current.y) * FOLLOW
          }
          current.visible = true
          current.drawing = target.drawing
        }
      }
      rafRef.current = requestAnimationFrame(step)
    }

    rafRef.current = requestAnimationFrame(step)
    return () => cancelAnimationFrame(rafRef.current)
  }, [enabled])

  return { connected, state, lastCommand, inkEvent, pointerRef }
}
