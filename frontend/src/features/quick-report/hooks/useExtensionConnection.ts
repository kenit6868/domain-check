import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from '../../../lib/notifications'

import { pingExtension, type ExtensionConnection } from '../extension/extensionBridge'

export function useExtensionConnection(enabled: boolean) {
  const [connection, setConnection] = useState<ExtensionConnection | null>(null)
  const [state, setState] = useState<'checking' | 'connected' | 'disconnected'>(enabled ? 'checking' : 'disconnected')
  const [message, setMessage] = useState('')
  const sequence = useRef(0)
  const refresh = useCallback(async () => {
    if (!enabled) return
    const current = ++sequence.current
    setState('checking')
    setMessage('')
    try {
      const next = await pingExtension()
      if (current !== sequence.current) return
      setConnection(next)
      setState('connected')
      setMessage('')
      toast.success('Đã kết nối Web Form Assistant.', { id: 'extension-connection' })
    } catch (error) {
      if (current !== sequence.current) return
      setConnection(null)
      setState('disconnected')
      setMessage(error instanceof Error ? error.message : 'Không kết nối được extension.')
      toast.error(error instanceof Error ? error.message : 'Không kết nối được extension.', { id: 'extension-connection', duration: 7_000 })
    }
  }, [enabled])
  useEffect(() => {
    if (!enabled) return
    let active = true
    const current = ++sequence.current
    void pingExtension().then((next) => {
      if (!active || current !== sequence.current) return
      setConnection(next)
      setState('connected')
      setMessage('')
    }).catch((error: unknown) => {
      if (!active || current !== sequence.current) return
      setConnection(null)
      setState('disconnected')
      setMessage(error instanceof Error ? error.message : 'Không kết nối được extension.')
    })
    return () => { active = false }
  }, [enabled])
  return { connection, state, message, refresh }
}
