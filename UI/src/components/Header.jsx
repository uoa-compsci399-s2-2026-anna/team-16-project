export default function Header({ onClear, showClear }) {
  return (
    <header className="site-header">
      <div className="header-inner">
        <div className="brand">
          <img src="/kai-commitment-logo.png" alt="Kai Commitment - Leading action on food waste" />
          <span className="prototype-label">Impact calculator</span>
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
