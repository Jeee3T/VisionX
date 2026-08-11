import {
  ChevronRight,
  ChevronLeft,
  MousePointer2,
  PenLine,
  Eraser,
} from 'lucide-react'

export const COMMANDS = {
  NEXT_SLIDE: { label: 'Next slide', icon: ChevronRight, colour: 'text-emerald-600', field: 'nextSlideGesture' },
  PREVIOUS_SLIDE: { label: 'Previous slide', icon: ChevronLeft, colour: 'text-sky-600', field: 'previousSlideGesture' },
  VIRTUAL_POINTER: { label: 'Virtual pointer', icon: MousePointer2, colour: 'text-violet-600', field: 'pointerGesture' },
  ANNOTATION_MODE: { label: 'Annotation mode', icon: PenLine, colour: 'text-amber-600', field: 'annotationGesture' },
  CLEAR_ANNOTATION: { label: 'Clear annotation', icon: Eraser, colour: 'text-rose-600', field: 'clearGesture' },
}

export const COMMAND_ORDER = [
  'NEXT_SLIDE',
  'PREVIOUS_SLIDE',
  'VIRTUAL_POINTER',
  'ANNOTATION_MODE',
  'CLEAR_ANNOTATION',
]

/** Engine status -> how the session strip should read. */
export const STATUS_TONE = {
  EXECUTED: { tone: 'success', text: 'Command sent' },
  HOLDING: { tone: 'holding', text: 'Hold the gesture' },
  WAIT_NEUTRAL: { tone: 'warning', text: 'Lower your hand to repeat' },
  COOLDOWN: { tone: 'warning', text: 'Cooling down' },
  LOW_CONFIDENCE: { tone: 'warning', text: 'Gesture unclear' },
  UNMAPPED: { tone: 'idle', text: 'Pose not assigned' },
  IDLE: { tone: 'idle', text: 'Waiting for a hand' },
}

export const CHART_COLOURS = ['#6366f1', '#8b5cf6', '#0ea5e9', '#f59e0b', '#ef4444', '#10b981']
