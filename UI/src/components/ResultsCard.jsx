export default function ResultsCard({ label, value, note, primary = false }) {
  return (
    <article className={`result-card ${primary ? 'primary-result' : ''}`}>
      <p className="result-label">{label}</p>
      <p className="result-value">{value}</p>
      {note && <p className="result-note">{note}</p>}
    </article>
  )
}
