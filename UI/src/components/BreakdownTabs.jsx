import { useState } from 'react'

const TAB_LABELS = {
  stage: 'By supply-chain stage',
  destination: 'By waste destination',
  food: 'By food type',
}

export default function BreakdownTabs({ breakdowns, totalKilograms }) {
  const [activeTab, setActiveTab] = useState('stage')
  const tabKeys = Object.keys(TAB_LABELS)
  const current = breakdowns[activeTab]

  const handleKeyDown = event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    const currentIndex = tabKeys.indexOf(activeTab)
    const nextIndex = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? tabKeys.length - 1
        : (currentIndex + (event.key === 'ArrowRight' ? 1 : -1) + tabKeys.length) % tabKeys.length
    setActiveTab(tabKeys[nextIndex])
    document.getElementById(`breakdown-tab-${tabKeys[nextIndex]}`)?.focus()
  }

  return (
    <section className="results-section" aria-labelledby="breakdown-title">
      <div className="result-section-heading">
        <span className="section-number">03</span>
        <div><h2 id="breakdown-title">Breakdown by category</h2><p>Explore how the recorded waste is distributed.</p></div>
      </div>
      <div className="breakdown-tabs" role="tablist" aria-label="Waste breakdown" onKeyDown={handleKeyDown}>
        {tabKeys.map(key => (
          <button
            id={`breakdown-tab-${key}`}
            key={key}
            type="button"
            role="tab"
            aria-selected={activeTab === key}
            aria-controls={`breakdown-panel-${key}`}
            tabIndex={activeTab === key ? 0 : -1}
            onClick={() => setActiveTab(key)}
          >{TAB_LABELS[key]}</button>
        ))}
      </div>
      <div id={`breakdown-panel-${activeTab}`} className="breakdown-panel" role="tabpanel" aria-labelledby={`breakdown-tab-${activeTab}`} tabIndex="0">
        {current.unavailable ? (
          <p className="empty-state">{current.unavailable}</p>
        ) : (
          <>
            <div className="bar-list" aria-hidden="true">
              {current.rows.map(row => {
                const percentage = totalKilograms ? (row.kilograms / totalKilograms) * 100 : 0
                return (
                  <div className="bar-row" key={row.label}>
                    <div><strong>{row.label}</strong><span>{percentage.toFixed(1)}%</span></div>
                    <div className="bar-track"><span style={{ width: `${percentage}%` }} /></div>
                  </div>
                )
              })}
            </div>
            <div className="table-scroll" tabIndex="0">
              <table>
                <caption>{TAB_LABELS[activeTab]} data</caption>
                <thead><tr><th scope="col">Category</th><th scope="col">Waste amount</th><th scope="col">Percentage</th><th scope="col">CO₂e</th><th scope="col">Cost</th><th scope="col">Water</th></tr></thead>
                <tbody>
                  {current.rows.map(row => {
                    const percentage = totalKilograms ? (row.kilograms / totalKilograms) * 100 : 0
                    return <tr key={row.label}><th scope="row">{row.label}</th><td>{row.kilograms.toLocaleString('en-NZ', { maximumFractionDigits: 2 })} kg</td><td>{percentage.toFixed(1)}%</td><td>Demo only</td><td>Demo only</td><td>Demo only</td></tr>
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </section>
  )
}
