import { Theme } from '@radix-ui/themes'
import { useEffect, useState, type PropsWithChildren } from 'react'
import { initialAppearance, THEME_KEY, ThemeContext, type Appearance } from './theme-context'

export function AppearanceProvider({ children }: PropsWithChildren) {
  const [appearance, updateAppearance] = useState(initialAppearance)
  const setAppearance = (value: Appearance) => {
    updateAppearance(value)
    try { localStorage.setItem(THEME_KEY, value) } catch { /* Keep the in-memory preference. */ }
  }
  useEffect(() => {
    document.documentElement.classList.toggle('dark', appearance === 'dark')
    document.documentElement.style.colorScheme = appearance
  }, [appearance])
  useEffect(() => {
    const listener = (event: StorageEvent) => { if (event.key === THEME_KEY) updateAppearance(initialAppearance()) }
    window.addEventListener('storage', listener)
    return () => { window.removeEventListener('storage', listener) }
  }, [])
  return <ThemeContext.Provider value={{ appearance, setAppearance }}>
    <Theme appearance={appearance} accentColor="indigo" grayColor="slate" radius="large" panelBackground="solid">{children}</Theme>
  </ThemeContext.Provider>
}
