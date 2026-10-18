import { Select } from '@radix-ui/themes'
import type { ComponentProps, ReactNode } from 'react'

const EMPTY = '__domaincheck_select_empty__'

type Props = Omit<ComponentProps<typeof Select.Trigger>, 'children' | 'defaultValue' | 'onChange'> & {
  value: string | number
  onValueChange: (value: string) => void
  children: ReactNode
  name?: string
}

export function AppSelect({ value, onValueChange, children, disabled, name, className = '', ...trigger }: Props) {
  return <Select.Root value={String(value) || EMPTY} onValueChange={(next) => { onValueChange(next === EMPTY ? '' : next) }} disabled={disabled} {...(name ? { name } : {})}>
    <Select.Trigger {...trigger} className={`app-select-trigger ${className}`} variant="surface" />
    <Select.Content className="app-select-content" position="popper" sideOffset={6} align="start" collisionPadding={12}>
      {children}
    </Select.Content>
  </Select.Root>
}

export function AppSelectItem({ value, children, ...props }: Omit<ComponentProps<typeof Select.Item>, 'value'> & { value: string | number }) {
  return <Select.Item {...props} value={String(value) || EMPTY} data-value={String(value)}>{children}</Select.Item>
}
