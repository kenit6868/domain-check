import { Check, Copy } from 'lucide-react'
import { useState } from 'react'
import { Button } from './Button'
import { toast } from '../../lib/notifications'

export function CopyButton({ value, label = 'Sao chép', iconOnly = false }: { value: string; label?: string; iconOnly?: boolean }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try { await navigator.clipboard.writeText(value) } catch { toast.error('Không thể sao chép. Kiểm tra quyền clipboard của trình duyệt.', { id: 'clipboard' }); return }
    toast.success('Đã sao chép.', { id: 'clipboard' })
    setCopied(true)
    window.setTimeout(() => { setCopied(false) }, 1400)
  }
  return (
    <Button type="button" className={iconOnly ? 'icon-button shrink-0' : 'secondary-button inline-flex items-center gap-2'} aria-label={iconOnly ? (copied ? 'Đã sao chép' : `${label}: ${value}`) : undefined} title={iconOnly ? (copied ? 'Đã sao chép' : label) : undefined} onClick={() => { void copy() }}>
      {copied ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
      {!iconOnly && (copied ? 'Đã sao chép' : label)}
    </Button>
  )
}
