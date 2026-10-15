import { useState } from 'react'
import { AppDialog } from './AppDialog'
import { Button } from './Button'

type Confirmation = { title: string; description: string; label: string; action: () => void }

export function useConfirmation() {
  const [pending, ask] = useState<Confirmation | null>(null)
  const confirmation = pending && <AppDialog title={pending.title} onClose={() => { ask(null) }}>
    <p className="mt-4 text-sm leading-6 text-slate-600 dark:text-slate-300">{pending.description}</p>
    <div className="mt-6 flex justify-end gap-3">
      <Button autoFocus className="secondary-button" onClick={() => { ask(null) }}>Hủy</Button>
      <Button className="danger-button" onClick={() => { ask(null); pending.action() }}>{pending.label}</Button>
    </div>
  </AppDialog>
  return { ask, confirmation }
}
