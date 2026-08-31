import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import usePresentationChannel from '../hooks/usePresentationChannel'
import { annotationApi, engineApi, presentationApi } from '../services/endpoints'
import { API_BASE, getToken } from '../services/api'

/**
 * The VisionX presentation window.
 *
 * This is what the audience looks at, and it is deliberately not the VisionX
 * application: no sidebar, no topbar, no controls, no camera preview. It is
 * opened in its own browser window by the session screen, so the presenter can
 * put it on the projector and keep the control window on their laptop.
 *
 * Everything on it is driven by the same command dispatcher that gestures,
 * voice, the control bar and the keyboard all go through - it renders state, it
 * never decides anything:
 *
 *     gesture / voice -> dispatcher -> SSE -> this window
 *
 * ## Why the ink and the pointer are drawn imperatively
 *
 * The slide number, the mode and the blank screen are React state: they change a
 * few times a minute. The pointer and the stroke in progress change at camera
 * frame rate, and rendering those through React would put a full reconciliation
 * between the presenter's fingertip and the screen. They are written straight to
 * a canvas and a transform inside one animation frame instead.
 *
 * ## Alignment
 *
 * The slide is letterboxed into the window, and the canvas is sized to the
 * *slide*, not the window. A 4:3 deck on a 16:9 screen therefore has ink that
 * lands where the presenter pointed rather than offset by the size of the black
 * bars.
 */

// The hand cannot comfortably reach the edges of the camera frame, so the usable
// region is inset and stretched back over the slide. Must match
// PointerController.MARGIN_X/Y on the server - if the two disagree, the dot
// drifts away from the fingertip towards the edges.
const MARGIN = 0.15
const stretch = (value) => Math.min(1, Math.max(0, (value - MARGIN) / (1 - 2 * MARGIN)))

// A stored stroke is in one of two coordinate spaces and they must not be
// conflated: 'camera' is the fingertip over the (inset) camera frame and is
// stretched when drawn, 'slide' is a mouse/touch stroke already in the slide's
// own coordinates and must be drawn as-is. Strokes written before the space was
// recorded all came from the gesture engine, hence the default.
const needsStretch = (stroke) => (stroke?.space || 'camera') === 'camera'

const INK_COLOUR = '#ef4444'
const INK_WIDTH = 4
// Slides on either side of the current one are fetched ahead, so arriving at the
// next slide is a cache hit rather than a render.
const PREFETCH_RADIUS = 2

export default function Present() {
  const [params] = useSearchParams()
  const presentationId = params.get('presentationId')

  const [presentation, setPresentation] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [aspect, setAspect] = useState(16 / 9)
  const [viewport, setViewport] = useState({ width: 0, height: 0 })
  const [showChrome, setShowChrome] = useState(true)

  const { connected, state, inkEvent, pointerRef } = usePresentationChannel({ enabled: true })

  const slide = state.slide
  const containerRef = useRef(null)
  const canvasRef = useRef(null)
  const dotRef = useRef(null)

  // Strokes live in refs, not state: a stroke grows by a point every frame.
  const savedStrokes = useRef([])       // persisted ink for this slide, from the API
  const liveStrokes = useRef([])        // strokes drawn in this window since it opened
  const activeStroke = useRef(null)     // the one being drawn right now
  const needsRedraw = useRef(true)

  // --- the deck ------------------------------------------------------------
  useEffect(() => {
    if (!presentationId) return
    presentationApi
      .get(presentationId)
      .then((response) => setPresentation(response.data.presentation))
      .catch((error) => setLoadError(error.message))
  }, [presentationId])

  const totalSlides = presentation?.totalSlides || state.totalSlides || 0

  const slideUrl = useCallback(
    (index) => {
      if (!presentationId || index < 1 || (totalSlides && index > totalSlides)) return null
      // Rendered at the window's own pixel width, so a 4K projector gets a 4K
      // slide and a laptop does not pay for one. Rounded to 320px steps so that
      // dragging the window between monitors reuses the cached render instead of
      // asking the server for a new one at every intermediate size.
      const width = Math.min(
        2560,
        Math.max(1280, Math.ceil(((viewport.width || 1920) * (window.devicePixelRatio || 1)) / 320) * 320),
      )
      return `${API_BASE}/presentations/${presentationId}/render/${index}?w=${width}&token=${getToken()}`
    },
    [presentationId, totalSlides, viewport.width],
  )

  // Prefetch the neighbours while the current slide is on screen, so a Next
  // Slide gesture lands on an image the browser already has.
  useEffect(() => {
    for (let offset = -PREFETCH_RADIUS; offset <= PREFETCH_RADIUS; offset += 1) {
      if (offset === 0) continue
      const url = slideUrl(slide + offset)
      if (url) {
        const image = new Image()
        image.src = url
      }
    }
  }, [slide, slideUrl])

  // --- persisted ink for this slide ----------------------------------------
  // The slide this window is currently showing, readable from an async callback.
  // Two fetches can be in flight at once when the presenter moves quickly, and
  // they can complete out of order - the guard below is what stops slide 2's ink
  // being painted onto slide 3.
  const showingSlide = useRef(slide)
  showingSlide.current = slide

  const loadSavedInk = useCallback(
    (forSlide) => {
      if (!presentationId) {
        savedStrokes.current = []
        needsRedraw.current = true
        return
      }
      annotationApi
        .forSlide(presentationId, forSlide)
        .then((response) => {
          // The presenter has moved on since this request was sent. Its ink
          // belongs to a slide that is no longer on screen, so drop it: the
          // fetch for the slide that IS on screen is already in flight.
          if (showingSlide.current !== forSlide) return
          savedStrokes.current = (response.data.annotations || []).map((row) => ({
            points: row.annotationData?.points || [],
            colour: row.annotationData?.colour || INK_COLOUR,
            width: row.annotationData?.width || INK_WIDTH,
            space: row.annotationData?.space,
          }))
          needsRedraw.current = true
        })
        .catch(() => {
          if (showingSlide.current !== forSlide) return
          savedStrokes.current = []
          needsRedraw.current = true
        })
    },
    [presentationId],
  )

  // A slide change resets the ink: strokes belong to the slide they were drawn
  // on, and carrying them forward would scribble over the next one. Cleared
  // synchronously so the new slide is never shown with the old slide's ink while
  // the fetch is in flight.
  useEffect(() => {
    savedStrokes.current = []
    liveStrokes.current = []
    activeStroke.current = null
    needsRedraw.current = true
    loadSavedInk(slide)
  }, [slide, loadSavedInk])

  // --- ink events from the dispatcher --------------------------------------
  useEffect(() => {
    if (!inkEvent) return
    if (inkEvent.action === 'CLEAR') {
      // Everything, saved and live: Clear Annotation means the slide is clean,
      // and leaving the ink that happened to be persisted would look like the
      // command half-worked. The server has already deleted it from MongoDB.
      savedStrokes.current = []
      liveStrokes.current = []
      activeStroke.current = null
    } else if (inkEvent.action === 'BEGIN') {
      // BEGIN carries the point the pen went down on. The dispatcher moves the
      // pointer to the start position and only then presses the pen, so that
      // first sample arrives with drawing=false and the frame loop never sees
      // it; seeding it here is what stops every stroke starting a point late.
      //
      // PREPENDED, not assigned: the frame loop may already have opened this
      // stroke (see above) and replacing it would throw away the points drawn
      // between the pen going down and this effect running.
      const start =
        typeof inkEvent.x === 'number' && typeof inkEvent.y === 'number'
          ? { x: inkEvent.x, y: inkEvent.y }
          : null
      if (!activeStroke.current) {
        activeStroke.current = {
          points: start ? [start] : [],
          colour: INK_COLOUR,
          width: INK_WIDTH,
        }
      } else if (start) {
        activeStroke.current.points.unshift(start)
      }
    } else if (inkEvent.action === 'END') {
      if (activeStroke.current?.points.length > 1) liveStrokes.current.push(activeStroke.current)
      activeStroke.current = null
    }
    needsRedraw.current = true
  }, [inkEvent])

  // --- geometry ------------------------------------------------------------
  useEffect(() => {
    const element = containerRef.current
    if (!element) return undefined
    const observer = new ResizeObserver(([entry]) => {
      setViewport({ width: entry.contentRect.width, height: entry.contentRect.height })
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  // The slide's box inside the window: letterboxed to its own aspect ratio, so
  // normalised coordinates map onto the slide rather than onto the black bars.
  const stage = useMemo(() => {
    const { width, height } = viewport
    if (!width || !height) return { width: 0, height: 0, left: 0, top: 0 }
    const byWidth = { width, height: width / aspect }
    const box = byWidth.height <= height ? byWidth : { width: height * aspect, height }
    return {
      ...box,
      left: (width - box.width) / 2,
      top: (height - box.height) / 2,
    }
  }, [viewport, aspect])

  // --- the frame loop ------------------------------------------------------
  // One animation frame does both jobs: move the dot and, if anything changed,
  // repaint the ink. Nothing here allocates per frame in the common case.
  useEffect(() => {
    let frame
    const draw = () => {
      const canvas = canvasRef.current
      const pointer = pointerRef.current

      if (canvas && stage.width > 0) {
        const ratio = window.devicePixelRatio || 1
        const pixelWidth = Math.round(stage.width * ratio)
        const pixelHeight = Math.round(stage.height * ratio)
        if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
          canvas.width = pixelWidth
          canvas.height = pixelHeight
          needsRedraw.current = true
        }

        // Extend the stroke in progress before deciding whether to repaint, so a
        // new point always shows up on the same frame it arrived.
        if (pointer.visible && pointer.drawing) {
          // The pointer stream is the authority on whether ink is being laid
          // down, not the BEGIN event.
          //
          // Both arrive in the same SSE batch, but they land at different times:
          // a pointer sample is written straight to a ref and is readable on
          // this very frame, while BEGIN goes through React state and an effect.
          // Requiring `activeStroke.current` to exist first therefore dropped
          // every point between the pen going down and React re-rendering - a
          // visible late start on every stroke, and worse the busier the tab is.
          //
          // BEGIN still matters: it carries the exact point the pen went down
          // on, which the effect seeds into the stroke. This branch only covers
          // the case where drawing started before that seed arrived.
          if (!activeStroke.current) {
            activeStroke.current = { points: [], colour: INK_COLOUR, width: INK_WIDTH }
          }
          const point = { x: pointer.x, y: pointer.y }
          const last = activeStroke.current.points[activeStroke.current.points.length - 1]
          // Sub-pixel moves are dropped: they cost a repaint and change nothing,
          // and a resting hand produces a lot of them.
          if (!last || Math.abs(last.x - point.x) > 0.002 || Math.abs(last.y - point.y) > 0.002) {
            activeStroke.current.points.push(point)
            needsRedraw.current = true
          }
        }

        if (needsRedraw.current) {
          needsRedraw.current = false
          const ctx = canvas.getContext('2d')
          ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
          ctx.clearRect(0, 0, stage.width, stage.height)
          ctx.lineCap = 'round'
          ctx.lineJoin = 'round'
          const paint = (stroke) => {
            const points = stroke?.points
            if (!points || points.length < 2) return
            const map = needsStretch(stroke) ? stretch : (value) => value
            ctx.strokeStyle = stroke.colour || INK_COLOUR
            ctx.lineWidth = stroke.width || INK_WIDTH
            ctx.beginPath()
            points.forEach((point, index) => {
              const x = map(point.x) * stage.width
              const y = map(point.y) * stage.height
              if (index === 0) ctx.moveTo(x, y)
              else ctx.lineTo(x, y)
            })
            ctx.stroke()
          }
          savedStrokes.current.forEach(paint)
          liveStrokes.current.forEach(paint)
          paint(activeStroke.current)
        }
      }

      // The dot: a transform on one element, never a React render.
      const dot = dotRef.current
      if (dot) {
        const showDot = pointer.visible && state.mode !== 'IDLE' && stage.width > 0
        dot.style.opacity = showDot ? '1' : '0'
        if (showDot) {
          const x = stage.left + stretch(pointer.x) * stage.width
          const y = stage.top + stretch(pointer.y) * stage.height
          dot.style.transform = `translate3d(${x}px, ${y}px, 0) translate(-50%, -50%)`
        }
      }

      frame = requestAnimationFrame(draw)
    }
    frame = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frame)
  }, [stage, state.mode, pointerRef])

  // --- chrome --------------------------------------------------------------
  // A thin status line, hidden after a few seconds of quiet and brought back by
  // moving the mouse. An audience should see the deck, not the tooling.
  useEffect(() => {
    let timer
    const wake = () => {
      setShowChrome(true)
      clearTimeout(timer)
      timer = setTimeout(() => setShowChrome(false), 2500)
    }
    wake()
    window.addEventListener('mousemove', wake)
    return () => {
      window.removeEventListener('mousemove', wake)
      clearTimeout(timer)
    }
  }, [])

  // Keyboard, in the window that actually has focus.
  //
  // The control window has the same fallback, but this is the window the
  // presenter just dragged to the projector and clicked on - so this is where
  // their key presses land. Sending them through `engineApi.command` rather than
  // moving the slide locally is the point: the keyboard is a fourth modality
  // into the *same* dispatcher, so it lands in the same history and the same
  // analytics as a gesture, and the control window stays in step.
  //
  // Esc closes the window. `window.close()` only works on a window that script
  // opened, which this one always is - the session screen opens it.
  useEffect(() => {
    const map = {
      ArrowRight: 'NEXT_SLIDE',
      ArrowLeft: 'PREVIOUS_SLIDE',
      PageDown: 'NEXT_SLIDE',
      PageUp: 'PREVIOUS_SLIDE',
      Home: 'FIRST_SLIDE',
      End: 'LAST_SLIDE',
      ' ': 'NEXT_SLIDE',
      p: 'VIRTUAL_POINTER',
      a: 'ANNOTATION_MODE',
      e: 'CLEAR_ANNOTATION',
      b: 'BLACKOUT',
      w: 'WHITEOUT',
    }
    const onKey = (event) => {
      if (event.key === 'Escape') {
        window.close()
        return
      }
      // A modifier means the presenter is talking to the browser, not the deck.
      if (event.ctrlKey || event.metaKey || event.altKey) return
      const command = map[event.key]
      if (!command) return
      event.preventDefault()
      engineApi.command(command).catch(() => {
        /* No session, or it belongs to someone else. Silent: an error toast on
           the projector is worse than a key that did nothing. */
      })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const currentSlideUrl = slideUrl(slide)
  // END_PRESENTATION blanks the deck the way leaving a slideshow does, without
  // closing the window: the presenter can bring it back with "Vision start the
  // presentation OK", a gesture-bound command, or the control bar. Closing the
  // window on a possibly-misheard command would not be recoverable from the
  // stage.
  const blank = state.blankScreen || (state.presenting ? null : 'BLACK')

  return (
    <div
      ref={containerRef}
      className="relative h-screen w-screen overflow-hidden bg-black"
      style={{ cursor: showChrome ? 'default' : 'none' }}
    >
      {/* The slide */}
      {currentSlideUrl && !blank && (
        <img
          key={currentSlideUrl}
          src={currentSlideUrl}
          alt={`Slide ${slide}`}
          onLoad={(event) => {
            const { naturalWidth, naturalHeight } = event.currentTarget
            if (naturalWidth && naturalHeight) setAspect(naturalWidth / naturalHeight)
          }}
          onError={() => setLoadError('This slide could not be rendered on the server.')}
          className="absolute select-none"
          style={{
            left: stage.left,
            top: stage.top,
            width: stage.width,
            height: stage.height,
          }}
          draggable={false}
        />
      )}

      {/* Blackout / whiteout - a real slideshow behaviour, and here it is just a div */}
      {blank && (
        <div className={`absolute inset-0 ${blank === 'WHITE' ? 'bg-white' : 'bg-black'}`} />
      )}

      {/* Ink, sized to the slide rather than the window */}
      <canvas
        ref={canvasRef}
        className="pointer-events-none absolute"
        style={{
          left: stage.left,
          top: stage.top,
          width: stage.width,
          height: stage.height,
          opacity: blank ? 0 : 1,
        }}
      />

      {/* The virtual pointer. Positioned by transform in the frame loop above. */}
      <span
        ref={dotRef}
        className="pointer-events-none absolute left-0 top-0 z-10 transition-opacity duration-150"
        style={{ opacity: 0 }}
      >
        <span className="absolute inset-0 -m-4 rounded-full bg-red-500/25 blur-md" />
        <span className="relative block h-4 w-4 rounded-full bg-red-500 ring-2 ring-white/80" />
      </span>

      {/* Status line */}
      <div
        className={`pointer-events-none absolute inset-x-0 bottom-0 flex items-center justify-between px-6 py-4 text-xs text-white/45 transition-opacity duration-500 ${
          showChrome ? 'opacity-100' : 'opacity-0'
        }`}
      >
        <span className="truncate">{presentation?.title || 'VisionX presentation'}</span>
        <span className="flex items-center gap-3">
          {!state.presenting && <span className="text-white/70">Presentation ended</span>}
          {state.mode === 'POINTER' && <span className="text-white/70">Pointer</span>}
          {state.mode === 'ANNOTATE' && <span className="text-white/70">Annotating</span>}
          {!connected && <span className="text-amber-400">Reconnecting…</span>}
          <span>
            {slide}
            {totalSlides ? ` / ${totalSlides}` : ''}
          </span>
        </span>
      </div>

      {/* Only shown when there is genuinely nothing to display */}
      {(loadError || !presentationId) && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black px-8 text-center">
          <p className="text-lg text-white/85">
            {presentationId ? 'This presentation could not be opened' : 'No presentation selected'}
          </p>
          <p className="max-w-md text-sm text-white/50">
            {loadError ||
              'Open a presentation from the VisionX library and click Start Presentation.'}
          </p>
          <p className="text-xs text-white/30">Press Esc to close this window.</p>
        </div>
      )}
    </div>
  )
}
