import { Button as RadixButton } from '@radix-ui/themes'
import type { ComponentProps } from 'react'

export function Button({ className = '', type = 'button', ...props }: ComponentProps<typeof RadixButton>) {
  const danger = className.includes('danger-button')
  const primary = className.includes('primary-button') || className.includes('login-button')
  return <RadixButton type={type} size="2" aria-busy={props.loading || undefined} variant={primary ? 'solid' : 'soft'} color={danger ? 'red' : primary ? 'indigo' : 'gray'} className={className} {...props} />
}
