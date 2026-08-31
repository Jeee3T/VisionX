import { useCallback, useEffect, useRef, useState } from 'react'
import { Hand, MonitorPlay, Sun } from 'lucide-react'
import { API_BASE, getToken } from '../../services/api'

const MARGIN = 0.15 // must match PointerController - the hand's comfortable reach

const stretch = (value) => Math.min(1, Math.max(0, (value - MARGIN) / (1 - 2 * MARGIN)))

// A stroke's points are in one of two spaces, and they must not be conflated:
//
//   'camera'  the fingertip over the camera frame - inset by MARGIN, so it is
//             stretched back over the slide when drawn.
//   'slide'   already over the slide - a mouse or touch stroke. Stretching it
//             moves it away from where it was drawn.
//
// The space is stored on the stroke. This used to be inferred from a `source`
// field that `annotation_service` strips before writing, so the check was dead
// and every mouse stroke came back stretched - i.e. in the wrong place.
const needsStretch = (stroke) => (stroke?.space || 'camera') === 'camera'

/**
 * The stage owns ~90% of the viewport. Annotations and the pointer render
 * directly on the slide canvas - never in a side panel.
 */
export default function SlideStage({
  presentation,
  slide,
  mode,
  pointer,
  savedStrokes = [],
  onStrokeComplete,
  hint,
}) {
  const wrapperRef = useRef(null)
  const canvasRef = useRef(null)
  const liveStroke = useRef([])
  const mouseStroke = useRef([])
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [imageFailed, setImageFailed] = useState(false)
  const [, forceRedraw] = useState(0)

  const hasThumbnail =
    presentation && (presentation.thumbnails || []).length >= slide && !imageFailed

  // --- canvas sizing -------------------------------------------------------
  useEffect(() => {
    const element = wrapperRef.current
    if (!element) return undefined
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height })
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  // --- drawing -------------------------------------------------------------
  const redraw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas || !size.width || !size.height) return
    const ratio = window.devicePixelRatio || 1
    canvas.width = size.width * ratio
    canvas.height = size.height * ratio
    const ctx = canvas.getContext('2d')
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
    ctx.clearRect(0, 0, size.width, size.height)
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'

    const drawStroke = (points, colour, width, useStretch) => {
      if (!points || points.length < 2) return
      ctx.strokeStyle = colour || '#ef4444'
      ctx.lineWidth = width || 4
      ctx.beginPath()
      points.forEach((point, index) => {
        const x = (useStretch ? stretch(point.x) : point.x) * size.width
        const y = (useStretch ? stretch(point.y) : point.y) * size.height
        if (index === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      })
      ctx.stroke()
    }

    savedStrokes.forEach((stroke) => {
      const data = stroke.annotationData || stroke
      drawStroke(data.points, data.colour, data.width, needsStretch(data))
    })
    drawStroke(liveStroke.current, '#ef4444', 4, true)    // fingertip: camera space
    drawStroke(mouseStroke.current, '#ef4444', 4, false)  // cursor: slide space
  }, [savedStrokes, size])

  useEffect(() => {
    redraw()
  }, [redraw])

  // --- live gesture stroke -------------------------------------------------
  useEffect(() => {
    if (mode !== 'ANNOTATE' || !pointer) {
      if (liveStroke.current.length) {
        liveStroke.current = []
        redraw()
      }
      return
    }
    const last = liveStroke.current[liveStroke.current.length - 1]
    if (!last || Math.abs(last.x - pointer.x) > 0.004 || Math.abs(last.y - pointer.y) > 0.004) {
      liveStroke.current = [...liveStroke.current, pointer].slice(-600)
      redraw()
    }
  }, [pointer, mode, redraw])

  // Clear the live overlay when the slide changes - saved strokes come from the API.
  useEffect(() => {
    liveStroke.current = []
    mouseStroke.current = []
    redraw()
  }, [slide, redraw])

  // --- mouse / touch drawing ----------------------------------------------
  const pointFromEvent = (event) => {
    const rect = wrapperRef.current.getBoundingClientRect()
    const source = event.touches?.[0] || event
    return {
      x: (source.clientX - rect.left) / rect.width,
      y: (source.clientY - rect.top) / rect.height,
    }
  }

  const startMouseStroke = (event) => {
    if (mode !== 'ANNOTATE') return
    mouseStroke.current = [pointFromEvent(event)]
    forceRedraw((n) => n + 1)
  }

  const extendMouseStroke = (event) => {
    if (mode !== 'ANNOTATE' || !mouseStroke.current.length) return
    mouseStroke.current = [...mouseStroke.current, pointFromEvent(event)]
    redraw()
  }

  const finishMouseStroke = () => {
    const points = mouseStroke.current
    mouseStroke.current = []
    if (points.length > 1 && onStrokeComplete) {
      // `space`, not `source`: the backend stores this one, and it is what
      // decides whether the stroke is stretched when it is replayed. A cursor
      // stroke is already in the slide's own coordinates.
      onStrokeComplete({ points, colour: '#ef4444', width: 4, space: 'slide' })
    }
    redraw()
  }

  const slideUrl =
    presentation && hasThumbnail
      ? `${API_BASE}/presentations/${presentation.id}/slides/${slide}?token=${getToken()}`
      : null

  return (
    <div
      ref={wrapperRef}
      onMouseDown={startMouseStroke}
      onMouseMove={extendMouseStroke}
      onMouseUp={finishMouseStroke}
      onMouseLeave={finishMouseStroke}
      onTouchStart={startMouseStroke}
      onTouchMove={extendMouseStroke}
      onTouchEnd={finishMouseStroke}
      className={`relative h-full w-full overflow-hidden rounded-2xl bg-ink-900 ${
        mode === 'ANNOTATE' ? 'cursor-crosshair' : 'cursor-default'
      }`}
    >
      {slideUrl ? (
        <img
          src={slideUrl}
          alt={`Slide ${slide}`}
          onError={() => setImageFailed(true)}
          className="h-full w-full object-contain"
        />
      ) : (
        <div className="flex h-full w-full flex-col items-center justify-center gap-4 bg-gradient-to-br from-ink-900 via-ink-800 to-ink-900 text-center">
          <MonitorPlay size={38} className="text-white/25" />
          <div>
            <p className="text-5xl font-semibold tracking-tight text-white/85">{slide}</p>
            <p className="mt-2 max-w-sm text-sm text-white/45">
              {presentation
                ? 'This slide could not be previewed here. The presentation window is what your audience sees.'
                : 'Free session - no deck is bound to this session.'}
            </p>
          </div>
        </div>
      )}

      <canvas ref={canvasRef} className="pointer-events-none absolute inset-0 h-full w-full" />

      {/* Virtual pointer dot */}
      {pointer && (mode === 'POINTER' || mode === 'ANNOTATE') && (
        <span
          className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-1/2"
          style={{ left: `${stretch(pointer.x) * 100}%`, top: `${stretch(pointer.y) * 100}%` }}
        >
          <span className="absolute inset-0 -m-3 rounded-full bg-red-500/30 blur-md" />
          <span className="relative block h-3.5 w-3.5 rounded-full bg-red-500 ring-2 ring-white/70" />
        </span>
      )}

      {/* Gentle, non-nagging hints */}
      {hint && (
        <div className="pointer-events-none absolute inset-x-0 bottom-6 flex justify-center">
          <span className="animate-fade-in inline-flex items-center gap-2 rounded-full bg-black/55 px-4 py-2 text-xs text-white/85 backdrop-blur">
            {hint.type === 'light' ? <Sun size={14} /> : <Hand size={14} />}
            {hint.message}
          </span>
        </div>
      )}
    </div>
  )
}
