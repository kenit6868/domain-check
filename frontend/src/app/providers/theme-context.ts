import { createContext, useContext } from 'react'

export type Appearance = 'light' | 'dark'
export const THEME_KEY = 'domaincheck.appearance'
export const ThemeContext = createContext<{ appearance: Appearance; setAppearance: (value: Appearance) => void }>({ appearance: 'dark', setAppearance: () => undefined })
export const useAppearance = () => useContext(ThemeContext)
export function initialAppearance(): Appearance {
  try {
    const saved = localStorage.getItem(THEME_KEY)
    if (saved === 'light' || saved === 'dark') return saved
  } catch { /* Storage may be unavailable. */ }
  return typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}
