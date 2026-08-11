import { useState } from 'react'
import { Camera, CameraOff, ChevronDown, ChevronUp } from 'lucide-react'
import { streamUrl } from '../../services/api'

/**
 * Small corner thumbnail: the presenter watches the slide, not themselves.
 * Collapsible, because during a real talk even this can be too much.
 */
export default function CameraPreview({ running, hidden }) {
  const [collapsed, setCollapsed] = useState(false)
  const [failed, setFailed] = useState(false)

  return (
    <div
      className={`overflow-hidden rounded-2xl border border-white/10 bg-ink-900/85 shadow-lift backdrop-blur transition-all duration-500 ${
        hidden ? 'opacity-25 hover:opacity-100' : 'opacity-100'
      }`}
    >
      <button
        onClick={() => setCollapsed((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2 text-xs text-white/70 hover:text-white"
      >
        <span className="flex items-center gap-1.5">
          {running && !failed ? <Camera size={13} /> : <CameraOff size={13} />} Camera
        </span>
        {collapsed ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
      </button>

      {!collapsed && (
        <div className="w-56 border-t border-white/10 bg-black/40">
          {running && !failed ? (
            <img
              src={streamUrl('/engine/preview')}
              alt="Camera preview"
              className="block w-full"
              onError={() => setFailed(true)}
            />
          ) : (
            <p className="px-3 py-6 text-center text-[11px] leading-relaxed text-white/45">
              {failed ? 'Preview stream unavailable.' : 'Camera is not running.'}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
