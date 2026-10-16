import { EvidenceReviewPage } from './EvidenceReviewPage'

export function ReviewQueuePage({ kind }: { kind: 'cloaking' | 'evidence' }) {
  return <EvidenceReviewPage kind={kind} />
}
