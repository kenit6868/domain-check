import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AppearanceProvider } from '../src/app/providers/AppearanceProvider'
import { initialAppearance, THEME_KEY } from '../src/app/providers/theme-context'
import { AppDialog } from '../src/components/shared/AppDialog'
import { Button } from '../src/components/shared/Button'
import { ThemeToggle } from '../src/components/shared/ThemeToggle'

afterEach(() => { cleanup(); localStorage.clear(); document.documentElement.classList.remove('dark'); vi.restoreAllMocks() })

describe('appearance and dialog behavior', () => {
  it('restores a saved appearance and persists a manual change across remounts', async () => {
    localStorage.setItem(THEME_KEY, 'dark')
    const view = render(<AppearanceProvider><ThemeToggle /></AppearanceProvider>)
    expect(document.documentElement).toHaveClass('dark')
    await userEvent.click(screen.getByRole('radio', { name: 'Sáng' }))
    expect(localStorage.getItem(THEME_KEY)).toBe('light')
    expect(document.documentElement).not.toHaveClass('dark')
    view.unmount()
    render(<AppearanceProvider><ThemeToggle /></AppearanceProvider>)
    expect(screen.getByRole('radio', { name: 'Sáng' })).toBeChecked()
  })

  it('falls back safely when browser storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('storage blocked') })
    expect(['light', 'dark']).toContain(initialAppearance())
  })

  it('traps keyboard focus and restores the opener after Escape', async () => {
    function Fixture() {
      const [open, setOpen] = useState(false)
      return <AppearanceProvider><Button onClick={() => { setOpen(true) }}>Open editor</Button>
        {open && <AppDialog title="Editor" onClose={() => { setOpen(false) }}>
          <label className="field">Name<input /></label><Button onClick={() => { setOpen(false) }}>Cancel</Button>
        </AppDialog>}
      </AppearanceProvider>
    }
    render(<Fixture />)
    const opener = screen.getByRole('button', { name: 'Open editor' })
    await userEvent.click(opener)
    const dialog = screen.getByRole('dialog', { name: 'Editor' })
    expect(dialog).toContainElement(document.activeElement as HTMLElement)
    await userEvent.tab({ shift: true })
    expect(dialog).toContainElement(document.activeElement as HTMLElement)
    await userEvent.keyboard('{Escape}')
    await waitFor(() => { expect(screen.queryByRole('dialog')).not.toBeInTheDocument() })
    expect(opener).toHaveFocus()
  })
})
