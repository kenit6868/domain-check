import { Button } from './Button'

export function QueryError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="error-notice flex flex-wrap items-center justify-between gap-3" role="alert"><span>{message}</span>
    <Button className="secondary-button" onClick={onRetry}>Thử tải lại</Button>
  </div>
}
