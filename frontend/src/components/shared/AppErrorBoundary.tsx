import { Component, type PropsWithChildren } from 'react'
import { Button } from './Button'

export class AppErrorBoundary extends Component<PropsWithChildren, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    if (this.state.failed) return <main className="app-surface min-h-screen p-8"><div className="data-panel mx-auto max-w-lg p-7" role="alert">
      <h1 className="text-xl font-semibold">Không thể hiển thị trang</h1>
      <p className="my-4 text-sm text-slate-500">Giao diện gặp lỗi. Tải lại trang để thử khôi phục; thao tác gửi sẽ không được tự động thực hiện lại.</p>
      <Button className="primary-button" onClick={() => { window.location.reload() }}>Tải lại trang</Button>
    </div></main>
    return this.props.children
  }
}
