export default function FormField({ id, label, optional = false, hint, error, children }) {
  const describedBy = [hint ? `${id}-hint` : '', error ? `${id}-error` : ''].filter(Boolean).join(' ')
  return (
    <div className={`form-field ${error ? 'has-error' : ''}`}>
      <label htmlFor={id}>
        {label} <span className={optional ? 'optional' : 'required'}>{optional ? '(optional)' : '(required)'}</span>
      </label>
      {hint && <p className="field-hint" id={`${id}-hint`}>{hint}</p>}
      {children({ describedBy: describedBy || undefined, invalid: Boolean(error) })}
      {error && <p className="field-error" id={`${id}-error`} role="alert">{error}</p>}
    </div>
  )
}
