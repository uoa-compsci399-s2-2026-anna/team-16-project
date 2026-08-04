export default function Header({ onClear, showClear }) {
  return (
    <header className="site-header">
      <div className="header-inner">
        <div className="brand" aria-label="Kai Commitment placeholder brand">
          <span className="brand-mark" aria-hidden="true">KC</span>
          <span>Kai Commitment</span>
          <span className="prototype-label">Prototype</span>
        </div>
        {showClear && (
          <button className="text-button" type="button" onClick={onClear}>
            Clear all data
          </button>
        )}
      </div>
    </header>
  )
}
