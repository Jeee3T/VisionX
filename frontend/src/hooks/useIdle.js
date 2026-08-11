import { useEffect, useState } from 'react'

/**
 * True once nothing has happened for `delay` ms. Drives the session screen's
 * auto-hiding chrome: the strip fades, the slide keeps the stage.
 */
export default function useIdle(delay = 3000, activityKey = null) {
  const [idle, setIdle] = useState(false)

  useEffect(() => {
    setIdle(false)
    let timer = setTimeout(() => setIdle(true), delay)

    const wake = () => {
      setIdle(false)
      clearTimeout(timer)
      timer = setTimeout(() => setIdle(true), delay)
    }

    window.addEventListener('mousemove', wake)
    window.addEventListener('keydown', wake)
    window.addEventListener('touchstart', wake)

    return () => {
      clearTimeout(timer)
      window.removeEventListener('mousemove', wake)
      window.removeEventListener('keydown', wake)
      window.removeEventListener('touchstart', wake)
    }
  }, [delay, activityKey])

  return idle
}
