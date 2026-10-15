import { Dialog, IconButton } from '@radix-ui/themes'
import { X } from 'lucide-react'
import { useState, type PropsWithChildren } from 'react'

export function AppDialog({ title, children, onClose, busy = false, maxWidth = '640px' }: PropsWithChildren<{ title: string; onClose: () => void; busy?: boolean; maxWidth?: string }>) {
  const [returnFocus] = useState(() => document.activeElement)
  return <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose() }}>
    <Dialog.Content maxWidth={maxWidth} className="app-dialog" aria-describedby={undefined}
      onInteractOutside={(event) => { event.preventDefault() }}
      onEscapeKeyDown={(event) => { if (busy) event.preventDefault() }}
      onCloseAutoFocus={(event) => { event.preventDefault(); if (returnFocus instanceof HTMLElement) returnFocus.focus() }}>
      <div className="flex items-start justify-between gap-4">
        <Dialog.Title as="h2" mb="0">{title}</Dialog.Title>
        <Dialog.Close><IconButton aria-label="Đóng hộp thoại" variant="ghost" color="gray" disabled={busy}><X size={18} /></IconButton></Dialog.Close>
      </div>
      {children}
    </Dialog.Content>
  </Dialog.Root>
}
