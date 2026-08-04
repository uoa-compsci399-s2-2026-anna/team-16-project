export default function NavigationButtons({ onBack, onContinue, continueLabel = 'Continue', continueDisabled = false, hideBack = false }) {
  return (
    <div className="navigation-buttons">
      {!hideBack && <button className="button button-secondary" type="button" onClick={onBack}>Back</button>}
      <button className="button button-primary" type="button" onClick={onContinue} disabled={continueDisabled}>
        {continueLabel}
      </button>
    </div>
  )
}
