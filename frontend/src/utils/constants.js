import {
  ChevronRight,
  ChevronLeft,
  MousePointer2,
  PenLine,
  Eraser,
  Hand,
  Hash,
  SkipBack,
  SkipForward,
  Play,
  Square,
  Moon,
  Sun,
} from 'lucide-react'

export const COMMANDS = {
  NEXT_SLIDE: { label: 'Next slide', icon: ChevronRight, colour: 'text-emerald-600', field: 'nextSlideGesture' },
  PREVIOUS_SLIDE: { label: 'Previous slide', icon: ChevronLeft, colour: 'text-sky-600', field: 'previousSlideGesture' },
  VIRTUAL_POINTER: { label: 'Virtual pointer', icon: MousePointer2, colour: 'text-violet-600', field: 'pointerGesture' },
  ANNOTATION_MODE: { label: 'Annotation mode', icon: PenLine, colour: 'text-amber-600', field: 'annotationGesture' },
  CLEAR_ANNOTATION: { label: 'Clear annotation', icon: Eraser, colour: 'text-rose-600', field: 'clearGesture' },
  // The escape hatch. Clear erases the ink and leaves the pen armed; this erases
  // it and leaves pen and pointer mode too, so the session is back at its default.
  RESET_ANNOTATION: { label: 'Exit annotation', icon: Hand, colour: 'text-ink-700', field: 'resetGesture' },
}

/**
 * Commands that exist but are not bound to a hand pose - they take a parameter,
 * or are awkward to hold a pose for. Voice and the control bar can issue them.
 */
export const UNBOUND_COMMANDS = {
  GO_TO_SLIDE: { label: 'Go to slide', icon: Hash, colour: 'text-indigo-600' },
  FIRST_SLIDE: { label: 'First slide', icon: SkipBack, colour: 'text-sky-600' },
  LAST_SLIDE: { label: 'Last slide', icon: SkipForward, colour: 'text-sky-600' },
  START_PRESENTATION: { label: 'Start slideshow', icon: Play, colour: 'text-emerald-600' },
  END_PRESENTATION: { label: 'End slideshow', icon: Square, colour: 'text-rose-600' },
  BLACKOUT: { label: 'Black screen', icon: Moon, colour: 'text-ink-700' },
  WHITEOUT: { label: 'White screen', icon: Sun, colour: 'text-amber-500' },
}

/** Every command the dispatcher can run, whatever issued it. */
export const ALL_COMMANDS = { ...COMMANDS, ...UNBOUND_COMMANDS }

export const COMMAND_ORDER = [
  'NEXT_SLIDE',
  'PREVIOUS_SLIDE',
  'VIRTUAL_POINTER',
  'ANNOTATION_MODE',
  'CLEAR_ANNOTATION',
  'RESET_ANNOTATION',
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

/** Where a command came from. All four converge on the same dispatcher. */
export const SOURCE_LABELS = {
  gesture: 'Gesture',
  voice: 'Voice',
  manual: 'Control bar',
  keyboard: 'Keyboard',
}

/** Voice confidence bands (see voice_assistant/intent/intents.py). */
export const VOICE_BANDS = {
  EXECUTE: { tone: 'success', label: 'Executed' },
  CONFIRM: { tone: 'warning', label: 'Confirm?' },
  REJECT: { tone: 'idle', label: 'Ignored' },
}

/** The explicit null classes both trained models can predict. */
export const NULL_GESTURE_CLASS = 'UNKNOWN'
export const NO_COMMAND_INTENT = 'NO_COMMAND'

export const CHART_COLOURS = ['#6366f1', '#8b5cf6', '#0ea5e9', '#f59e0b', '#ef4444', '#10b981']
