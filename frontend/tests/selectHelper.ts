import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

export async function chooseSelect(trigger: HTMLElement, value: string) {
  await userEvent.click(trigger)
  const option = screen.getAllByRole('option').find((item) => item.getAttribute('data-value') === value)
  if (!option) throw new Error(`Missing select option: ${value}`)
  await userEvent.click(option)
}
