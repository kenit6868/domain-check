const states: Record<string, { label: string; tone: string }> = {
  active: { label: 'Đang hoạt động', tone: 'active' },
  pending: { label: 'Chờ kích hoạt', tone: 'pending' },
  disabled: { label: 'Đã khóa', tone: 'disabled' },
  queued: { label: 'Đang chờ', tone: 'pending' },
  running: { label: 'Đang chạy', tone: 'info' },
  checking: { label: 'Đang kiểm tra', tone: 'info' },
  sending: { label: 'Đang gửi', tone: 'info' },
  send_queued: { label: 'Chờ gửi', tone: 'pending' },
  stopping: { label: 'Đang dừng', tone: 'pending' },
  stopped: { label: 'Đã dừng', tone: 'disabled' },
  paused: { label: 'Tạm dừng', tone: 'pending' },
  waiting_for_session: { label: 'Chờ phiên kết nối', tone: 'pending' },
  stale: { label: 'Cần kiểm tra heartbeat', tone: 'pending' },
  success: { label: 'Thành công', tone: 'active' },
  succeeded: { label: 'Thành công', tone: 'active' },
  completed: { label: 'Hoàn tất', tone: 'active' },
  accepted: { label: 'Đã nhận yêu cầu', tone: 'info' },
  sent: { label: 'Đã gửi', tone: 'active' },
  submitted: { label: 'Đã tiếp nhận', tone: 'active' },
  failed: { label: 'Thất bại', tone: 'error' },
  denied: { label: 'Bị từ chối', tone: 'error' },
  interrupted: { label: 'Bị gián đoạn', tone: 'pending' },
  partial: { label: 'Hoàn tất một phần', tone: 'pending' },
  unknown: { label: 'Chưa rõ kết quả', tone: 'pending' },
}

export function StatusBadge({ value, label }: { value: string; label?: string }) {
  const state = states[value.toLowerCase()]
  return <span className={`status status-badge status-${state?.tone ?? 'disabled'}`} title={value}>
    <i aria-hidden="true" className="status-badge-dot" />{label ?? state?.label ?? value}
  </span>
}
