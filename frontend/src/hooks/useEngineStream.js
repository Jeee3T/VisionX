import { useEffect, useRef, useState } from 'react'
import { streamUrl } from '../services/api'

/**
 * Subscribes to the engine's Server-Sent Events channel.
 *
 * The backend rate-limits telemetry to ~12 events/second, so this is a live feed
 * without any frame-rate polling. One connection serves the whole session screen.
 */
export default function useEngineStream(enabled = true) {
  const [telemetry, setTelemetry] = useState(null)
  const [engineState, setEngineState] = useState(null)
  const [lastCommand, setLastCommand] = useState(null)
  const [connected, setConnected] = useState(false)
  const [streamError, setStreamError] = useState(null)
  const sourceRef = useRef(null)

  useEffect(() => {
    if (!enabled) {
      sourceRef.current?.close()
      sourceRef.current = null
      setConnected(false)
      return undefined
    }

    const source = new EventSource(streamUrl('/engine/stream'))
    sourceRef.current = source

    source.onopen = () => {
      setConnected(true)
      setStreamError(null)
    }

    source.onmessage = (event) => {
      let payload
      try {
        payload = JSON.parse(event.data)
      } catch {
        return
      }

      switch (payload.type) {
        case 'telemetry':
          setTelemetry(payload)
          break
        case 'command':
          setLastCommand({ ...payload, receivedAt: Date.now() })
          break
        case 'state':
        case 'camera':
          setEngineState(payload)
          break
        case 'error':
          setStreamError(payload.message)
          setEngineState(payload)
          break
        case 'annotations_cleared':
          setLastCommand({ command: 'CLEAR_ANNOTATION', slide: payload.slide, receivedAt: Date.now() })
          break
        default:
          break
      }
    }

    source.onerror = () => {
      setConnected(false)
      // EventSource reconnects on its own; we only surface the state.
    }

    return () => {
      source.close()
      sourceRef.current = null
      setConnected(false)
    }
  }, [enabled])

  return { telemetry, engineState, lastCommand, connected, streamError }
}
