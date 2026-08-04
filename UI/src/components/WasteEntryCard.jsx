export default function WasteEntryCard({ record, index, kilograms, onEdit, onRemove, review = false }) {
  return (
    <article className="waste-card">
      <div className="waste-card-heading">
        <div>
          <p className="eyebrow">Waste record {index + 1}</p>
          <h3>{Number(record.amount).toLocaleString('en-NZ')} {record.unit}</h3>
        </div>
        <div className="card-actions">
          <button className="text-button" type="button" onClick={onEdit}>Edit<span className="sr-only"> waste record {index + 1}</span></button>
          {!review && <button className="text-button danger" type="button" onClick={onRemove}>Remove<span className="sr-only"> waste record {index + 1}</span></button>}
        </div>
      </div>
      <dl className="record-details">
        <div><dt>In kilograms</dt><dd>{kilograms.toLocaleString('en-NZ')} kg</dd></div>
        <div><dt>Destination</dt><dd>{record.destination}</dd></div>
        <div><dt>Food category</dt><dd>{record.foodCategory || 'Not specified'}</dd></div>
      </dl>
    </article>
  )
}
