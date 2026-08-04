import FormField from './FormField.jsx'
import { FOOD_CATEGORIES, UNITS, WASTE_DESTINATIONS } from '../data/options.js'

export default function WasteEntryForm({ draft, errors, onChange, onSubmit, editing }) {
  return (
    <form className="entry-form" onSubmit={onSubmit} noValidate>
      <div className="entry-form-grid">
        <FormField id="waste-amount" label="Waste amount" error={errors.amount} hint="Enter a number greater than zero.">
          {props => <input {...props} id="waste-amount" inputMode="decimal" type="number" min="0" step="any" value={draft.amount} onChange={event => onChange('amount', event.target.value)} />}
        </FormField>
        <FormField id="waste-unit" label="Unit" error={errors.unit}>
          {props => (
            <select {...props} id="waste-unit" value={draft.unit} onChange={event => onChange('unit', event.target.value)}>
              {UNITS.map(unit => <option key={unit}>{unit}</option>)}
            </select>
          )}
        </FormField>
        <FormField id="waste-destination" label="Waste destination" error={errors.destination}>
          {props => (
            <select {...props} id="waste-destination" value={draft.destination} onChange={event => onChange('destination', event.target.value)}>
              <option value="">Select a destination</option>
              {WASTE_DESTINATIONS.map(option => <option key={option}>{option}</option>)}
            </select>
          )}
        </FormField>
        <FormField id="food-category" label="Food category" optional>
          {props => (
            <select {...props} id="food-category" value={draft.foodCategory} onChange={event => onChange('foodCategory', event.target.value)}>
              <option value="">Not specified</option>
              {FOOD_CATEGORIES.map(option => <option key={option}>{option}</option>)}
            </select>
          )}
        </FormField>
      </div>
      <button className="button button-add" type="submit">{editing ? 'Save changes' : 'Add waste record'}</button>
    </form>
  )
}
