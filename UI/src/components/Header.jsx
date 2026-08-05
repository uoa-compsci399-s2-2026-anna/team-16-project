export default function Header({ onHome, onClear, showClear, dark = false }) {
  return (
    <header className={`site-header ${dark ? 'intro-header' : ''}`}>
      <div className="header-inner">
        <button className="brand brand-home-button" type="button" onClick={onHome} aria-label="Return to calculator introduction">
          <img src={dark ? '/kai-commitment-logo-white.webp' : '/kai-commitment-logo.png'} alt="Kai Commitment - Leading action on food waste" />
          <span className="prototype-label">Impact calculator</span>
        </button>
        {showClear && (
          <button className="text-button" type="button" onClick={onClear}>
            Clear all data
          </button>
        )}
      </div>
    </header>
  )
}
