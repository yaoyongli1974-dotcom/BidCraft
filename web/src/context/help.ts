import { createContext } from 'react'

export interface HelpApi {
  openHelp: () => void
}

/** Kept apart from the provider so fast refresh treats it as component-only. */
export const HelpContext = createContext<HelpApi | null>(null)
