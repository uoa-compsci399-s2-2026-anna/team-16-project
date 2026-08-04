import { DEMONSTRATION_NOTICE } from '../data/mockResults.js'

export default function Disclaimer({ compact = false }) {
  return (
    <aside className={`disclaimer ${compact ? 'compact' : ''}`} aria-label="Important information">
      <span className="info-icon" aria-hidden="true">i</span>
      <div>
        <strong>Estimate notice</strong>
        <p>{DEMONSTRATION_NOTICE} Final results will depend on factors supplied and approved by Kai Commitment.</p>
      </div>
    </aside>
  )
}
