export default function StepIndicator({ steps, currentStep }) {
  return (
    <nav className="step-indicator" aria-label="Calculator progress">
      <p className="step-mobile">Step {currentStep + 1} of {steps.length}: <strong>{steps[currentStep]}</strong></p>
      <ol>
        {steps.map((step, index) => {
          const state = index < currentStep ? 'complete' : index === currentStep ? 'current' : 'upcoming'
          return (
            <li key={step} className={state} aria-current={index === currentStep ? 'step' : undefined}>
              <span className="step-number" aria-hidden="true">{index < currentStep ? '✓' : index + 1}</span>
              <span>{step}</span>
              <span className="sr-only"> — {state}</span>
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
