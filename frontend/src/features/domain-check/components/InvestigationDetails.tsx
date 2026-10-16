import { Globe, ShieldCheck, Server, ScanSearch } from 'lucide-react'
import type { ReactNode } from 'react'

const labels: Record<string, string> = {
  ip: 'Địa chỉ IP', issuer: 'Tổ chức cấp', serial: 'Serial', not_after: 'Hết hạn', not_before: 'Có hiệu lực từ',
  registrar: 'Nhà đăng ký', emails: 'Email liên hệ', status: 'Trạng thái', name_servers: 'Nameserver',
  creation_date: 'Ngày đăng ký', expiration_date: 'Ngày hết hạn', source: 'Nguồn', registry: 'Registry',
  report_webform: 'Form báo cáo', abuse_email: 'Email abuse', scheme: 'Giao thức', page_title: 'Tiêu đề trang',
  status_code: 'HTTP status', redirected: 'Chuyển hướng', form_count: 'Số form', has_password_input: 'Ô mật khẩu',
  records: 'Bản ghi', providers: 'Nhà cung cấp', verdict: 'Kết luận', score: 'Điểm', label: 'Đánh giá',
  reasons: 'Lý do', malicious: 'Malicious', suspicious: 'Suspicious', harmless: 'Harmless', total_engines: 'Tổng engine',
  flagged: 'Có cảnh báo', link: 'Link kết quả', note: 'Ghi chú', ca: 'CA', can_revoke: 'Hỗ trợ thu hồi',
  engine: 'Engine', signals: 'Tín hiệu', manual_review_required: 'Cần duyệt thủ công', observed_at: 'Thời điểm kiểm tra',
  profiles_available: 'Profile khả dụng', profiles_failed: 'Profile lỗi', target_url: 'URL kiểm tra',
}
function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}
function valueText(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Chưa có dữ liệu'
  if (typeof value === 'boolean') return value ? 'Có' : 'Không'
  return typeof value === 'string' || typeof value === 'number' ? String(value) : 'Chưa có dữ liệu'
}
function DataValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) return value.length
    ? <ul className="inspection-list">{(value as unknown[]).map((item, index) => <li key={index}><DataValue value={item} /></li>)}</ul>
    : <span className="inspection-muted">Không có dữ liệu</span>
  if (isRecord(value)) return Object.keys(value).length ? <dl className="inspection-fields">{Object.entries(value).map(([key, item]) =>
    <div className="inspection-row" key={key}><dt>{labels[key] ?? key.replaceAll('_', ' ')}</dt><dd><DataValue value={item} /></dd></div>)}</dl>
    : <span className="inspection-muted">Chưa có dữ liệu</span>
  return <span className="whitespace-pre-wrap break-words">{valueText(value)}</span>
}
function Group({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return <section className="inspection-group" aria-label={title}><h4>{icon}{title}</h4>{children}</section>
}
function Entry({ label, value }: { label: string; value: unknown }) {
  return <div className="inspection-entry"><h5>{label}</h5><DataValue value={value} /></div>
}
export function InvestigationDetails({ result }: { result: Record<string, unknown> }) {
  const cloaking = isRecord(result.cloaking) ? result.cloaking : {}
  const summary = Object.fromEntries(['verdict', 'score', 'engine', 'profiles_available', 'profiles_failed', 'manual_review_required']
    .filter((key) => key in cloaking).map((key) => [key, cloaking[key]]))
  return <div className="mt-3"><h3 className="mb-2 text-xs font-semibold">Thông số kiểm tra</h3>
    <div className="inspection-grid">
      <Group title="Domain & liên hệ" icon={<Globe size={15} />}>
        <Entry label="WHOIS" value={result.whois} />
        {result.domain_age_days !== undefined && <Entry label="Tuổi domain (ngày)" value={result.domain_age_days} />}
        {result.registrar_abuse_email_used !== undefined && <Entry label="Email registrar đã chọn" value={result.registrar_abuse_email_used} />}
        <Entry label="Liên hệ registry" value={result.registry_contact} />
      </Group>
      <Group title="Hạ tầng & website" icon={<Server size={15} />}>
        <Entry label="SSL / chứng chỉ" value={result.cert} /><Entry label="HTTP" value={result.http_check} />
        <Entry label="CDN" value={result.cdn_detected} /><Entry label="MX" value={result.mx_records} />
      </Group>
      <Group title="Đánh giá & bảo mật" icon={<ShieldCheck size={15} />}>
        <Entry label="VirusTotal" value={result.virustotal} /><Entry label="Google Safe Browsing" value={result.safebrowsing} />
        <Entry label="Reputation" value={result.reputation} /><Entry label="Ghi chú CA" value={result.ca_note} />
      </Group>
      <Group title="Cloaking & quan sát" icon={<ScanSearch size={15} />}>
        <Entry label="Cloaking HTTP" value={summary} />
        <Entry label="IP quan sát" value={result.origin_ip_scan} /><Entry label="WHOIS IP" value={result.origin_ip_whois} />
        {Object.keys(cloaking).length > 0 && <details className="inspection-technical"><summary>Chi tiết profile & tín hiệu</summary><DataValue value={cloaking} /></details>}
      </Group>
    </div>
    <details className="inspection-technical mt-2"><summary>Dữ liệu kỹ thuật đầy đủ</summary>
      <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-all text-[11px]">{JSON.stringify(result, null, 2)}</pre>
    </details>
  </div>
}
