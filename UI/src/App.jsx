import { useMemo, useState } from 'react'
import Header from './components/Header.jsx'
import StepIndicator from './components/StepIndicator.jsx'
import SupplyChainStageCards from './components/SupplyChainStageCards.jsx'
import ResultsCard from './components/ResultsCard.jsx'
import Disclaimer from './components/Disclaimer.jsx'
import NavigationButtons from './components/NavigationButtons.jsx'
import BreakdownTabs from './components/BreakdownTabs.jsx'
import { FOOD_CATEGORIES, UNITS, WASTE_DESTINATIONS } from './data/options.js'
import { DEMONSTRATION_NOTICE, MOCK_RESULTS } from './data/mockResults.js'

const STEPS = ['Supply-chain stage', 'Food type', 'Waste amount', 'Destination', 'Review', 'Results']
const EMPTY_ALLOCATIONS = Object.fromEntries(WASTE_DESTINATIONS.map(destination => [destination, '']))
const toKilograms = (amount, unit) => Number(amount) * (unit === 'tonnes' ? 1000 : 1)
const hasTwoDecimalsOrLess = value => /^\d+(\.\d{1,2})?$/.test(value)

function App() {
  const [currentStep, setCurrentStep] = useState(-1)
  const [supplyChainStage, setSupplyChainStage] = useState('')
  const [foodCategory, setFoodCategory] = useState('')
  const [wasteAmount, setWasteAmount] = useState('')
  const [unit, setUnit] = useState('kilograms')
  const [allocations, setAllocations] = useState(EMPTY_ALLOCATIONS)
  const [entries, setEntries] = useState([])
  const [lastChangedDestination, setLastChangedDestination] = useState('')
  const [errors, setErrors] = useState({})

  const totalKilograms = wasteAmount ? toKilograms(wasteAmount, unit) : 0
  const allocatedAmount = useMemo(() => Object.values(allocations).reduce((sum, value) => sum + (Number(value) || 0), 0), [allocations])
  const allocationExcess = Math.max(allocatedAmount - Number(wasteAmount || 0), 0)
  const hasNegativeAllocation = Object.values(allocations).some(value => value !== '' && Number(value) < 0)
  const hasInvalidAllocationDecimal = Object.values(allocations).some(value => value && !hasTwoDecimalsOrLess(value))
  const allocationIsValid = allocatedAmount > 0 && allocationExcess === 0 && !hasNegativeAllocation && !hasInvalidAllocationDecimal
  const hasData = Boolean(entries.length || supplyChainStage || foodCategory || wasteAmount || allocatedAmount)

  const changeStep = nextStep => {
    setCurrentStep(nextStep)
    setErrors({})
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const validateStage = () => {
    if (supplyChainStage) return true
    setErrors({ stage: 'Select where in the food supply chain the waste occurred.' })
    return false
  }

  const validateAmount = () => {
    const numericAmount = Number(wasteAmount)
    if (!wasteAmount) setErrors({ amount: 'Enter a waste amount.' })
    else if (!Number.isFinite(numericAmount) || numericAmount <= 0) setErrors({ amount: 'Waste amount must be greater than zero.' })
    else if (!hasTwoDecimalsOrLess(wasteAmount)) setErrors({ amount: 'Enter no more than two decimal places.' })
    else return true
    return false
  }

  const validateAllocations = () => {
    if (hasNegativeAllocation) {
      setErrors({ allocation: 'Destination amounts must be zero or greater.' })
      return false
    }
    if (hasInvalidAllocationDecimal) {
      setErrors({ allocation: 'Enter destination amounts to no more than two decimal places.' })
      return false
    }
    if (allocationExcess > 0) {
      setErrors({ allocation: `Allocated waste exceeds total waste by ${allocationExcess.toFixed(2)} ${unit === 'kilograms' ? 'kg' : 'tonnes'}.` })
      return false
    }
    if (allocatedAmount <= 0) {
      setErrors({ allocation: 'Enter an amount for at least one waste destination.' })
      return false
    }
    return true
  }

  const resetAllData = () => {
    setSupplyChainStage('')
    setFoodCategory('')
    setWasteAmount('')
    setUnit('kilograms')
    setAllocations(EMPTY_ALLOCATIONS)
    setEntries([])
    setLastChangedDestination('')
    setErrors({})
    setCurrentStep(-1)
  }

  const clearAll = () => {
    if (!window.confirm('Clear all calculator data and return to the introduction?')) return
    resetAllData()
  }

  const updateAllocation = (destination, value) => {
    setAllocations(current => ({ ...current, [destination]: value }))
    setLastChangedDestination(destination)
    setErrors({})
  }

  const currentEntry = { supplyChainStage, foodCategory, wasteAmount, unit, allocations }
  const resetCurrentEntry = () => {
    setSupplyChainStage('')
    setFoodCategory('')
    setWasteAmount('')
    setUnit('kilograms')
    setAllocations(EMPTY_ALLOCATIONS)
    setLastChangedDestination('')
  }
  const addAnotherEntry = () => {
    setEntries(current => [...current, { ...currentEntry, id: crypto.randomUUID() }])
    resetCurrentEntry()
    changeStep(0)
  }
  const editSavedEntry = index => {
    const entry = entries[index]
    setSupplyChainStage(entry.supplyChainStage)
    setFoodCategory(entry.foodCategory)
    setWasteAmount(entry.wasteAmount)
    setUnit(entry.unit)
    setAllocations(entry.allocations)
    setEntries(current => current.filter((_, entryIndex) => entryIndex !== index))
    changeStep(0)
  }
  const removeSavedEntry = index => setEntries(current => current.filter((_, entryIndex) => entryIndex !== index))
  const allEntries = [...entries, currentEntry]

  const content = currentStep === -1 ? <Introduction onStart={() => changeStep(0)} /> : [
    <SupplyChainStep key="stage" value={supplyChainStage} error={errors.stage} onChange={value => { setSupplyChainStage(value); setErrors({}) }} onBack={() => changeStep(-1)} onContinue={() => validateStage() && changeStep(1)} />,
    <FoodTypeStep key="food" value={foodCategory} onChange={setFoodCategory} onBack={() => changeStep(0)} onContinue={() => changeStep(2)} />,
    <WasteAmountStep key="amount" amount={wasteAmount} unit={unit} error={errors.amount} onAmountChange={value => { setWasteAmount(value); setErrors({}) }} onUnitChange={setUnit} onBack={() => changeStep(1)} onContinue={() => validateAmount() && changeStep(3)} />,
    <DestinationStep key="destination" allocations={allocations} total={Number(wasteAmount)} allocated={allocatedAmount} unit={unit} error={errors.allocation} allocationExcess={allocationExcess} hasNegative={hasNegativeAllocation} lastChangedDestination={lastChangedDestination} onChange={updateAllocation} onBack={() => changeStep(2)} onContinue={() => validateAllocations() && changeStep(4)} continueDisabled={!allocationIsValid} />,
    <Review key="review" entries={entries} currentEntry={currentEntry} totalKilograms={totalKilograms} onEdit={changeStep} onEditSaved={editSavedEntry} onRemoveSaved={removeSavedEntry} onAddAnother={addAnotherEntry} onBack={() => changeStep(3)} onCalculate={() => changeStep(5)} />,
    <Results key="results" entries={allEntries} onEdit={() => changeStep(4)} onStartOver={clearAll} />,
  ][currentStep]

  return (
    <>
      <Header onHome={resetAllData} onClear={clearAll} showClear={hasData} dark={currentStep === -1} />
      {currentStep >= 0 && <StepIndicator steps={STEPS} currentStep={currentStep} />}
      <main id="main-content" className="main-content" tabIndex="-1">{content}</main>
      <footer><p>Kai Commitment · Food Waste Impact Calculator prototype</p></footer>
    </>
  )
}

function Introduction({ onStart }) {
  return (
    <section className="hero" aria-labelledby="page-title">
      <div className="hero-copy">
        <p className="eyebrow">For New Zealand food businesses</p>
        <h1 id="page-title">Food Waste Impact Calculator</h1>
        <p className="lead">Turn your food waste measurements into a clearer view of their potential environmental and financial impact.</p>
        <button className="button button-primary button-large" type="button" onClick={onStart}>Start calculator</button>
        <p className="privacy-note">This prototype does not save or submit your information.</p>
      </div>
      <div className="hero-food-pattern" aria-hidden="true">
        <svg className="food-arch-mask" viewBox="0 0 1500 190" preserveAspectRatio="none">
          <defs>
            <mask id="food-arch-cutouts">
              <rect width="1500" height="190" fill="white" />
              {[150, 450, 750, 1050, 1350].flatMap((centre) => [
                <ellipse key={`${centre}-outer`} cx={centre} cy="190" rx="205" ry="166" fill="none" stroke="black" strokeWidth="32" />,
                <ellipse key={`${centre}-inner`} cx={centre} cy="190" rx="151" ry="120" fill="none" stroke="black" strokeWidth="28" />,
              ])}
              {[300, 600, 900, 1200].map((intersection) => (
                <path
                  key={`${intersection}-intersection`}
                  d={`M ${intersection} 72 L ${intersection + 36} 126 L ${intersection} 181 L ${intersection - 36} 126 Z`}
                  fill="black"
                />
              ))}
            </mask>
          </defs>
          <rect width="1500" height="190" fill="currentColor" mask="url(#food-arch-cutouts)" />
        </svg>
      </div>
      <div className="hero-support-grid">
        <div className="needs-panel"><h2>What you will need</h2><ul className="check-list"><li>Where the waste occurred in the food supply chain</li><li>The food type, if known</li><li>The total waste amount in kilograms or tonnes</li><li>How that total was distributed across waste destinations</li></ul></div>
      </div>
    </section>
  )
}

function SupplyChainStep({ value, error, onChange, onBack, onContinue }) {
  return <section className="content-section" aria-labelledby="stage-title"><p className="eyebrow">Step 1</p><h1 id="stage-title">Where in the food supply chain did this waste occur?</h1><p className="section-intro" id="supply-chain-support">Choose the stage that best describes where the food waste was generated.</p><SupplyChainStageCards value={value} error={error} onChange={onChange} /><NavigationButtons onBack={onBack} onContinue={onContinue} /></section>
}

function FoodTypeStep({ value, onChange, onBack, onContinue }) {
  return (
    <section className="content-section" aria-labelledby="food-title">
      <p className="eyebrow">Step 2 · Optional</p><h1 id="food-title">What type of food waste are you measuring?</h1><p className="section-intro">Choose one category if you know it, or continue without selecting an option.</p>
      <fieldset className="choice-fieldset"><legend className="sr-only">Food type</legend><div className="simple-choice-list">
        {FOOD_CATEGORIES.map((category, index) => <label className={`simple-choice ${value === category ? 'selected' : ''}`} key={category}><input type="radio" name="food-category" value={category} checked={value === category} onChange={() => onChange(category)} /><span><strong>{category}</strong>{index === 0 && <small>Recommended if you do not separate food waste by category</small>}</span>{value === category && <span className="selected-label" aria-hidden="true">✓ Selected</span>}</label>)}
      </div></fieldset>
      {value && <button type="button" className="text-button" onClick={() => onChange('')}>Clear optional selection</button>}
      <NavigationButtons onBack={onBack} onContinue={onContinue} />
    </section>
  )
}

function WasteAmountStep({ amount, unit, error, onAmountChange, onUnitChange, onBack, onContinue }) {
  return (
    <section className="content-section" aria-labelledby="amount-title"><p className="eyebrow">Step 3</p><h1 id="amount-title">How much food waste are you measuring?</h1><p className="section-intro">Enter the total amount. You will allocate this total across destinations in the next step.</p>
      <div className="form-panel amount-grid"><div className={`form-field ${error ? 'has-error' : ''}`}><label htmlFor="total-waste">Waste amount <span className="required">(required)</span></label><p className="field-hint">Use up to two decimal places.</p><input id="total-waste" type="number" inputMode="decimal" min="0" step="0.01" value={amount} aria-invalid={Boolean(error)} aria-describedby={error ? 'amount-error' : undefined} onChange={event => onAmountChange(event.target.value)} />{error && <p className="field-error" id="amount-error" role="alert">{error}</p>}</div><div className="form-field"><label htmlFor="total-unit">Unit <span className="required">(required)</span></label><p className="field-hint">Choose the measurement unit.</p><select id="total-unit" value={unit} onChange={event => onUnitChange(event.target.value)}>{UNITS.map(option => <option key={option}>{option}</option>)}</select></div></div>
      <NavigationButtons onBack={onBack} onContinue={onContinue} />
    </section>
  )
}

function DestinationStep({ allocations, total, allocated, unit, error, allocationExcess, hasNegative, lastChangedDestination, onChange, onBack, onContinue, continueDisabled }) {
  const remaining = total - allocated
  const liveError = hasNegative
    ? 'Destination amounts must be zero or greater.'
    : allocationExcess > 0
      ? `Allocated waste exceeds total waste by ${allocationExcess.toFixed(2)} ${unit === 'kilograms' ? 'kg' : 'tonnes'}.`
      : error
  const summaryInvalid = allocationExcess > 0 || hasNegative
  return (
    <section className="content-section wide" aria-labelledby="destination-title"><p className="eyebrow">Step 4</p><h1 id="destination-title">Where did the food waste go?</h1><p className="section-intro">Enter an amount for every applicable destination. The combined amount cannot exceed your total waste.</p>
      <div className={`allocation-summary ${summaryInvalid ? 'invalid' : ''}`} aria-live="polite"><div><span>Total waste</span><strong>{total.toFixed(2)} {unit}</strong></div><div><span>Allocated</span><strong>{allocated.toFixed(2)} {unit}</strong></div><div><span>Remaining</span><strong>{remaining.toFixed(2)} {unit}</strong></div></div>
      <div className={`destination-list ${liveError ? 'has-error' : ''}`}>{WASTE_DESTINATIONS.map(destination => { const value = allocations[destination]; const inputInvalid = (value !== '' && Number(value) < 0) || (allocationExcess > 0 && destination === lastChangedDestination); return <div className={`destination-row ${inputInvalid ? 'invalid' : ''}`} key={destination}><label htmlFor={`destination-${destination.replace(/[^a-z0-9]/gi, '-').toLowerCase()}`}>{destination}{destination === 'Not harvested / ploughed-in' && <small>Primarily relevant to agriculture</small>}</label><div className="amount-with-unit"><input id={`destination-${destination.replace(/[^a-z0-9]/gi, '-').toLowerCase()}`} type="number" inputMode="decimal" min="0" step="0.01" value={value} onChange={event => onChange(destination, event.target.value)} aria-invalid={inputInvalid} aria-label={`${destination} amount in ${unit}`} /><span>{unit}</span></div></div> })}</div>
      {liveError && <p className="field-error" role="alert">{liveError}</p>}
      <NavigationButtons onBack={onBack} onContinue={onContinue} continueDisabled={continueDisabled} />
    </section>
  )
}

function Review({ entries, currentEntry, totalKilograms, onEdit, onEditSaved, onRemoveSaved, onAddAnother, onBack, onCalculate }) {
  const usedDestinations = Object.entries(currentEntry.allocations).filter(([, amount]) => Number(amount) > 0)
  return (
    <section className="content-section wide" aria-labelledby="review-title"><p className="eyebrow">Step 5</p><h1 id="review-title">Review your information</h1><p className="section-intro">Check this entry, or add another supply-chain entry before viewing the combined results.</p>
      {entries.length > 0 && <section className="saved-entries" aria-labelledby="saved-entry-title"><div className="section-heading-row"><h2 id="saved-entry-title">Added entries</h2><span>{entries.length}</span></div>{entries.map((entry, index) => <article className="saved-entry-card" key={entry.id}><div><span className="eyebrow">Entry {index + 1}</span><h3>{entry.supplyChainStage}</h3><p>{Number(entry.wasteAmount).toFixed(2)} {entry.unit} · {entry.foodCategory || 'Food type not provided'}</p></div><div className="card-actions"><button className="text-button" type="button" onClick={() => onEditSaved(index)}>Edit<span className="sr-only"> entry {index + 1}</span></button><button className="text-button danger" type="button" onClick={() => onRemoveSaved(index)}>Remove<span className="sr-only"> entry {index + 1}</span></button></div></article>)}</section>}
      <div className="section-heading-row current-entry-heading"><h2>Current entry {entries.length + 1}</h2><span>Ready to calculate</span></div>
      <article className="review-block"><div className="section-heading-row"><h2>Supply-chain stage</h2><button className="text-button" type="button" onClick={() => onEdit(0)}>Edit</button></div><p><strong>Supply-chain stage:</strong> {currentEntry.supplyChainStage}</p></article>
      <article className="review-block"><div className="section-heading-row"><h2>Food type</h2><button className="text-button" type="button" onClick={() => onEdit(1)}>Edit</button></div><p>{currentEntry.foodCategory || 'Not provided'}</p></article>
      <article className="review-block"><div className="section-heading-row"><h2>Waste amount</h2><button className="text-button" type="button" onClick={() => onEdit(2)}>Edit</button></div><p><strong>{Number(currentEntry.wasteAmount).toFixed(2)} {currentEntry.unit}</strong> · {totalKilograms.toLocaleString('en-NZ', { maximumFractionDigits: 2 })} kg</p></article>
      <article className="review-block"><div className="section-heading-row"><h2>Waste destinations</h2><button className="text-button" type="button" onClick={() => onEdit(3)}>Edit</button></div><dl className="review-destinations">{usedDestinations.map(([destination, amount]) => <div key={destination}><dt>{destination}</dt><dd>{Number(amount).toFixed(2)} {currentEntry.unit}</dd></div>)}</dl></article>
      <button className="button button-add add-entry-button" type="button" onClick={onAddAnother}>+ Add another supply-chain entry</button>
      <Disclaimer compact /><NavigationButtons onBack={onBack} onContinue={onCalculate} continueLabel={entries.length ? `Calculate results for ${entries.length + 1} entries` : 'Calculate impact'} />
    </section>
  )
}

const aggregateRows = items => Object.entries(items.reduce((totals, item) => ({ ...totals, [item.label]: (totals[item.label] || 0) + item.kilograms }), {})).map(([label, kilograms]) => ({ label, kilograms }))

function Results({ entries, onEdit, onStartOver }) {
  const totalKilograms = entries.reduce((sum, entry) => sum + toKilograms(entry.wasteAmount, entry.unit), 0)
  const stageRows = aggregateRows(entries.map(entry => ({ label: entry.supplyChainStage, kilograms: toKilograms(entry.wasteAmount, entry.unit) })))
  const destinationRows = aggregateRows(entries.flatMap(entry => Object.entries(entry.allocations).filter(([, amount]) => Number(amount) > 0).map(([label, amount]) => ({ label, kilograms: toKilograms(amount, entry.unit) }))))
  const foodItems = entries.filter(entry => entry.foodCategory && entry.foodCategory !== 'General food waste — type unknown').map(entry => ({ label: entry.foodCategory, kilograms: toKilograms(entry.wasteAmount, entry.unit) }))
  const foodRows = aggregateRows(foodItems)
  const breakdowns = {
    stage: { rows: stageRows },
    destination: destinationRows.length ? { rows: destinationRows } : { unavailable: 'Waste-destination breakdown is not available because no destination data was provided.' },
    food: foodRows.length ? { rows: foodRows } : { unavailable: 'Food-type breakdown is not available because no food category data was provided.' },
  }
  const tonnes = totalKilograms / 1000
  const downloadResults = () => {
    const entryLines = entries.flatMap((entry, index) => {
      const destinationLines = Object.entries(entry.allocations)
        .filter(([, amount]) => Number(amount) > 0)
        .map(([destination, amount]) => `  - ${destination}: ${Number(amount).toFixed(2)} ${entry.unit}`)
      return [
        `Entry ${index + 1}: ${entry.supplyChainStage}`,
        `Food type: ${entry.foodCategory || 'Not provided'}`,
        `Waste amount: ${Number(entry.wasteAmount).toFixed(2)} ${entry.unit}`,
        'Destinations:',
        ...destinationLines,
        '',
      ]
    })
    const report = [
      'Food Waste Impact Calculator — Demonstration Results',
      '',
      `Total food waste: ${totalKilograms.toLocaleString('en-NZ', { maximumFractionDigits: 2 })} kg`,
      `Total food waste: ${tonnes.toLocaleString('en-NZ', { minimumFractionDigits: 3, maximumFractionDigits: 3 })} tonnes`,
      '',
      ...entryLines,
      DEMONSTRATION_NOTICE,
      'CO₂e, financial cost, water impact and tangible equivalents are not calculated in this prototype.',
      'Percentage waste is not available because total food handled data is required.',
    ].join('\n')
    const url = URL.createObjectURL(new Blob([report], { type: 'text/plain;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'food-waste-impact-results.txt'
    link.click()
    URL.revokeObjectURL(url)
  }
  return (
    <section className="content-section results-page" aria-labelledby="results-title"><p className="eyebrow">Step 6</p><h1 id="results-title">Your estimated impact</h1><p className="section-intro">Only total food waste and input-based breakdown percentages are calculated in this prototype.</p>
      <section className="results-section" aria-labelledby="impact-summary-title"><div className="result-section-heading"><span className="section-number">01</span><div><h2 id="impact-summary-title">Impact summary</h2><p>A high-level view of the recorded food waste.</p></div></div><div className="results-grid"><ResultsCard label="Total food waste" value={`${totalKilograms.toLocaleString('en-NZ', { maximumFractionDigits: 2 })} kg`} note={`${tonnes.toLocaleString('en-NZ', { minimumFractionDigits: 3, maximumFractionDigits: 3 })} tonnes`} primary /><ResultsCard label="Estimated CO₂e emissions" value={MOCK_RESULTS.emissions} note={DEMONSTRATION_NOTICE} /><ResultsCard label="Estimated financial cost in NZD" value={MOCK_RESULTS.cost} note={DEMONSTRATION_NOTICE} /><ResultsCard label="Estimated water impact" value={MOCK_RESULTS.water} note={DEMONSTRATION_NOTICE} /><ResultsCard label="Percentage waste" value="Not available" note="Total food handled data is required." /></div></section>
      <section className="results-section" aria-labelledby="equivalents-title"><div className="result-section-heading"><span className="section-number">02</span><div><h2 id="equivalents-title">Tangible equivalents</h2><p>Plain-language comparisons will appear after they are approved.</p></div></div><div className="equivalent-grid">{['Kilometres driven', 'Meal equivalents', 'Shower equivalents'].map(label => <article key={label}><span aria-hidden="true">—</span><h3>{label}</h3><p>Available once approved conversion factors are supplied.</p></article>)}</div></section>
      <BreakdownTabs breakdowns={breakdowns} totalKilograms={totalKilograms} />
      <section className="methodology-compact" id="results-methodology" aria-labelledby="results-methodology-title"><h2 id="results-methodology-title">Methodology &amp; Limitations</h2><p>Results are estimates. Scientific factors and equivalents have not yet been supplied or approved.</p><p>Data sources and factor version: not yet available.</p><details><summary>View methodology</summary><div><p>Total waste and category percentages use only the values entered in this calculator.</p><p>CO₂e, cost, water and tangible equivalents remain demonstration placeholders. Percentage waste is unavailable until total food handled data is collected.</p></div></details></section>
      <div className="result-actions"><button className="button button-secondary" type="button" onClick={onEdit}>Edit your data</button><button className="button button-secondary" type="button" onClick={onStartOver}>Start a new calculation</button><button className="button button-primary" type="button" onClick={downloadResults}>Download results</button></div>
    </section>
  )
}

export default App
