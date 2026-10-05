import { Link } from 'react-router-dom'
import {
  ArrowRight,
  Camera,
  ChevronLeft,
  ChevronRight,
  Eraser,
  Gauge,
  MousePointer2,
  PenLine,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { useAuth } from '../context/AuthContext'

const GESTURES = [
  { icon: ChevronRight, title: 'Next slide', text: 'Move forward without touching anything.' },
  { icon: ChevronLeft, title: 'Previous slide', text: 'Step back to answer a question.' },
  { icon: MousePointer2, title: 'Virtual pointer', text: 'Your fingertip becomes the laser pointer.' },
  { icon: PenLine, title: 'Annotation mode', text: 'Draw on the slide mid-sentence.' },
  { icon: Eraser, title: 'Clear annotation', text: 'Wipe the slide clean and carry on.' },
]

const PIPELINE = [
  { title: 'Capture', text: 'OpenCV pulls frames from a standard webcam.' },
  { title: 'Detect', text: 'MediaPipe extracts 21 hand landmarks per frame.' },
  { title: 'Recognise', text: 'Finger geometry produces a pose and a confidence score.' },
  { title: 'Filter', text: 'Confidence gate + debounce + neutral state block false positives.' },
  { title: 'Execute', text: 'The dispatcher drives the presentation VisionX is showing.' },
]

export default function Landing() {
  const { isAuthenticated } = useAuth()

  return (
    <div className="min-h-screen bg-white">
      <header className="sticky top-0 z-40 border-b border-ink-200/60 bg-white/80 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-5">
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-gradient text-sm font-bold text-white">
              VX
            </span>
            <span className="font-semibold text-ink-900">VisionX</span>
          </div>
          <div className="flex items-center gap-2">
            {isAuthenticated ? (
              <Link to="/dashboard" className="btn-primary">
                Open dashboard <ArrowRight size={16} />
              </Link>
            ) : (
              <>
                <Link to="/login" className="btn-ghost">
                  Sign in
                </Link>
                <Link to="/register" className="btn-primary">
                  Get started
                </Link>
              </>
            )}
          </div>
        </div>
      </header>

      {/* Hero */}
      <section className="relative overflow-hidden bg-brand-soft">
        <div className="absolute -right-32 top-10 h-72 w-72 rounded-full bg-brand-200/50 blur-3xl" />
        <div className="mx-auto grid max-w-6xl gap-12 px-5 py-20 lg:grid-cols-2 lg:items-center lg:py-28">
          <div className="relative animate-fade-in">
            <span className="chip bg-white text-brand-700 shadow-card">
              <Sparkles size={13} /> AI-powered presentation control
            </span>
            <h1 className="mt-5 text-4xl font-semibold leading-tight tracking-tight text-ink-900 md:text-5xl">
              Control your slides with your <span className="bg-brand-gradient bg-clip-text text-transparent">hands</span>.
            </h1>
            <p className="mt-5 max-w-lg text-base leading-relaxed text-ink-600">
              VisionX turns a standard webcam into a contactless presentation remote. No clicker, no sensor
              glove, no depth camera — just computer vision, a confidence-gated gesture pipeline, and your slides.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link to={isAuthenticated ? '/dashboard' : '/register'} className="btn-primary px-5 py-3">
                {isAuthenticated ? 'Go to dashboard' : 'Create your account'} <ArrowRight size={17} />
              </Link>
              <Link to="/login" className="btn-secondary px-5 py-3">
                I already have one
              </Link>
            </div>
            <div className="mt-8 flex flex-wrap gap-x-6 gap-y-2 text-xs text-ink-500">
              <span className="inline-flex items-center gap-1.5">
                <Camera size={14} className="text-brand-500" /> Standard webcam
              </span>
              <span className="inline-flex items-center gap-1.5">
                <Gauge size={14} className="text-brand-500" /> Real-time, on-device
              </span>
              <span className="inline-flex items-center gap-1.5">
                <ShieldCheck size={14} className="text-brand-500" /> False-positive protection
              </span>
            </div>
          </div>

          <div className="relative animate-fade-in">
            <div className="card overflow-hidden p-0 shadow-lift">
              <div className="flex items-center gap-2 border-b border-ink-200/70 bg-white px-4 py-3">
                <span className="h-2.5 w-2.5 rounded-full bg-red-400" />
                <span className="h-2.5 w-2.5 rounded-full bg-amber-400" />
                <span className="h-2.5 w-2.5 rounded-full bg-emerald-400" />
                <span className="ml-2 text-xs text-ink-400">Presentation session</span>
              </div>
              <div className="relative aspect-video bg-gradient-to-br from-ink-900 via-ink-800 to-ink-900 p-6">
                <div className="flex h-full flex-col justify-center">
                  <p className="text-xs uppercase tracking-[0.2em] text-white/40">Slide 4 of 18</p>
                  <p className="mt-3 text-2xl font-semibold text-white/90">Results &amp; Discussion</p>
                  <div className="mt-4 h-1 w-24 rounded bg-brand-500" />
                </div>
                <span className="absolute left-[62%] top-[46%]">
                  <span className="absolute inset-0 -m-3 rounded-full bg-red-500/30 blur-md" />
                  <span className="relative block h-3.5 w-3.5 rounded-full bg-red-500 ring-2 ring-white/70" />
                </span>
                <div className="absolute bottom-4 left-4 right-4 flex items-center gap-3 rounded-xl bg-black/55 px-3 py-2 text-[11px] text-white/85 backdrop-blur">
                  <span className="flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-emerald-400" /> Live
                  </span>
                  <span className="h-3 w-px bg-white/20" />
                  <span>pinky up</span>
                  <span className="h-3 w-px bg-white/20" />
                  <span className="text-emerald-300">Next slide</span>
                  <span className="ml-auto tabular-nums text-white/60">04:12</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Gestures */}
      <section className="mx-auto max-w-6xl px-5 py-20">
        <h2 className="text-2xl font-semibold text-ink-900">Five gestures. That is the whole interface.</h2>
        <p className="mt-2 max-w-2xl text-sm text-ink-500">
          Each command is bound to a hand pose you choose. Change the binding in Gesture Settings and it applies
          to your next session.
        </p>
        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {GESTURES.map(({ icon: Icon, title, text }) => (
            <div key={title} className="card p-5 transition-shadow hover:shadow-lift">
              <span className="inline-flex rounded-xl bg-brand-50 p-2.5 text-brand-600">
                <Icon size={19} />
              </span>
              <h3 className="mt-4 text-sm font-semibold text-ink-900">{title}</h3>
              <p className="mt-1 text-xs leading-relaxed text-ink-500">{text}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Pipeline */}
      <section className="border-y border-ink-200/70 bg-ink-50/60">
        <div className="mx-auto max-w-6xl px-5 py-20">
          <h2 className="text-2xl font-semibold text-ink-900">What happens between your hand and the slide</h2>
          <div className="mt-8 grid gap-4 md:grid-cols-5">
            {PIPELINE.map((step, index) => (
              <div key={step.title} className="card relative p-5">
                <span className="text-xs font-semibold text-brand-500">0{index + 1}</span>
                <h3 className="mt-2 text-sm font-semibold text-ink-900">{step.title}</h3>
                <p className="mt-1 text-xs leading-relaxed text-ink-500">{step.text}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-5 py-20">
        <div className="card flex flex-col items-center gap-5 bg-brand-gradient p-12 text-center text-white shadow-lift">
          <h2 className="max-w-xl text-2xl font-semibold">Your next talk does not need a clicker.</h2>
          <Link to={isAuthenticated ? '/dashboard' : '/register'} className="btn bg-white px-6 py-3 text-brand-700 hover:bg-white/90">
            {isAuthenticated ? 'Open dashboard' : 'Start free'} <ArrowRight size={17} />
          </Link>
        </div>
      </section>

      <footer className="border-t border-ink-200/70 py-8">
        <p className="text-center text-xs text-ink-400">
          VisionX — AI-Powered Vision-Based Intelligent Presentation Control System
        </p>
      </footer>
    </div>
  )
}
