// Whether the console is the owner's (through the tunnel) or the public, read-only demo.
import { createContext, useContext } from 'react'
import type { DemoInfo, DemoRecording } from './api'

export type Mode = {
  demo: boolean
  info: DemoInfo | null
  recording: DemoRecording | null
}

export const ModeContext = createContext<Mode>({ demo: false, info: null, recording: null })

export function useMode(): Mode {
  return useContext(ModeContext)
}

export const DEMO_REASON = 'A recorded evening: only the owner can decide or change anything.'
