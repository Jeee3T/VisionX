import { Link } from 'react-router-dom'
import { Hand, MousePointer2, PenLine, ShieldCheck } from 'lucide-react'

const POINTS = [
  { icon: Hand, title: 'Five gestures, zero hardware', text: 'A standard webcam replaces the clicker.' },
  { icon: MousePointer2, title: 'Pointer that follows your hand', text: 'Point at the slide, not at a device.' },
  { icon: PenLine, title: 'Annotate mid-sentence', text: 'Draw on the slide without breaking eye contact.' },
  { icon: ShieldCheck, title: 'False-positive protection', text: 'Confidence gate + debounce before any command fires.' },
]

export default function AuthLayout({ title, subtitle, children, footer }) {
  return (
    <div className="flex min-h-screen">
      {/* Marketing rail - desktop only, the form owns the small screens. */}
      <div className="relative hidden w-1/2 flex-col justify-between overflow-hidden bg-brand-gradient p-12 text-white lg:flex">
        <div className="absolute -right-24 -top-24 h-80 w-80 rounded-full bg-white/10 blur-2xl" />
        <div className="absolute -bottom-32 -left-16 h-96 w-96 rounded-full bg-white/10 blur-3xl" />

        <Link to="/" className="relative flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/15 text-sm font-bold backdrop-blur">
            VX
          </span>
          <span className="text-lg font-semibold">VisionX</span>
        </Link>

        <div className="relative">
          <h2 className="max-w-md text-3xl font-semibold leading-snug">
            Present with your hands. Leave the clicker behind.
          </h2>
          <div className="mt-10 grid gap-5">
            {POINTS.map(({ icon: Icon, title: heading, text }) => (
              <div key={heading} className="flex gap-3.5">
                <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/15">
                  <Icon size={17} />
                </span>
                <div>
                  <p className="text-sm font-semibold">{heading}</p>
                  <p className="text-sm text-white/75">{text}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        <p className="relative text-xs text-white/60">AI-Powered Vision-Based Intelligent Presentation Control System</p>
      </div>

      <div className="flex w-full items-center justify-center px-5 py-10 lg:w-1/2">
        <div className="w-full max-w-sm animate-fade-in">
          <Link to="/" className="mb-8 flex items-center gap-2.5 lg:hidden">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-gradient text-sm font-bold text-white">
              VX
            </span>
            <span className="font-semibold text-ink-900">VisionX</span>
          </Link>

          <h1 className="text-2xl font-semibold text-ink-900">{title}</h1>
          {subtitle && <p className="mt-1.5 text-sm text-ink-500">{subtitle}</p>}

          <div className="mt-7">{children}</div>

          {footer && <div className="mt-6 text-center text-sm text-ink-500">{footer}</div>}
        </div>
      </div>
    </div>
  )
}
