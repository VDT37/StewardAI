import { useCallback, useEffect, useState } from 'react'

export type OnError = (error: unknown) => void

// Loads once, then every few seconds. load must be stable (module level or memoised).
export function useLoad<T>(load: () => Promise<T>, onError: OnError, everyMs = 4000) {
  const [data, setData] = useState<T | null>(null)
  const reload = useCallback(() => {
    load().then(setData, onError)
  }, [load, onError])
  useEffect(() => {
    reload()
    const timer = setInterval(reload, everyMs)
    return () => clearInterval(timer)
  }, [reload, everyMs])
  return [data, reload] as const
}
