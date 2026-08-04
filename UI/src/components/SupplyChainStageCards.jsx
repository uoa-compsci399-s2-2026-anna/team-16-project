import { useState } from 'react'
import { SUPPLY_CHAIN_STAGES } from '../data/options.js'

const toId = value => value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')

export default function SupplyChainStageCards({ value, error, onChange }) {
  const [expandedStages, setExpandedStages] = useState([])

  const toggleDetails = stageName => {
    setExpandedStages(current => current.includes(stageName)
      ? current.filter(name => name !== stageName)
      : [...current, stageName])
  }

  return (
    <fieldset className={`stage-fieldset ${error ? 'has-error' : ''}`} aria-describedby={error ? 'supply-chain-error supply-chain-support' : 'supply-chain-support'}>
      <legend>
        Supply-chain stage <span className="required">(required)</span>
      </legend>
      <div className="stage-card-list">
        {SUPPLY_CHAIN_STAGES.map(stage => {
          const stageId = toId(stage.name)
          const detailsId = `${stageId}-details`
          const expanded = expandedStages.includes(stage.name)
          const selected = value === stage.name
          return (
            <div className={`stage-card ${selected ? 'selected' : ''}`} key={stage.name}>
              <label className="stage-select" htmlFor={stageId}>
                <input id={stageId} name="supply-chain-stage" type="radio" value={stage.name} checked={selected} onChange={() => onChange(stage.name)} />
                <span className="stage-copy">
                  <span className="stage-title">{stage.name}</span>
                  <span className="stage-description">{stage.description}</span>
                </span>
                {selected && <span className="selected-label" aria-hidden="true">✓ Selected</span>}
              </label>
              <button
                className="details-button"
                type="button"
                aria-expanded={expanded}
                aria-controls={detailsId}
                aria-label={`${expanded ? 'Hide' : 'Show'} details for ${stage.name}`}
                onClick={() => toggleDetails(stage.name)}
              >
                Details <span className={`chevron ${expanded ? 'expanded' : ''}`} aria-hidden="true">⌄</span>
              </button>
              <div className="stage-details" id={detailsId} hidden={!expanded}>
                <p>{stage.details}</p>
              </div>
            </div>
          )
        })}
      </div>
      {error && <p className="field-error" id="supply-chain-error" role="alert">{error}</p>}
    </fieldset>
  )
}
