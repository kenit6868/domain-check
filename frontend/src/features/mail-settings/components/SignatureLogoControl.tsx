import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Button } from '../../../components/shared/Button'
import { resetProfileLogo, uploadProfileLogo } from '../api/mailSettingsApi'

export function SignatureLogoControl({ artifactId, disabled, onSaved }: { artifactId: string | null; disabled: boolean; onSaved: () => void }) {
  const client = useQueryClient()
  const [preview, setPreview] = useState<string>()
  const [validation, setValidation] = useState('')
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview) }, [preview])
  const saved = () => { onSaved(); setPreview(undefined); void client.invalidateQueries({ queryKey: ['domain-worker'] }) }
  const upload = useMutation({ mutationFn: uploadProfileLogo, meta: { successMessage: 'Đã thay logo chữ ký.' }, onSuccess: saved })
  const reset = useMutation({ mutationFn: resetProfileLogo, meta: { successMessage: 'Đã khôi phục logo mặc định.' }, onSuccess: saved })
  const busy = upload.isPending || reset.isPending
  return <div className="mt-3 flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
    <img src={preview ?? (artifactId ? `/api/v1/artifacts/${artifactId}` : '/api/v1/settings/profile/logo')} alt="Logo chữ ký" width={64} height={64} className="size-16 rounded object-contain" />
    <div className="min-w-0 flex-1"><p className="text-xs font-semibold">Logo chữ ký email</p>
      <p className="mt-1 text-xs text-slate-500">Luôn kèm logo khi soạn/gửi mail. Mặc định dùng logo WIN; ảnh thay thế áp dụng cho hồ sơ của bạn.</p>
      {!disabled && <div className="mt-2 flex flex-wrap items-center gap-2"><label className="field">Thay logo (PNG/JPEG, tối đa 2 MB)
        <input type="file" accept="image/png,image/jpeg" disabled={busy} onChange={(event) => {
          const file = event.target.files?.[0]; event.target.value = ''; if (!file) return
          setValidation('')
          if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 2 * 1024 * 1024) { setValidation('Chọn ảnh PNG/JPEG tối đa 2 MB.'); return }
          setPreview(URL.createObjectURL(file)); upload.mutate(file, { onError: () => { setPreview(undefined) } })
        }} /></label>
        {artifactId && <Button className="secondary-button" disabled={busy} loading={reset.isPending} onClick={() => { reset.mutate() }}>Khôi phục logo mặc định</Button>}
      </div>}
      {upload.isPending && <p className="mt-1 text-xs" role="status">Đang tải logo…</p>}
      {(validation || upload.error || reset.error) && <p role="alert" className="mt-2 text-xs text-rose-600">{validation || upload.error?.message || reset.error?.message}</p>}
    </div>
  </div>
}
