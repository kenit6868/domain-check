import { Theme } from '@radix-ui/themes'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { AppSelect, AppSelectItem } from '../src/components/shared/AppSelect'
import { chooseSelect } from './selectHelper'

afterEach(cleanup)
function Fixture() {
  const [value, setValue] = useState('')
  return <Theme><label>Workflow<AppSelect value={value} onValueChange={setValue}>
    <AppSelectItem value="">Tất cả</AppSelectItem><AppSelectItem value="worker">Domain Worker</AppSelectItem>
    <AppSelectItem value="locked" disabled>Không được chọn</AppSelectItem>
  </AppSelect></label><output aria-label="Giá trị">{value || 'empty'}</output></Theme>
}
it('preserves empty filter values and selects through the themed listbox', async () => {
  render(<Fixture />)
  const trigger = screen.getByRole('combobox', { name: 'Workflow' })
  await chooseSelect(trigger, 'worker')
  expect(screen.getByLabelText('Giá trị')).toHaveTextContent('worker')
  expect(trigger).toHaveTextContent('Domain Worker')
  await chooseSelect(trigger, '')
  expect(screen.getByLabelText('Giá trị')).toHaveTextContent('empty')
})
it('supports keyboard opening, selection, Escape and focus return', async () => {
  render(<Fixture />)
  const trigger = screen.getByRole('combobox', { name: 'Workflow' })
  trigger.focus()
  await userEvent.keyboard('{ArrowDown}')
  expect(screen.getByRole('listbox')).toBeVisible()
  await userEvent.keyboard('{End}{Enter}')
  expect(trigger).toHaveTextContent('Domain Worker')
  await userEvent.keyboard('{ArrowDown}{Escape}')
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  expect(trigger).toHaveFocus()
})
it('does not open disabled controls or call their handlers', async () => {
  const change = vi.fn()
  render(<Theme><AppSelect aria-label="Disabled" value="a" onValueChange={change} disabled><AppSelectItem value="a">A</AppSelectItem></AppSelect></Theme>)
  await userEvent.click(screen.getByRole('combobox'))
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  expect(change).not.toHaveBeenCalled()
})
