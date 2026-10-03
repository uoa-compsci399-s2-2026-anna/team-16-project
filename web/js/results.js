import { escapeHtml, formatNumber, stepNav } from './view.js'
import { t, activeLanguage, isMachineTranslated, MACHINE_TRANSLATION_NOTICE } from './i18n.js'
import { entryTotal, isPresetUnit, kgToTonnes, presetUnitCode, rowKgString } from './units.js'
import { ComparisonResults, ImprovementScenario } from './improvement.js'
import { contribute, exportPdf } from './api.js'
// §7.2a. Written from here on one event only — a contribute that landed — because
// `contributed` is the one thing in `RESULT_KEYS` this module can change.
import { writeResultSnapshot } from './snapshot.js'
import { exportPayload } from './submission.js'
// v1.68. The `en-NZ` date pin lives in one place for this feature — see
// `formatInstant`'s own note: what is printed back has to be what the field
// that collected it would accept, character for character.
import { formatInstant } from './period.js'
// `state as liveState`: the grace window's `setTimeout` fires long after the click that
// armed it, and the snapshot that click was handed may by then describe a calculator the
// visitor has cleared or recalculated. `setState` mutates this object in place, so the
// module binding is always the current state — and every renderer here still takes its
// `state` as a parameter, so nothing else in this file reads the global by accident.
import { leafDisplayName, setState, state as liveState, taxonomyForResult } from './state.js'

const DEMONSTRATION_NOTICE = 'Demonstration only — verified calculation factors have not yet been supplied.'

// Task 4's flower stays on screen for one `kc-flower-bloom` (styles.css) plus headroom,
// then `contributeCalculation` clears the flag that renders it. Not the animation's own
// duration alone: a re-render that lands mid-tween (a keystroke in the improvement panel,
// say) would otherwise cut the bloom off with the timer already spent.
const CONTRIBUTE_CELEBRATE_MS = 900

// Round four: the client asked for a tick on the left, a separate Submit button on the
// right, and five seconds in which the press can be taken back.
//
// **The five seconds are a grace period BEFORE the request, not an undo after it.**
// `POST /api/v1/contribute` is one-way by construction — `set_public_contribution`
// (`db/repository.py`) only ever sets `is_public_contributed` to TRUE and §6.2.2 has no
// path that clears it — so a real undo would mean a new route, a new repository function
// and a contract change. It would also mean a window in which the row is in the public
// aggregate and the visitor has been told they can still withdraw it. Arming a timer and
// sending nothing until it expires gives the visitor the same five seconds with none of
// that: "undo" cancels a request that was never made, so there is nothing to unsend.
//
// Kept in step with `--contribute-grace` in `styles.css`, which is the countdown
// animation's own duration. The two are the same five seconds seen from two sides, and a
// change to one without the other shows a bar that empties early or late.
const CONTRIBUTE_GRACE_MS = 5000

// **The timer handle lives here, in module scope, and it has to.** `render()`
// (`calculator.js`) replaces `main.innerHTML` on every `setState`, so a handle parked on
// a `data-` attribute, a closure over an element or anything else inside `<main>` is
// discarded by the first unrelated re-render — and a handle that is discarded is a
// `setTimeout` nobody can cancel, i.e. an Undo button that does not undo. Same pattern,
// and the same reason, as `reloadTaxonomy` in `calculator.js`.
//
// `null` when no window is open. `state.contributeArmedUntil` is the *visible* half of
// the same fact and is what renders; this is only the cancel handle.
let contributeGraceTimer = null

// §7.6.5-adjacent: the one piece of user *preference* this module reads rather than an
// API figure. Guarded rather than called bare because the Node harness
// `tests/web/test_results_export.py` stubs `window` as `{ location: { search: '' } }` -
// no `matchMedia` - to run this module outside a browser at all; a bare call would throw
// on module load rather than on the one branch that actually needs it.
const prefersReducedMotion = () =>
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches

// The English source strings, which are also the catalogue keys. `TAB_LABELS` is
// keyed by tab and read in three places, one of which is a table caption, so the
// translation happens where it is rendered rather than here - a module-level t()
// would be evaluated once and would be right only by accident.
const TAB_LABELS = { stage: 'By supply-chain stage', destination: 'By waste destination', food: 'By food type' }

// The one metric code this module names, and it is not the hard-coded list §7.6.5 forbids:
// that rule exists because a view listing `['co2e','water','cost']` *omits* the metric a
// staff member inserted, and every metric the response carries still appears here. `mass` is
// held out because §3 hoists it — its formula is `qty_kg` (§4.3), so `scenario.total_kg` and
// `by_destination[].qty_kg` are the same figure, and it is already on screen as the primary
// card and the "Waste amount" column. Printing it twice per row is not configurability.
const MASS_METRIC = 'mass'

// §7.6.6: `downstream` may be negative (§2.2), so a metric total may be — and that negative
// total is the reuse-and-offset result the calculator exists to show. `.value-negative` is
// the stylesheet's marker for it: beetroot and bold, and deliberately **no arrow**, because
// this is a quantity rather than a movement. Nothing marks an ordinary positive total: a
// green mark against every figure on the page is decoration, not a signal. The arrows belong
// to the comparison screen's `.change-*` classes, where the sign of `net_benefit` is a
// direction of change — see the ruling beside them in `styles.css`.
const negativeClass = value => (Number.isFinite(value) && value < 0 ? ' value-negative' : '')

// Display-only coercion of an API decimal string (§7.6.1): §1.2 puts decimals on the wire
// as strings and `toLocaleString` needs a number. `Number(value) || 0` stood here and made
// "the engine did not return this metric", "this value is malformed" and "this value is
// zero" the same figure on screen. A figure that is absent has to read as absent, which is
// what `formatNumber` does with a non-finite input.
const number = value => (value === null || value === undefined || value === '' ? Number.NaN : Number(value))

// Arithmetic on what the user typed, not on an API figure. A blank destination row is a
// zero here, and that is the only reason a `|| 0` is correct anywhere in this module.
const typed = value => Number(value) || 0

const findByCode = (items, code) => (items || []).find(item => item.code === code)

// §6.2 returns each metric total in `unit`, and that is the unit the number is in. §6.1
// rules `display_unit` a presentation variant of `unit` *at the same scale* — never a
// different scale, because §7.6.1 leaves no layer that could convert between the two.
// Prefer the response's own `unit` regardless: it travels with the figure.
const metricUnit = (metric, definition) => metric?.unit || definition?.unit || ''

// Item ⑦: `time_frame` is a closed vocabulary (§6.2) for exactly the reason
// `gwp_horizon` is closed to 20 and 100 - a value this map has no phrase for must
// never reach a visitor as the raw identifier. The four phrases are the ones
// `calculator.js`'s own step-4 `<select>` already offers, reused rather than
// reworded so the word a visitor chose is the word they see reflected back.
//
// **`custom` is the fifth member and it is deliberately not in here (v1.68).**
// Not an omission: "Custom period" is the name of a *control*, and read back to
// somebody looking at their own results it says nothing they did not already
// know. What they chose was two instants, so two instants are what the line
// says. `periodPhrase` below is where that decision lives, and the map keeps
// its original job — a value with no phrase and no interval renders nothing.
const TIME_FRAME_LABELS = {
  one_week: 'One week',
  one_month: 'One month',
  one_quarter: 'One quarter',
  one_year: 'One year',
}

// v1.68. What the period reads back as, in the three shapes §6.2 accepts.
//
// | What was sent | What this says |
// | --- | --- |
// | a preset alone (every row before v1.67) | `One week` |
// | a preset **and** an interval — the designed normal case | `One week · 14/09/2026 08:10 – 21/09/2026 08:10` |
// | `custom` and an interval | `14/09/2026 08:10 – 21/09/2026 08:10` |
//
// **A preset beside an interval prints both, and that is the whole point of the
// second row.** From v1.67 a preset is a button that *fills* the picker, so the
// interval is what the figures actually cover and `time_frame` is the record of
// which shortcut produced it. A line that printed only "One week" over dates the
// visitor may since have moved would be telling half the truth, and the half it
// dropped is the one the client asks for first — *did they mean a standard week,
// or did they choose those dates?* Printing the phrase alone cannot answer it;
// printing the dates alone cannot either.
//
// **The dash and the separator are notation, not prose, and they carry no `t()`
// key.** `api/pdf_render.py` prints this identical sentence onto the PDF through
// `Catalogue.gettext`, which *raises* rather than falling back to English for a
// key a catalogue does not carry — so a newly coined sentence here would mean
// every non-English download 500ing until twenty catalogues caught up. The same
// reasoning that document already applies to `GWP100` and to its own
// `·`-separated cover line applies here: a range written `A – B` is the same
// notation in every language this calculator ships in, and the sentence around
// it is translated by the key that already exists.
const periodPhrase = (timeFrame, periodStart, periodEnd) => {
  const interval = periodStart && periodEnd
    ? `${formatInstant(periodStart)} – ${formatInstant(periodEnd)}`
    : ''
  if (timeFrame === 'custom') return interval
  const phrase = TIME_FRAME_LABELS[timeFrame]
  if (!phrase) return ''
  return interval ? `${t(phrase)} · ${interval}` : t(phrase)
}

// A label, never a computation (contract v1.48, §6.2): nothing on this page is scaled
// by the period, so it renders as one plain line rather than beside any figure it might
// be misread as multiplying. `''` - "not stated" - renders nothing, the same way an
// unstated money figure renders nothing rather than a placeholder.
function resultsPeriod(state) {
  const period = periodPhrase(state.timeFrame, state.periodStart, state.periodEnd)
  if (!period) return ''
  return `<p class="results-period">${escapeHtml(t('These figures cover: %(period)s', { period }))}</p>`
}

// The export's own line for the same fact, worded identically to `resultsPeriod`
// above so a visitor reading the page and the file they downloaded from it sees
// the same sentence rather than two different ways of saying the same thing.
const periodLine = state => {
  const period = periodPhrase(state.timeFrame, state.periodStart, state.periodEnd)
  return period ? t('These figures cover: %(period)s', { period }) : ''
}

// Display-only coercion of a two-decimal-place NZD string (§4.5, §1.2): the money
// block is dollars and cents, not a ten-place metric value, but it is still a string
// on the wire and still passes through `Number()` for display only (§7.6.1). 'NZ$' is
// currency notation, not a phrase, so it is not passed through `t()` - the same
// reasoning `kg` throughout this file is written in and never translated (§7.7.7).
const nzd = value => `NZ$${formatNumber(number(value), 2)}`
const percentText = value => `${formatNumber(number(value), 2)}%`
const hasValue = value => value !== null && value !== undefined

// §4.6: every figure this card and the money block below it carry now travels with a
// `data_state` entry, and `null` alone cannot tell "nobody typed one" from "some entries
// did and some did not" from "everybody typed one and the ratio is still undefined" -
// that is the whole reason the engine grew a fourth state rather than leaving the figure
// `None`-or-a-value. A partially answered figure used to arrive as a wrong number (a sum
// with a silently short denominator) and, since Task 1, arrives as `null` instead -
// silence is the honest fallback but it is not the best one available, so `incomplete`
// gets its own sentence rather than being folded into "nobody said".
//
// `undefined` (v1.51) is the fourth: every entry answered, and the ratio built from what
// they answered has no defined value because it summed to zero — a production total of
// zero is not the same claim as "nobody said", and reading it that way is exactly the
// defect v1.51 closed. See `engine/calculate.py::_share_state`.
//
// One field a reader of this comment should not miss: `production_share_percent` is the
// one figure on this page the mock-factor warning does not describe. Every other card is
// `qty_kg * factor`, and the factor set is mock (open item O-1) until real ones arrive.
// This card is `current.total_kg / total_input_kg` - two masses the visitor typed, with
// no factor and no formula anywhere in the division - so it is exactly as trustworthy
// under the placeholder banner as it will be once real factors are supplied. The
// methodology paragraph in `renderResults` says so in words a visitor can read.

// One field of `totals.money`, read against its own `data_state` entry (§4.6): `complete`
// formats the value, `incomplete` returns the shared note below instead of nothing,
// `undefined` (v1.51 - reachable only by `wasted_share_percent`, the one ratio among the
// four money fields) returns its own note rather than either of the other two sentences,
// and `not_supplied` - and any state this module has not learned - returns `null`, which
// the caller reads as "print no row", the same rule `hasValue` gave every field here
// before `data_state` existed.
//
// Both notes are literal `t('...')` calls, not module-level constants passed by reference -
// `tests/web/i18n_keys.py` only extracts a `t()` argument literally or from its own named
// list of indirect constants, and this string is not on that list. An indirect reference
// here would render in every language but the one the visitor chose, silently, with no
// test able to catch it - the exact failure mode `_INDIRECT` exists to name deliberately
// rather than let happen by accident.
function moneyFieldText(money, dataState, field, format) {
  if (hasValue(money?.[field])) return format(money[field])
  if (dataState?.[field] === 'incomplete') return t('Not every entry supplied this figure, so it cannot be totalled.')
  if (dataState?.[field] === 'undefined') return t('The total value was zero, so this cannot be calculated.')
  return null
}

/**
 * The "Percentage waste" card's own four states (§4.6, v1.51), read and not derived: the
 * engine divides `current.total_kg` by the summed `total_input_kg` and this only formats
 * what comes back. `complete` prints the percentage; `incomplete` says the coverage was
 * partial rather than showing nothing where a wrong number used to sit; `undefined` says
 * every entry answered and the total came to zero, so the share itself has no value -
 * distinct from `not_supplied` because "nobody said" and "the answer was zero" are not the
 * same claim, and conflating them is the defect v1.51 closed; `not_supplied` - and anything
 * this module has not learned the name of yet, the same forward-compatible fallback
 * `hasValue` already gives every other absent figure - says nobody stated it, in words
 * about what the visitor typed rather than about what the calculator can report.
 */
function productionShareText(totals) {
  const state = totals.data_state?.production_share_percent
  if (state === 'complete' && hasValue(totals.production_share_percent)) {
    return { value: `${formatNumber(number(totals.production_share_percent), 2)}%`, note: '' }
  }
  if (state === 'incomplete') {
    return {
      value: t('Data incomplete'),
      note: t('Some entries stated a production total and some did not, so a share of waste cannot be shown.'),
    }
  }
  if (state === 'undefined') {
    return {
      value: t('Undefined'),
      note: t('You said this covered 0 kg in total, so a share of waste cannot be shown.'),
    }
  }
  return {
    value: t('Not supplied'),
    note: t('You did not say how much food this covered, so a share of waste cannot be shown.'),
  }
}

/**
 * §4.5's money block, rendered beside `summaryCards()` inside the same "Impact
 * summary" section rather than as a section of its own - four figures the engine
 * derived from what the visitor typed, and nothing here derives a fifth. `totals.money`
 * is `null` unless at least one entry supplied a value, and each of the four fields is
 * independently `null` unless what it derives from was supplied - a computed zero would
 * read as "this food was worth nothing" rather than "nobody said" (§4.5), so a `null`
 * field is left off the list rather than printed as `0.00`, a dash, or a bare `NZ$`.
 *
 * The share is printed exactly as the response gives it, deliberately unclamped
 * (§4.5): a figure over 100% is a visitor's own typo showing through, not a rendering
 * fault, and nothing here is a bar or a width that a figure over 100% could overflow.
 *
 * **Three figures, not four: `saving_nzd` is not one of them, and that is the fix
 * rather than a deletion.** The three above are properties of the *current* scenario
 * alone — what the visitor said their food was worth — and this block is fed by the
 * Calculate button, which sends `alternative: null` for every entry
 * (`submission.js`). §4.5 makes `saving_nzd` `None` "when no entry carries an
 * alternative", so the row this block used to carry could not be reached by any
 * visitor: the response that does hold a saving is Compare Impact's, and it lands on
 * `state.improvementResult`. The saving is now rendered by `ComparisonResults`
 * (`improvement.js`), beside the comparison that produced it, and written to the
 * export by `comparisonLines` below, under the same heading as that comparison.
 * Teaching this block to read a second response would have put a figure about the
 * improved scenario inside a section describing the current one.
 *
 * §4.6 gave each of these three fields its own `data_state`, on the same terms as the
 * percentage card above. Task 1 turned a partial sum into `null` — a two-entry submission
 * that priced one entry no longer reports that entry's figure as the whole submission's
 * total — and a block that only checked `hasValue` would now render nothing for that row,
 * which is honest but not the most it can say: `moneyFieldText` prints the shared
 * incomplete note instead, and stays silent only for a field nobody touched at all.
 */
function moneySummary(totals) {
  const money = totals.money
  if (!money) return ''
  const dataState = totals.data_state
  const rows = []
  const total = moneyFieldText(money, dataState, 'total_value_nzd', nzd)
  if (total !== null) {
    rows.push(`<div class="money-row"><span class="money-label">${escapeHtml(t('Total value of food handled'))}</span><span class="money-value">${escapeHtml(total)}</span></div>`)
  }
  const wasted = moneyFieldText(money, dataState, 'wasted_value_nzd', nzd)
  if (wasted !== null) {
    rows.push(`<div class="money-row"><span class="money-label">${escapeHtml(t('Value of food wasted'))}</span><span class="money-value">${escapeHtml(wasted)}</span></div>`)
  }
  const share = moneyFieldText(money, dataState, 'wasted_share_percent', percentText)
  if (share !== null) {
    rows.push(`<div class="money-row"><span class="money-label">${escapeHtml(t('Share of value wasted'))}</span><span class="money-value">${escapeHtml(share)}</span></div>`)
  }
  if (!rows.length) return ''
  return `<div class="money-summary"><h3>${escapeHtml(t('The money'))}</h3><p class="result-note">${escapeHtml(t("Figures the calculator did not derive: what you typed for value, summed by the calculation service."))}</p><div class="money-rows">${rows.join('')}</div></div>`
}

// `moneySummary`'s figures, in text, worded to match the rows on screen rather than
// re-deriving them: same three fields, same `data_state` rule. Returns `[]` (no
// heading printed) when the block is `null` or carries nothing - a heading over an empty
// list is the "—" this module exists to avoid. The saving is not here for the reason it
// is not on screen here either: it belongs to the comparison, and `savingLines` below
// writes it under that comparison's own heading.
function moneyLines(totals) {
  const money = totals.money
  if (!money) return []
  const dataState = totals.data_state
  const lines = []
  const total = moneyFieldText(money, dataState, 'total_value_nzd', nzd)
  if (total !== null) lines.push(`  - ${t('Total value of food handled')}: ${total}`)
  const wasted = moneyFieldText(money, dataState, 'wasted_value_nzd', nzd)
  if (wasted !== null) lines.push(`  - ${t('Value of food wasted')}: ${wasted}`)
  const share = moneyFieldText(money, dataState, 'wasted_share_percent', percentText)
  if (share !== null) lines.push(`  - ${t('Share of value wasted')}: ${share}`)
  if (!lines.length) return []
  return ['', t('The money'), ...lines]
}

/**
 * A summary card's explanation: the `i`, and the panel it reveals.
 *
 * A pure hover tooltip -- the pointer entering the `i` reveals it and leaving hides it,
 * with no click-to-latch state. The adjacent-sibling focus rule in CSS gives keyboard
 * users the same panel when Tab puts a visible focus ring on the button.
 *
 * **`label` is a parameter and not a constant.** It was `How this comparison was worked
 * out` on every card, which is the equivalences' own string (see `equivalences` below):
 * a screen reader announced "how this comparison was worked out" for *Total food waste*
 * and *Percentage waste*, neither of which is a comparison.
 *
 * `body` is assembled from `t()` output and interpolated taxonomy names, each escaped at
 * the point it is built -- see the three builders below.
 */
function resultExplanation(body, id, label) {
  const tooltipId = `result-explanation-${id}`
  return `<div class="result-explanation"><button class="result-explanation__trigger" type="button" aria-label="${escapeHtml(label)}" aria-describedby="${escapeHtml(tooltipId)}">i</button><div class="result-explanation__body" id="${escapeHtml(tooltipId)}" role="tooltip">${body}</div></div>`
}

/**
 * One metric's explanation, and **it does not restate the formula.**
 *
 * The first draft carried a per-code table -- `{co2e, ch4, water, cost}` -- each entry
 * spelling out `waste (kg) × [production factor + destination factor]`. Two things are
 * wrong with that and both are named by the contract:
 *
 * * §7.6 rule 5: *iterate over metrics; never hard-code their codes.* A metric a staff
 *   member adds gets no explanation, or an untranslated fallback, and nothing says so.
 * * **The expression lives in the `metric` row**, and staff edit it through the admin
 *   panel. A formula copied into a call site is a second copy that goes stale silently:
 *   the tooltip keeps asserting a calculation the engine has stopped performing, and no
 *   test in the tree compares the two.
 *
 * What is said instead is the engine's own invariant, which the contract fixes and staff
 * cannot edit away: *a formula computes one line, and the engine sums the lines.* That is
 * true of every metric, including one added tomorrow. The published formulas and factors
 * are on the methodology page, which is where a reader who wants the expression should
 * be sent -- §6.3 publishes them there precisely so this page does not have to.
 */
function metricExplanation(metric, definition, code) {
  const name = definition?.name || code
  const unit = metricUnit(metric, definition)
  return `<p>${escapeHtml(t('%(metric)s from the food you entered, in %(unit)s.', { metric: name, unit }))}</p>`
    + `<p>${escapeHtml(t('Each amount you entered is multiplied by the factors published for its food type and its destination, and the results are added together. The published factors and formulas are on the methodology page.'))}</p>`
}

function massExplanation() {
  return `<p>${escapeHtml(t('The total weight of the food waste you entered, added across every destination.'))}</p>`
}

/**
 * The share's explanation, and the one card where the placeholder banner does not apply.
 *
 * §4.6: this figure is `current.total_kg ÷ total_input_kg` -- two masses the visitor
 * typed, with no factor anywhere in the division. Every other number on this page is
 * `qty_kg × a factor` and the factor set is mock (O-1). A reader who distrusts this one
 * *because of* the banner is distrusting the number the banner was never about, so the
 * card says so itself.
 */
function shareExplanation() {
  return `<p>${escapeHtml(t('The waste you entered as a percentage of the food you said you handled.'))}</p>`
    + `<p>${escapeHtml(t('This is a ratio of two figures you typed, so the placeholder factors do not affect it.'))}</p>`
}

function summaryCards(totals, taxonomy) {
  const metrics = totals.current?.metrics || {}
  const impactCards = Object.entries(metrics).filter(([code]) => code !== MASS_METRIC).map(([code, metric]) => {
    const definition = findByCode(taxonomy.metrics, code)
    const precision = Number(metric.display_precision ?? definition?.display_precision ?? 2)
    const total = number(metric.total)
    const name = definition?.name || code
    const unit = metricUnit(metric, definition)
    return `<article class="result-card"><p class="result-label">${escapeHtml(name)}</p><p class="result-value${negativeClass(total)}">${formatNumber(total, precision)} ${escapeHtml(unit)}</p>${resultExplanation(metricExplanation(metric, definition, code), code, t('How this figure was worked out'))}</article>`
  }).join('')
  const totalKg = number(totals.total_kg)
  const share = productionShareText(totals)
  const shareNote = share.note ? `<p class="result-note">${escapeHtml(share.note)}</p>` : ''
  return `<article class="result-card primary-result"><p class="result-label">${escapeHtml(t('Total food waste'))}</p><p class="result-value">${formatNumber(totalKg, 2)} kg</p><p class="result-note">${formatNumber(kgToTonnes(totals.total_kg), 3)} ${escapeHtml(t('tonnes'))}</p>${resultExplanation(massExplanation(), 'mass', t('How this figure was worked out'))}</article>${impactCards}<article class="result-card"><p class="result-label">${escapeHtml(t('Percentage waste'))}</p><p class="result-value">${escapeHtml(share.value)}</p>${shareNote}${resultExplanation(shareExplanation(), 'production-share', t('How this figure was worked out'))}</article>`
}

// §3: `label` is `label_template` with the equivalence's own value already interpolated and
// formatted by the engine, and no consumer re-derives it. The map this replaced summed
// `value` across entries and then rendered three hard-coded English labels of its own for
// three hard-coded codes, so a new equivalence never appeared and the client's approved
// wording was overridden (§7.6.5).
//
// **§6.2 (v1.71): this array is not every equivalence the factor set publishes.** Where
// several rows share a `family` they are rungs of one ladder -- the same comparison at
// several sizes -- and the response carries the ONE the engine chose for this submission,
// so that a small result reads "500 ten-minute showers" instead of "0 Olympic swimming
// pools". Nothing here selects, filters or re-orders: the array is already the answer, in
// the order it should be drawn. A `family` is not on the wire and must not be inferred --
// which rung a reader sees is a server-side decision (§7.6 rule 1), exactly as the number
// inside the sentence is.
function equivalences(totals) {
  const rows = totals.current?.equivalences || []
  if (!rows.length) return `<p class="empty-state">${escapeHtml(t('Tangible equivalents are available once approved conversion factors are supplied.'))}</p>`
  return `<div class="equivalent-grid">${rows.map(row => `<article><h3>${escapeHtml(row.label)}</h3>${equivalenceBasis(row, totals)}</article>`).join('')}</div>`
}

// §7.6 rule 1: nothing here is arithmetic. `value_per_unit_display` and the
// metric total arrive already formatted by the engine, and this only lays them
// out. §7.7.7: `description` is staff-typed and is printed verbatim in every
// language, exactly as `preset.label` is -- only the connective words around
// it are translated, and here there are none.
//
// **v1.80 (#127): what this panel holds is one sentence, and `source_note` is
// not it.** The client, using the tool as a tester, read one to four sentences
// of audit provenance behind every `?` and said it meant nothing there. So the
// panel prints `description` -- one staff sentence saying what the comparison
// MEANS -- and when there is none it prints NOTHING. It must never fall back
// to `source_note`: that fallback is the long version coming back, which is
// the whole of what was removed. `source_note` is still published by
// `GET /factors` and still rendered by `methodology.js`, which is where a
// reader who wants to check where a conversion factor came from goes.
//
// **The standing per-equivalence caveat went with it** -- "The conversion
// factor comes from the client. The total it is applied to comes from
// placeholder factors." It is safe to remove because it was never the
// obligation: §7.6 rule 2's obligation is the PAGE-LEVEL placeholder banner,
// mandatory and non-dismissible while `is_mock`, on this view and on every
// export. Nothing here touches it. The removed sentence is also the one whose
// unconditional version once made a real published factor set describe itself
// as placeholder data (`test_a_real_factor_set_carries_no_warning`), so this
// function no longer needs to know whether the set is mock at all -- and a
// parameter kept for a caller that no longer exists is a parameter somebody
// finds a use for.
function equivalenceBasis(row, totals) {
  const source = totals.current?.metrics?.[row.source_metric]
  const total = source ? `${formatNumber(source.total, source.display_precision)} ${source.unit}` : ''
  const description = row.description
    ? `<p class="equivalent-basis__note">${escapeHtml(row.description)}</p>`
    : ''
  return `<details class="equivalent-basis">
  <summary aria-label="${escapeHtml(t('How this comparison was worked out'))}">?</summary>
  <div class="equivalent-basis__body">
    <dl>
      <div><dt>${escapeHtml(t('Total'))}</dt><dd>${escapeHtml(total)}</dd></div>
      <div><dt>${escapeHtml(t('Per unit'))}</dt><dd>&times; ${escapeHtml(row.value_per_unit_display)}</dd></div>
      <div><dt>${escapeHtml(row.name)}</dt><dd>= ${escapeHtml(formatNumber(row.value, 0))}</dd></div>
    </dl>
    ${description}
  </div>
</details>`
}

// §6.2: the breakdown is rendered from `entries[]`, and nothing on this screen is added
// together. The accumulator this replaced merged rows whose labels collided — two entries
// in one sector, or the same destination under two entries — by summing engine-computed
// metric totals in the browser (§7.6.1). It is also the wrong presentation: 1,200 kg to
// landfill from processing and 1,200 kg from primary production draw different upstream
// factors and are genuinely two rows, which is the whole reason a multi-entry calculation
// was worth making. A single combined view, if the client asks for one, arrives as a field
// the engine fills with a stated aggregation rule — not as a loop here.
const metricCells = scenario => Object.fromEntries(Object.entries(scenario?.metrics || {})
  .map(([code, metric]) => [code, { total: metric.total, unit: metric.unit, display_precision: metric.display_precision }]))

const sectorName = (entry, response, taxonomy) => {
  const code = response.sector ?? entry.sector
  return findByCode(taxonomy.sectors, code)?.name || code
}

// One row per destination of one scenario, every figure read from that scenario's own
// result: `qty_kg` and each metric's `value` come from `by_destination`. Called both on
// one entry's `current` (§6.2 populates it per entry) and on `totals.current` (populated
// per metric since v1.48, §3 rule 2) — the two calls read the same shape, one entry-scale
// and one rolled up across every entry, and `code` is kept on the row so a caller can match
// one scenario's row to the other's by destination rather than by re-reading `label`.
function destinationRows(scenario, taxonomy) {
  const rows = new Map()
  for (const [code, metric] of Object.entries(scenario.metrics || {})) {
    for (const line of metric.by_destination || []) {
      const row = rows.get(line.destination) || {
        code: line.destination,
        label: findByCode(taxonomy.destinations, line.destination)?.name || line.destination,
        kilograms: number(line.qty_kg),
        metrics: {},
      }
      row.metrics[code] = { total: line.value, unit: metric.unit, display_precision: metric.display_precision }
      rows.set(line.destination, row)
    }
  }
  return [...rows.values()]
}

// The food leaf under a destination-first stage.
//
// **Two leaves could render byte-identical here, and that is a wrong-number defect.**
// This returned `Standard mix / not specified` both for a NULL `food_category` and for
// the `standard_mix` category. Step 2 offers those as two separate boxes - §5.4 requires
// it, because "the visitor did not break their waste down by type" is not the same
// answer as "the visitor chose the standard mix" - and a chain that ticks both is two
// leaves with different masses and different numbers under one identical row label.
//
// `leafDisplayName` is now the only thing that names a leaf, on this page and on the
// five other surfaces that show one, so the two can no longer collide and a leaf can no
// longer be called one thing on step 3 and another on the results page.
//
// The response's own `food_category` wins over the entry's, as before: §6.2 echoes what
// the engine resolved, and an entry paired with the wrong response is a defect this
// label should show rather than hide.
const stageFoodLabel = (entry, response, taxonomy) => leafDisplayName(
  { foodCategory: response.food_category ?? entry.foodCategory, foodItem: entry.foodItem ?? null },
  taxonomy,
)

// Destination first, then the entries that share it, then each entry's own food category —
// the transpose the client asked for of the per-entry sections this replaced. The group
// total is `totalsRow`, read whole from `totals.current.by_destination` (§7.6.1: nothing
// here adds the entries' rows together); the per-entry rows nested under it are each
// entry's own figures, computed once per entry and matched to a group by destination code.
function destinationGroups(entryResults, totalsRows, taxonomy) {
  const perEntry = entryResults.map(({ entry, response }) => ({
    entry,
    response,
    rows: destinationRows(response.current || {}, taxonomy),
  }))
  return totalsRows
    .map(totalsRow => {
      const stages = perEntry.flatMap(({ entry, response, rows }) => {
        const row = rows.find(candidate => candidate.code === totalsRow.code)
        if (!row) return []
        return [{
          label: sectorName(entry, response, taxonomy),
          foodLabel: stageFoodLabel(entry, response, taxonomy),
          row,
        }]
      })
      return { code: totalsRow.code, label: totalsRow.label, totalsRow, stages }
    })
    .filter(group => group.stages.length)
}

function breakdowns(entryResults, totals, taxonomy) {
  const stage = []
  const food = []
  // **Which sectors appear more than once.** A chain that named three food categories is
  // three entries in one sector, so the stage tab would print three rows labelled
  // "Processing" differing only in their numbers — and the accumulator that used to merge
  // colliding labels was removed deliberately (it summed engine-computed figures in the
  // browser, §7.6.1). They must not be merged, so they have to be told apart: the food is
  // added to the label exactly where the sector alone is ambiguous, and nowhere else, so
  // a submission of one entry per sector reads as it always did.
  const sectorCounts = new Map()
  for (const { entry, response } of entryResults) {
    const code = response.sector ?? entry.sector
    sectorCounts.set(code, (sectorCounts.get(code) || 0) + 1)
  }
  for (const { entry, response } of entryResults) {
    const scenario = response.current || {}
    const metrics = metricCells(scenario)
    const kilograms = number(scenario.total_kg)
    const sector = sectorName(entry, response, taxonomy)
    const shared = (sectorCounts.get(response.sector ?? entry.sector) || 0) > 1
    stage.push({ label: shared ? t('%(sector)s — %(food)s', { sector, food: stageFoodLabel(entry, response, taxonomy) }) : sector, kilograms, metrics })
    const foodCode = response.food_category ?? entry.foodCategory
    const foodDefinition = findByCode(taxonomy.food_categories, foodCode)
    if (foodCode && !foodDefinition?.is_standard_mix) food.push({ label: foodDefinition?.name || foodCode, kilograms, metrics })
  }
  const totalsRows = destinationRows(totals.current || {}, taxonomy)
  const groups = destinationGroups(entryResults, totalsRows, taxonomy)
  // The note explains a relationship between two levels of figures, so it is printed
  // only where that relationship exists: a group fed by one entry now prints its
  // figures once (see `destinationTree`), and with one entry — the commonest journey
  // by far — *every* group is such a group. Printing the sentence there had the page
  // saying the rows "add up to the total rather than repeat it" directly beneath rows
  // that repeated it exactly.
  const anySplit = groups.some(group => group.stages.length > 1)
  return {
    stage: { sections: [{ rows: stage }] },
    destination: groups.length
      ? { groups, note: anySplit ? t("The figure beside each destination is the engine's own cross-entry total. The rows beneath it are each entry's own — they can draw different upstream factors, which is why they add up to the total shown rather than repeat it.") : '' }
      : { unavailable: t('Waste-destination breakdown is not available because no destination data was provided.') },
    food: food.length ? { sections: [{ rows: food }] } : { unavailable: t('Food-type breakdown is not available because no food category data was provided.') },
  }
}

function metricCell(row, code, taxonomy) {
  const cell = row.metrics[code]
  if (!cell) return t('Not available')
  const definition = findByCode(taxonomy.metrics, code)
  const total = number(cell.total)
  const text = `${formatNumber(total, Number(cell.display_precision ?? definition?.display_precision ?? 2))} ${escapeHtml(metricUnit(cell, definition))}`
  return Number.isFinite(total) && total < 0 ? `<span class="value-negative">${text}</span>` : text
}

/**
 * The metric columns of a breakdown tab, taken from the response rather than from this file.
 *
 * §7.6.5 and §2.1: adding a metric is meant to cost one `INSERT` and one formula. The three
 * columns hard-coded here — `CO₂e / Cost / Water` — cost a code change instead, and they had
 * already silently dropped `ch4`, a metric that is in §2.1's own example list and in
 * `tests/fixtures/taxonomy.json`.
 *
 * The order is the response's own key order, not a sort applied here: §4.1 defines
 * `FactorBundle.metrics` as sorted by `sort_order`, and the engine iterates it in that order
 * to build `metrics`, so JSON key order already is display order. Codes are collected across
 * every section of the tab so all its tables carry the same columns, and a row that is
 * missing one renders `metricCell`'s "Not available" rather than shifting the row.
 */
function metricColumns(sections) {
  const codes = []
  for (const section of sections) {
    for (const row of section.rows || []) {
      for (const code of Object.keys(row.metrics || {})) {
        if (code !== MASS_METRIC && !codes.includes(code)) codes.push(code)
      }
    }
  }
  return codes
}

const metricName = (code, taxonomy) => findByCode(taxonomy.metrics, code)?.name || code

const widestRow = rows => rows.reduce((max, row) => Math.max(max, Number.isFinite(row.kilograms) ? Math.abs(row.kilograms) : 0), 0)

function breakdownTable(section, tabLabel, taxonomy, widest, columns) {
  // Bar width only. This is a bar's width relative to the widest bar on the tab — scaled
  // across every section, so two per-entry sections stay comparable to each other — and not
  // a figure printed on the page. The percentage-of-total that stood here was a number the
  // engine never produced, and §6.2 defines no share for an entry or a destination.
  const bars = section.rows.map(row => {
    const width = widest && Number.isFinite(row.kilograms) ? Math.min(Math.abs(row.kilograms) / widest * 100, 100) : 0
    return `<div class="bar-row"><div><strong>${escapeHtml(row.label)}</strong><span>${formatNumber(row.kilograms, 2)} kg</span></div><div class="bar-track"><span style="width:${width}%"></span></div></div>`
  }).join('')
  const caption = section.label
    ? t('%(tab)s data — %(entry)s', { tab: tabLabel, entry: section.label })
    : t('%(tab)s data', { tab: tabLabel })
  const heading = section.label ? `<h3 class="breakdown-entry-heading">${escapeHtml(section.label)}</h3>` : ''
  const head = columns.map(code => `<th scope="col">${escapeHtml(metricName(code, taxonomy))}</th>`).join('')
  const body = section.rows.map(row => `<tr><th scope="row">${escapeHtml(row.label)}</th><td>${formatNumber(row.kilograms, 2)} kg</td>${columns.map(code => `<td>${metricCell(row, code, taxonomy)}</td>`).join('')}</tr>`).join('')
  return `<div class="breakdown-entry">${heading}<div class="bar-list" aria-hidden="true">${bars}</div><div class="table-scroll" tabindex="0"><table><caption>${escapeHtml(caption)}</caption><thead><tr><th scope="col">${escapeHtml(t('Category'))}</th><th scope="col">${escapeHtml(t('Waste amount'))}</th>${head}</tr></thead><tbody>${body}</tbody></table></div></div>`
}

// One destination group's figures beside its heading or beside a stage: every metric but
// `mass` (held out for the reason `metricLines` holds it out — it is the kilogram figure
// printed next to it), read through the same `metricCell` the other two tabs use so a
// negative `downstream` offset (§7.6.6) is marked here exactly as it is there.
function destinationRowFigures(row, taxonomy) {
  return Object.keys(row.metrics || {})
    .filter(code => code !== MASS_METRIC)
    .map(code => `<span>${escapeHtml(metricName(code, taxonomy))}: ${metricCell(row, code, taxonomy)}</span>`)
    .join('')
}

// The destination-first tree: one `.destination-group` per destination, carrying the
// engine's own rolled-up total (`data-destination` names the code so a caller — this
// file's tests among them — can find one group without depending on render order); inside
// it, one `.destination-group__stage` per entry that used that destination, each showing
// that entry's own sector and figures; inside that, the one `.destination-group__food` its
// food category is. Three levels for the three questions the client asked in that order:
// which destination, which stage of the supply chain sent it there, what food it was.
function destinationTree(groups, taxonomy) {
  return groups.map(group => {
    // **One contributing entry means one set of figures.** §3 rule 2 builds the
    // group's total by rolling the entries' own `by_destination` rows up, so when
    // exactly one entry used this destination the roll-up *is* that entry's row —
    // identical by construction, at every metric. Printing both put the same
    // numbers on screen twice under a note saying they were different, in the
    // default single-entry journey. The stage and its food category still render:
    // "which stage sent it there, what food it was" are two of the three questions
    // this tree exists to answer, and neither is a repeated figure.
    const repeated = group.stages.length === 1
    const stages = group.stages.map(stage => `<li class="destination-group__stage"><div class="destination-group__stage-row"><strong>${escapeHtml(stage.label)}</strong>${repeated ? '' : `<span>${formatNumber(stage.row.kilograms, 3)} kg</span>`}</div>${repeated ? '' : `<div class="destination-group__stage-figures">${destinationRowFigures(stage.row, taxonomy)}</div>`}<ul class="destination-group__foods"><li class="destination-group__food">${escapeHtml(stage.foodLabel)}</li></ul></li>`).join('')
    return `<article class="destination-group" data-destination="${escapeHtml(group.code)}"><div class="destination-group__header"><h3 class="destination-group__name">${escapeHtml(group.label)}</h3><p class="destination-group__total">${formatNumber(group.totalsRow.kilograms, 3)} kg</p></div><div class="destination-group__figures">${destinationRowFigures(group.totalsRow, taxonomy)}</div><ol class="destination-group__stages">${stages}</ol></article>`
  }).join('')
}

function breakdownSection(state, entryResults) {
  const totals = state.result?.totals || {}
  const allBreakdowns = breakdowns(entryResults, totals, taxonomyForResult(state))
  const active = state.resultBreakdownTab in TAB_LABELS ? state.resultBreakdownTab : 'stage'
  const current = allBreakdowns[active]
  let panel
  if (current.unavailable) {
    panel = `<p class="empty-state">${escapeHtml(current.unavailable)}</p>`
  } else if (active === 'destination') {
    // The tree, not `breakdownTable`: a destination's stages and foods are two more levels
    // than that table's single row of columns has room for, and its bars and per-tab metric
    // columns describe a flat list that this shape no longer is.
    panel = `${current.note ? `<p class="breakdown-note">${escapeHtml(current.note)}</p>` : ''}${destinationTree(current.groups, taxonomyForResult(state))}`
  } else {
    const scale = widestRow(current.sections.flatMap(section => section.rows))
    const columns = metricColumns(current.sections)
    panel = `${current.note ? `<p class="breakdown-note">${escapeHtml(current.note)}</p>` : ''}${current.sections.map(section => breakdownTable(section, t(TAB_LABELS[active]), taxonomyForResult(state), scale, columns)).join('')}`
  }
  return `<section class="results-section" id="breakdown-section" aria-labelledby="breakdown-title"><div class="result-section-heading"><span class="section-number">03</span><div><h2 id="breakdown-title">${escapeHtml(t('Breakdown by category'))}</h2><p>${escapeHtml(t('Explore how the recorded waste is distributed.'))}</p></div></div><div class="breakdown-tabs" role="tablist" aria-label="${escapeHtml(t('Waste breakdown'))}">${Object.entries(TAB_LABELS).map(([key, label]) => `<button id="breakdown-tab-${key}" type="button" role="tab" data-action="breakdown-tab" data-tab="${key}" aria-selected="${active === key}" aria-controls="breakdown-panel-${key}" tabindex="${active === key ? 0 : -1}">${escapeHtml(t(label))}</button>`).join('')}</div><div id="breakdown-panel-${active}" class="breakdown-panel" role="tabpanel" aria-labelledby="breakdown-tab-${active}" tabindex="0">${panel}</div></section>`
}

// One metric, worded the way the summary card words it: the taxonomy's name, the figure at
// the metric's own `display_precision`, and the unit that travelled with the figure. Nothing
// is recomputed and nothing is re-scaled — §7.6.1 leaves no layer that could.
function metricText(code, cell, taxonomy) {
  const definition = findByCode(taxonomy.metrics, code)
  const precision = Number(cell.display_precision ?? definition?.display_precision ?? 2)
  return `${definition?.name || code}: ${formatNumber(number(cell.total), precision)} ${metricUnit(cell, definition)}`.trimEnd()
}

// The response's own key order, which §4.1 already sorted by `sort_order`. `mass` is held
// out for the reason `summaryCards` holds it out — it is the "Total food waste" line above,
// printed twice otherwise — and that single-code exclusion is not the hard-coded list
// §7.6.5 forbids: every other metric the response carries is printed, whatever it is.
const metricLines = (scenario, taxonomy, indent) => Object.entries(scenario?.metrics || {})
  .filter(([code]) => code !== MASS_METRIC)
  .map(([code, cell]) => `${indent}${metricText(code, cell, taxonomy)}`)

// The destination breakdown table, in text: one line per destination of one entry, carrying
// that destination's mass and every metric the engine computed against it.
const destinationImpactLines = (scenario, taxonomy) => destinationRows(scenario, taxonomy).map(row => {
  const figures = Object.entries(row.metrics)
    .filter(([code]) => code !== MASS_METRIC)
    .map(([code, cell]) => metricText(code, cell, taxonomy))
  return `  - ${row.label} (${formatNumber(row.kilograms, 3)} kg): ${figures.join('; ') || t('no impact figures were returned')}`
})

/**
 * §4.5's saving, in text, in the two lines the comparison screen shows it in.
 *
 * Same fields and the same `null`-means-absent rule as `moneyLines` — **on the same
 * three-state terms as `moneyFieldText` gives every other money field, not a bare
 * `hasValue` check.** `saving_nzd` used to be tested with `hasValue` alone, which
 * printed nothing at all when the state was `incomplete`, while the PDF (`api/
 * pdf_render.py::_money_rows`, which reads the same `data_state.saving_nzd`) printed
 * the shared "Not every entry supplied this figure, so it cannot be totalled."
 * sentence — the exact disagreement v1.50's own change-log item 4 rules out. The
 * caveat about the nominal rate only makes sense beside an actual figure, so it is
 * appended only when the state produced one, not when it produced the sentence.
 */
function savingLines(totals) {
  const saving = totals?.money?.saving_nzd
  const text = moneyFieldText(totals?.money, totals?.data_state, 'saving_nzd', nzd)
  if (text === null) return []
  const lines = [`  - ${t('Value of food not wasted at all')}: ${text}`]
  if (hasValue(saving)) {
    lines.push(`    ${t('This assumes an even value per kilogram within each entry you priced, the way a box of produce is costed as a whole - not a measured price, and not an average taken across every entry.')}`)
  }
  return lines
}

// The comparison screen, in text, and only when one was run. `net_benefit` is read from the
// response — §6.2 computes `current − alternative` per metric and the browser must not
// subtract the two itself (§7.6.1), which is the defect `improvement.js` already had removed
// from the screen and which this file must not reintroduce on its way to a file.
function comparisonLines(state) {
  const totals = state.improvementResult?.totals
  if (!totals?.alternative) return []
  const netBenefit = totals.net_benefit || {}
  const lines = []
  for (const [code, cell] of Object.entries(totals.current?.metrics || {})) {
    if (code === MASS_METRIC) continue
    const improved = totals.alternative?.metrics?.[code]
    if (!improved) continue
    // **`state.taxonomy` and not `taxonomyForResult(state)`, and this is the one read in
    // this file where that is right.** These lines render `state.improvementResult`, which
    // Compare Impact computed on *this* page load under the currently published set — so
    // the fresh vocabulary is the one that named its rows. The thirteen reads that render
    // `state.result` are the other case; see `taxonomyForResult` in `state.js`.
    const definition = findByCode(state.taxonomy.metrics, code)
    const precision = Number(cell.display_precision ?? definition?.display_precision ?? 2)
    const unit = metricUnit(cell, definition)
    const difference = number(netBenefit[code])
    // The screen's own wording, from `changeCopy`: a positive `net_benefit` is a saving.
    const change = !Number.isFinite(difference)
      ? t('Not available')
      : Math.abs(difference) < 1e-9
        ? t('no change')
        : `${formatNumber(Math.abs(difference), precision)} ${unit} ${difference > 0 ? t('saved') : t('increase')}`.trimEnd()
    const figure = value => `${formatNumber(number(value), precision)} ${unit}`.trimEnd()
    lines.push(`  - ${definition?.name || code}: ${figure(cell.total)} → ${figure(improved.total)} (${change})`)
  }
  // §4.5's saving is a figure about *this* comparison — `rate x diverted mass`, and
  // `diverted` is `current − alternative` — so it is written here rather than beside the
  // three current-scenario money figures `moneyLines` prints. It is also why the heading
  // is printed when `lines` is empty but a saving is present: a response can carry a
  // saving and no comparable metric, and dropping the heading would strand the figure.
  const saving = savingLines(totals)
  if (!lines.length && !saving.length) return []
  return ['', t('Improved scenario (Current → Improved)'), ...lines, ...saving]
}

/**
 * One destination row of one entry, in the unit **that row** was measured in.
 *
 * `${qtyInput} ${entry.totalUnit}` stood here, and since a row carries its own unit that
 * was a figure labelled with somebody else's: 500 kg entered against an entry measured in
 * tonnes exported as `500.00 tonnes`, and half a tonne against an entry measured in
 * kilograms exported as `0.50 kilograms`. This file's own note says the report exists to
 * be attached to an email and believed, which is why a wrong label on it is not a cosmetic
 * defect — it is a thousandfold error in a document written to be trusted.
 *
 * A row measured in anything but kilograms carries the kilograms too, the way
 * `wasteAmountLine` does for a container: `0.50 tonnes` is what the visitor said and
 * `(500.000 kg)` is what was calculated from it, and a reader holding only this file needs
 * both to check one against the other. `kg` is not translated — §7.7.7, metric units are
 * international notation — and a container's `label` is staff-typed and published as
 * written.
 */
function destinationLine(line, entry, taxonomy) {
  const unit = line.unit || entry.totalUnit
  const destination = findByCode(taxonomy.destinations, line.destination)?.name || line.destination
  const amount = typed(line.qtyInput).toFixed(2)
  const presets = taxonomy.unit_presets || []
  const kilograms = rowKgString(line.qtyInput, unit, presets)
  if (isPresetUnit(unit)) {
    const preset = findByCode(presets, presetUnitCode(unit))
    return `  - ${destination}: ${amount} × ${preset?.label || presetUnitCode(unit)}${kilograms ? ` (${kilograms} kg)` : ''}`
  }
  if (unit === 'tonnes') return `  - ${destination}: ${amount} ${t('tonnes')} (${kilograms} kg)`
  return `  - ${destination}: ${amount} ${t('kilograms')}`
}

/**
 * One entry's waste amount, as the visitor gave it.
 *
 * Mass mode reads the field the visitor typed in. Container mode reads the count and the
 * container, and appends the kilograms `entryTotal` derives — never the kilograms alone,
 * because the number a reader can check against their own bins is the count.
 */
function wasteAmountLine(entry, taxonomy) {
  if (entry.measureMode !== 'container') {
    return `${t('Waste amount')}: ${typed(entry.totalAmount).toFixed(2)} ${t(entry.totalUnit === 'tonnes' ? 'tonnes' : 'kilograms')}`
  }
  const preset = findByCode(taxonomy.unit_presets || [], entry.unitPreset)
  const kilograms = entryTotal(entry, taxonomy.unit_presets).amount
  const container = preset?.label || entry.unitPreset || ''
  return `${t('Waste amount')}: ${typed(entry.unitCount).toFixed(2)} × ${container} (${kilograms} kg)`
}

/**
 * The plain-text report, built and returned rather than downloaded.
 *
 * Split out of `downloadResults` because it is the half worth asserting on: the export was
 * shipping the total mass, the entries and the factor version and **not one output figure**
 * — no greenhouse gas, no methane, no water, no cost — under the file name
 * `food-waste-impact-results.txt`. A results export with no results is the file somebody
 * attaches to an email, and every number in it now comes from `state.result`, which is the
 * engine's, never from arithmetic performed here (§7.6.1).
 *
 * @param {object} state
 * @returns {string}
 */
export function buildResultsReport(state) {
  const totals = state.result?.totals || {}
  const totalKg = number(totals.total_kg)
  const summary = metricLines(totals.current, taxonomyForResult(state), '  - ')
  // §3: `label` is the engine's own sentence, copied verbatim. The lines under
  // it are the same facts the page shows behind its disclosure -- paper has no
  // "open" gesture, and this file is the copy most likely to be forwarded to
  // someone who will challenge the figure (§6.3).
  //
  // v1.80 (#127): `description` where `Basis: <source_note>` and the mock
  // caveat used to be, printed only when it is there. The three surfaces say
  // the same thing or they say nothing; `source_note` is not a fallback here
  // any more than it is on screen. The placeholder obligation is `notice` /
  // `DEMONSTRATION_NOTICE` a few lines below, gated on `is_mock` and
  // untouched.
  const equivalents = (totals.current?.equivalences || []).flatMap(row => {
    const source = totals.current?.metrics?.[row.source_metric]
    const total = source ? `${formatNumber(source.total, source.display_precision)} ${source.unit}` : ''
    return [
      `  - ${row.label}`,
      `      ${t('Total')}: ${total}  ${t('Per unit')}: x ${row.value_per_unit_display}`,
      `      ${row.name}: ${formatNumber(row.value, 0)}`,
      ...(row.description ? [`      ${row.description}`] : []),
    ]
  })
  const entryLines = (state.result?.entry_results || []).flatMap(({ entry, response }, index) => {
    const sector = findByCode(taxonomyForResult(state).sectors, entry.sector)
    const destinations = entry.current.filter(line => typed(line.qtyInput) > 0).map(line => destinationLine(line, entry, taxonomyForResult(state)))
    const scenario = response?.current || {}
    const impact = metricLines(scenario, taxonomyForResult(state), '  - ')
    const byDestination = destinationImpactLines(scenario, taxonomyForResult(state))
    return [
      t('Entry %(number)s: %(sector)s', { number: index + 1, sector: sector?.name || entry.sector }),
      // One leaf, one name (`leafDisplayName`). This read `Not provided` for a
      // chain that named no food while every screen the reader had just left said
      // `Not broken down by type`, and the file is the copy that gets forwarded.
      `${t('Food type')}: ${leafDisplayName(entry, taxonomyForResult(state))}`,
      // A container entry has no `totalAmount` — the visitor said "two 240 L wheelie
      // bins", not "139.20 kilograms" — so reading that field printed **0.00 kilograms**
      // into a report whose whole job is to be attached to an email and believed. The
      // report says what was entered and the kilograms it came to, in that order, and
      // `entryTotal` is the same reconciliation the form uses rather than a second copy
      // of it. `preset.label` is staff-typed and is published as written (§7.7.7).
      wasteAmountLine(entry, taxonomyForResult(state)),
      `${t('Destinations')}:`, ...destinations,
      ...(impact.length ? [`${t('Impact for this entry')}:`, ...impact] : []),
      ...(byDestination.length ? [`${t('Impact by destination')}:`, ...byDestination] : []),
      '',
    ]
  })
  // §7.6.2: the placeholder notice is conditional on `is_mock`, on **every** export, and it
  // was appended unconditionally. That reads as correct while every factor set is mock and
  // inverts the day a real one is published — a client-facing report that disclaims real
  // data is the more damaging half of the same bug. The factor version replaces it as the
  // line that says which numbers these are, so a real export is not left saying nothing.
  const notice = state.result?.factor_set?.is_mock ? [t(DEMONSTRATION_NOTICE)] : []
  // Contract v1.59, printed beside `notice` rather than inside each entry's
  // block: one food appears once however many entries named it, which is the
  // same list the screen shows, from the same function.
  const averaged = categoryAverageLines(state, '')
  const averagedBlock = averaged.length ? ['', t('Food category average'), ...averaged] : []
  // The export leaves the browser and is read by somebody who did not choose the
  // language it was written in, so a machine-translated interface has to say so on
  // the file as well as on the screen it came from.
  const translationNotice = isMachineTranslated() ? ['', MACHINE_TRANSLATION_NOTICE] : []
  // Item ⑦: a label, printed once near the top of the file, same as on screen — no
  // figure below it is scaled by the period (contract v1.48).
  const period = periodLine(state)
  // §4.6: the same three states `summaryCards` renders as the card, worded the same way,
  // so a visitor reading the page and the file downloaded from it sees the same sentence.
  const share = productionShareText(totals)
  return [
    t('Food Waste Impact Calculator — Results'),
    '',
    ...(period ? [period, ''] : []),
    `${t('Total food waste')}: ${formatNumber(totalKg, 2)} kg`,
    `${t('Total food waste')}: ${formatNumber(kgToTonnes(totals.total_kg), 3)} ${t('tonnes')}`,
    '',
    t('Impact summary'),
    ...(summary.length ? summary : [`  - ${t('No impact metrics were returned.')}`]),
    '',
    t('Tangible equivalents'),
    ...(equivalents.length ? equivalents : [`  - ${t('Tangible equivalents are available once approved conversion factors are supplied.')}`]),
    ...moneyLines(totals),
    ...comparisonLines(state),
    '',
    ...entryLines,
    `${t('Factor version')}: ${state.result?.factor_set?.version_label || t('Not supplied')}`,
    ...notice,
    ...averagedBlock,
    `${t('Percentage waste')}: ${share.value}${share.note ? `. ${share.note}` : ''}`,
    ...translationNotice,
  ].join('\n')
}

/**
 * The download's file name, stamped with local time — shared by both downloads
 * (Task 3, 2026-08-31) so the stamp's own convention exists in exactly one place.
 *
 * A fixed name meant every export after the first arrived as
 * `food-waste-impact-results (1).txt`, and the browser decides that suffix, not
 * this code - so the order is the download order, not the calculation order, and
 * nothing on the file says which scenario it holds. Somebody comparing two runs
 * has to open both to tell them apart.
 *
 * Sortable order (year first, zero-padded), so a directory listing is
 * chronological. Local time rather than UTC: the stamp exists to be recognised
 * by the person who pressed the button, and they are reading their own clock.
 *
 * `now` is a parameter because a function that reads the system clock cannot be
 * asserted on - the same reason `engine.calculate` takes no clock.
 *
 * `ext` and `unique` are both optional and both new: the text download still
 * calls this with neither, so its own name is untouched. The PDF passes both —
 * see `downloadPdf` — because the stamp alone has one-second resolution and a
 * fixed name is exactly what made a second PDF land as `... (1).pdf`; the client
 * asked for a timestamp *and* something unique per file, not the timestamp
 * alone, so `unique` is a real per-download identifier rather than a second
 * clock reading.
 */
export function exportFilename(now = new Date(), { ext = 'txt', unique } = {}) {
  const pad = (value) => String(value).padStart(2, '0')
  const stamp = [
    now.getFullYear(),
    pad(now.getMonth() + 1),
    pad(now.getDate()),
  ].join('-') + '-' + [
    pad(now.getHours()),
    pad(now.getMinutes()),
    pad(now.getSeconds()),
  ].join('')
  const suffix = unique ? `-${unique}` : ''
  return `food-waste-impact-results-${stamp}${suffix}.${ext}`
}

/**
 * A short identifier that tells two downloads apart without depending on the
 * clock — `exportFilename`'s stamp alone cannot, at one-second resolution.
 * `crypto.randomUUID` is available in every browser this interface supports
 * (it needs only a secure context, and `localhost` qualifies for it exactly as
 * the deployed origin will); the fallback covers an embedded or older engine
 * that Playwright or a real visitor could still present.
 */
function downloadUniqueId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID().replace(/-/g, '').slice(0, 8)
  }
  return Math.random().toString(36).slice(2, 10)
}

/**
 * The plain-text download, on `data-action="download-results"` — the original export.
 * PR #46 replaced this implementation with a hand-rolled PDF rather than adding one beside
 * it; the PDF now comes from the server (`downloadPdf` below), so the two formats are two
 * buttons, rendered together as one choice rather than one in the step navigation and one
 * trailing after it (Task 3, 2026-08-31 — see `renderResults`' own `.download-actions`).
 *
 * The link is attached to the document before `click()` and removed after: a detached
 * anchor is not reliably actionable in Firefox. Both details, and the deferred revoke
 * below, are PR #46's and are kept on their merit.
 */
export function downloadResults(state) {
  const url = URL.createObjectURL(new Blob([buildResultsReport(state)], { type: 'text/plain;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = exportFilename()
  document.body?.append(link)
  link.click()
  link.remove?.()
  // Firefox and Safari can still be consuming the object URL when `click()` returns, so
  // revoking synchronously can cancel the download. Released on the next task instead.
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

/**
 * The document `POST /api/v1/export/pdf` renders (`api/export.py`) — beside the plain-text
 * download above, not instead of it. Sends what the state already has and downloads what
 * comes back; every figure in the document is the server's (§7.6.1), computed on this
 * request rather than carried over from `state.result`.
 *
 * `exportPayload` reads the locale from `activeLanguage()` — the same value `i18n.js`
 * negotiated to put the rest of this page's own text on screen — because the server has no
 * other way to know which language the visitor is reading (§O-8).
 *
 * **Stamped by `exportFilename`, the same helper the text export uses, not a constant.**
 * `EXPORT_FILENAME` in `api/export.py` still names the response's `Content-Disposition`
 * header, but that header is never what names this file: `exportPdf` (`web/js/api.js`)
 * returns a `Blob` from a completed `fetch`, not a navigation the browser could read a
 * header from, so the name a visitor sees has only ever been this `<a download>`'s own
 * attribute — a front-end fix, and not one the contract needs to record. A fixed name here
 * is exactly what turned every second PDF into `... (1).pdf`, the confusing sibling of
 * `exportFilename()`'s reason for stamping the text export in the first place.
 */
export async function downloadPdf(state) {
  if (state.pdfExporting) return
  setState({ pdfExporting: true, pdfError: null })
  try {
    const blob = await exportPdf(exportPayload(state, activeLanguage()))
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = exportFilename(new Date(), { ext: 'pdf', unique: downloadUniqueId() })
    document.body?.append(link)
    link.click()
    link.remove?.()
    // Same fix PR #46's review confirmed was right: revoking synchronously can cancel the
    // download in some browsers, because Firefox and Safari can still be reading the
    // object URL when `click()` returns. Deferred to the next task instead.
    setTimeout(() => URL.revokeObjectURL(url), 0)
    setState({ pdfExporting: false })
  } catch (error) {
    setState({ pdfExporting: false, pdfError: error.message || t('The calculator service could not be reached. Check your connection and try again.') })
  }
}

/**
 * The client's own ask (round three): "a long rounded button rather than a tick, a bit
 * cuter, perhaps a small flower animation on press" — built from five half-circles, the
 * brand's own supporting graphic (the logo's half-disc, at small scale in an odd-numbered
 * group), each offset out from a shared hub before it is rotated into place - meeting at
 * the hub rather than at a single shared centre point, which is what keeps the five lobes
 * readable as petals instead of tiling into a solid disc (five half-discs rotated about
 * one point cover the circle completely, with no ground visible between them - a pie
 * chart, not a flower). Renders only from `contributeBlock`, only while
 * `state.contributeCelebrating` is true, so it never plays on its own — see that flag's
 * note in `state.js` for why a re-render alone must not replay it, and
 * `contributeCalculation` below for what clears it.
 *
 * `aria-hidden`: the flower adds nothing a screen reader needs. Every fact it stands for
 * — ticked, and now contributed — is already on the accessible checkbox itself and in
 * `.contribute-status`'s own text.
 *
 * **Where it blooms is now a slot rather than an overlay.** It used to be
 * `position: absolute` hung off `.contribute-choice`'s inline-end, which put it beside
 * the tick without occupying the row. `.contribute-slot` puts it in normal flow in
 * that same place, because Submit and Undo now share the position with it — see
 * `contributeBlock`.
 */
function contributeFlower() {
  // Each half-disc is drawn at the origin, same as before, then pushed outward by 7 units
  // along its own local +x *before* the rotation places it around the hub (`translate`
  // first, `rotate` second - SVG composes transforms right to left) - so its flat edge
  // sits 7 units from the hub and its dome reaches to 16, leaving the hub itself clear and
  // leaving ground visible between one dome tip and the next.
  const petal = (rotate, fill) =>
    `<path d="M0,-9 A9,9 0 0 1 0,9 Z" fill="${fill}" transform="rotate(${rotate}) translate(7,0)"></path>`
  return `<svg class="contribute-flower" width="44" height="44" viewBox="-22 -22 44 44" aria-hidden="true" focusable="false">${petal(0, 'var(--kai-pea)')}${petal(72, 'var(--kai-banana)')}${petal(144, 'var(--kai-pea)')}${petal(216, 'var(--kai-banana)')}${petal(288, 'var(--kai-pea)')}<circle r="3.4" fill="var(--kai-kale)"></circle></svg>`
}

/**
 * §6.2.2, and the control this whole task exists to write.
 *
 * **Unticked by construction.** `state.contributed` starts `false` and nothing here
 * sets it before a press — a pre-checked box would make stage one's `is_public_
 * contributed` default of FALSE decorative, which is exactly what item ⑬ reverses
 * §2.3's "no consent checkbox" decision to prevent.
 *
 * **Still a real checkbox, underneath.** Round three restyled this into the "long
 * rounded button" the client asked for, but `#contribute` is still a native
 * `input[type="checkbox"]` — visually replaced by the `<label>` beside it (`styles.css`'s
 * `.contribute-toggle`), never removed from the accessibility tree. That is what keeps
 * `page.locator('#contribute').check()` / `.is_checked()` — this file's own database-
 * reading tests among them — working unchanged: Playwright and a screen reader alike
 * still see a checkbox with this label as its accessible name. `aria-checked` is set
 * explicitly alongside the native `checked` property, redundant on a native input but
 * literally what the brief asks for kept, in case the visual control is ever rebuilt on
 * a non-native element that has no `checked` property of its own to fall back on.
 *
 * **The checked state is never colour alone.** `.contribute-toggle__mark` (`styles.css`)
 * switches from an open ring to a filled disc with a check mark drawn in its own `::after`
 * — a shape change, not a repaint — and `.contribute-status` below says the same thing in
 * words once a press succeeds. A visitor who cannot see colour, or is reading a screen
 * reader, still gets an unambiguous answer either way.
 *
 * **One-way, and said so before the click, not after.** The route only ever sets the
 * flag (§6.2.2's own table has no path that clears it), so unticking this box once the
 * request has gone would be a control that lies about its own affordance. Rather than
 * allow that gesture, both `checked` and `disabled` are keyed on `armed || pending ||
 * done` — ticked and locked from the moment Submit is pressed, not only once the
 * response lands. **Fix round 1:** `checked` used to read `done` alone, so for the whole
 * of the request the box showed unticked and disabled — a visitor watching their own
 * tick appear to undo itself, which is the one message this particular control must
 * never send. The sentence beside it says the choice is one-way before the click
 * reaches it at all.
 *
 * **Round four: two steps, and the tick is not one of them.** The client asked for a
 * tick on the left and a separate Submit button on the right. So the tick now does
 * *nothing* on its own — it sets `state.contributeTicked` and enables Submit, and no
 * request leaves the browser until Submit is pressed and its five seconds have run out.
 * That is a real change of meaning, not a layout change: the box is now a statement of
 * intent that the visitor can still change their mind about, which is exactly what makes
 * a grace period coherent. `#contribute` is unchanged in every other respect — still a
 * native checkbox, still named by `label[for="contribute"]`, still described by the
 * sentence — because Playwright's `check()` / `is_checked()` and a screen reader depend
 * on precisely that.
 *
 * **The five seconds are spent before the request, not after it.** See
 * `CONTRIBUTE_GRACE_MS` at the top of this file for why a server-side undo was refused.
 * While the window is open, the action slot holds Undo instead of Submit and
 * `contributeCountdown` below draws the time. Undo cancels the timer and leaves the box
 * **ticked** — the visitor said what they wanted and then declined to send it yet; the
 * pre-press state is the ticked, unsent one, and Submit can be pressed again.
 *
 * **One id for the action slot, deliberately.** Submit and Undo are two buttons, but
 * they are one slot, and `main.js` restores focus after a re-render by id. Sharing
 * `#contribute-action` is what keeps a keyboard visitor on the control they just pressed
 * instead of being dropped back on `<main>` at the moment the page grows an Undo button
 * they were never told about. The button's accessible name is what announces the change.
 *
 * **Consent survives a recalculation, on purpose.** `state.contributed` is not reset
 * by `submitCalculation` or `compareImprovement` on a return trip through the wizard,
 * matching `upsert_submission`'s own behaviour: the row is the same submission,
 * revised, and the flag this control set is not touched by an update either. Resetting
 * the box here would show an unticked control over a row that is, in the database,
 * still contributed — a front end telling a visitor "you haven't" about a choice the
 * server has already recorded. The sentence below says plainly that a recalculation's
 * *figures* replace the earlier ones under the same choice, which is the true state of
 * affairs rather than a fresh question with a false "no" already implied. The status
 * line after a successful press (**fix round 1**) states the same thing in the present
 * tense, rather than a past tense the server cannot actually promise — §6.2.2 answers
 * 204 on an unknown or expired token exactly as it does on a live one, so "has been
 * added" may be false in a way this page cannot detect; "are in" is true on the first
 * press and true again after every revision.
 */
function contributeBlock(state) {
  const pending = state.contributing
  const done = state.contributed
  const armed = contributeWindowIsOpen(state)
  // `pending || done` still forces the tick on: a request in flight, or landed, is a
  // consent given, whatever `contributeTicked` happens to hold. That is the fix-round-1
  // rule unchanged — the box must never read back empty over a consent already sent.
  const ticked = state.contributeTicked || pending || done
  const locked = armed || pending || done
  const celebrate = done && state.contributeCelebrating && !prefersReducedMotion()
  // Three states for one slot, and `done` has none: trap 4 in the brief — a visitor who
  // returns through the wizard after contributing must not be offered Submit again, so
  // the slot is empty and `.contribute-status` speaks for it.
  //
  // **Round five: the action and the flower are the same slot, in the same place.**
  // The row used to read tick — flower — (a gap the width of the row) — Submit, with
  // `.contribute-action { margin-inline-start: auto }` throwing the button to the far
  // end, so the thing the visitor pressed and the thing that answered the press were at
  // opposite sides of the block and nothing connected them. `.contribute-slot` sits
  // immediately after the tick, which is where the flower already was, and holds one
  // occupant at a time: **Submit → Undo → the flower, each replacing the last in the
  // position the eye is already on.** The handover is exact — `done` empties `action`
  // and turns `celebrate` on in the same `setState` (`contributeCalculation`), so the
  // one render that removes Undo is the one that plants the flower.
  //
  // The flower is unchanged in every other respect: still gated on
  // `state.contributeCelebrating` so an unrelated re-render cannot replay it, and still
  // gated on `prefersReducedMotion()` so a visitor who asked for no motion is given
  // none — both reasons are in `contributeFlower`'s own note above and in `state.js`.
  // In that case the slot simply ends up empty, which is the state it was already in
  // for every reduced-motion visitor before this change.
  const action = done
    ? ''
    : armed
      ? `<button class="button button-secondary contribute-action" type="button" id="contribute-action" data-action="contribute-undo">${escapeHtml(t('Undo'))}</button>`
      : `<button class="button button-primary contribute-action" type="button" id="contribute-action" data-action="contribute-submit" ${ticked && !pending ? '' : 'disabled'}>${escapeHtml(t('Submit'))}</button>`
  return `<div class="contribute-block">
    <p class="contribute-sentence" id="contribute-sentence">${escapeHtml(t('This sends an anonymous copy of your results into this calculator\'s public statistics — no name, no address, nothing that identifies you. You have five seconds after pressing Submit to undo it; once those five seconds pass it cannot be undone from here, and if you come back and recalculate, your updated figures take its place under this same choice.'))}</p>
    <div class="contribute-control">
      <span class="contribute-choice">
        <input type="checkbox" id="contribute" aria-describedby="contribute-sentence" aria-checked="${ticked ? 'true' : 'false'}" ${ticked ? 'checked' : ''} ${locked ? 'disabled' : ''}>
        <label for="contribute" class="contribute-toggle"><span class="contribute-toggle__mark" aria-hidden="true"></span><span class="contribute-toggle__text">${escapeHtml(t('I would like to contribute to the Kai Commitment'))}</span></label>
      </span>
      <span class="contribute-slot">${action}${celebrate ? contributeFlower() : ''}</span>
    </div>
    ${armed ? contributeCountdown(state) : ''}
    ${done ? `<p class="contribute-status" role="status">${escapeHtml(t("Your latest figures are in this calculator's public statistics."))}</p>` : ''}
    ${state.contributeError ? `<p class="field-error" role="alert">${escapeHtml(state.contributeError)}</p>` : ''}
  </div>`
}

/** Whether a grace window is open right now. `null` when none is. */
const contributeWindowIsOpen = state => Number(state.contributeArmedUntil) > 0

/**
 * The five seconds, drawn — and the one piece of this control that is a CSS animation
 * rather than a re-render, on purpose.
 *
 * **Why not a counter.** Showing "4… 3… 2…" means a state change a second, and
 * `render()` (`calculator.js`) replaces `main.innerHTML` on every `setState`. The
 * results page would be rebuilt five times over a window the visitor is quite likely to
 * be spending in the improvement panel, wiping the caret out of whatever they were
 * typing. The bar is one element with one 5s animation and costs nothing per frame.
 *
 * **Why the negative delay.** A CSS animation restarts whenever its element is created,
 * and this element is created afresh by every re-render. `animation-delay` with a
 * negative value starts an animation already part-way through, so offsetting it by the
 * time that has actually elapsed — `now` against `state.contributeArmedUntil`, the
 * absolute instant the window ends — makes a rebuilt bar resume where the old one was
 * instead of filling the visitor a second, third and fourth five seconds while the real
 * `setTimeout` runs out underneath. The timer itself is never restarted by a render;
 * only the picture of it could drift, and this is what stops it.
 *
 * **Reduced motion gets a stepped bar, not no bar.** `styles.css` switches the timing
 * function to `steps(5)` under `prefers-reduced-motion: reduce`: the bar jumps once a
 * second instead of sliding. Removing it entirely would leave a visitor who asked for
 * no animation with an Undo button and no way to know how much of their window was
 * left, which is worse than the motion. How long they have never depends on seeing the
 * bar at all: the paragraph above the control says "five seconds" in words.
 *
 * **The line beside the bar says the state, not the duration.** It read *"Sending in
 * five seconds"*, which is a promise about the future standing next to a bar that is
 * already visibly spending those seconds — and it counted from now while the paragraph
 * above counts from the press. What the visitor needs in this moment is the one fact
 * that lets them relax or act: nothing has gone yet, and here is the button that keeps
 * it that way. The duration belongs to the bar and to the paragraph, which both have
 * it.
 */
function contributeCountdown(state) {
  const elapsed = Math.min(Math.max(CONTRIBUTE_GRACE_MS - (state.contributeArmedUntil - Date.now()), 0), CONTRIBUTE_GRACE_MS)
  return `<div class="contribute-countdown">
    <span class="contribute-countdown__track" aria-hidden="true"><span class="contribute-countdown__bar" style="animation-delay: -${Math.round(elapsed)}ms"></span></span>
    <span class="contribute-countdown__text">${escapeHtml(t('Not sent yet. Press Undo to stop it.'))}</span>
  </div>`
}

/**
 * Submit: arm the window, and send nothing.
 *
 * The request is `contributeCalculation`'s, fired by the `setTimeout` below once the
 * five seconds are spent — so up to that instant there is nothing to withdraw, which is
 * the whole design (see `CONTRIBUTE_GRACE_MS`). `toPublicMessage` is threaded through
 * rather than imported for `contributeCalculation`'s own reason: `calculator.js` already
 * imports this module and the import back would be a cycle.
 *
 * Guarded on `contributeTicked` as well as on the three states that already exclude a
 * press, because the button is `disabled` in markup only — a disabled attribute is a
 * statement to the browser, not a guarantee to this function.
 */
export function armContribute(state, toPublicMessage) {
  if (!state.contributeTicked || state.contributing || state.contributed || contributeWindowIsOpen(state)) return
  clearTimeout(contributeGraceTimer)
  contributeGraceTimer = setTimeout(() => {
    contributeGraceTimer = null
    // `liveState`, not the snapshot this closure was created with: five seconds is long
    // enough for the visitor to have pressed Undo (which clears this), cleared the
    // calculator, or run another calculation. A window that is no longer open is a
    // request that must not be sent, and returning here — rather than in
    // `contributeCalculation` — means no `setState` and so no re-render either.
    if (!contributeWindowIsOpen(liveState)) return
    setState({ contributeArmedUntil: null })
    contributeCalculation(liveState, toPublicMessage)
  }, CONTRIBUTE_GRACE_MS)
  setState({ contributeArmedUntil: Date.now() + CONTRIBUTE_GRACE_MS, contributeError: null })
}

/**
 * Undo: cancel the timer, and leave the tick alone.
 *
 * Nothing is unsent because nothing was sent. The box stays **ticked** and becomes
 * editable again, so the visitor is returned to the state they were in immediately
 * before the press rather than to a blank control — they chose to contribute and then
 * chose not to send it yet, and only the second of those two decisions was undone.
 * Submit can be pressed again straight away, on a fresh five seconds.
 */
export function cancelContribute() {
  clearTimeout(contributeGraceTimer)
  contributeGraceTimer = null
  setState({ contributeArmedUntil: null })
}

/**
 * Runs on the checkbox's `change` and stores the result, or its message, on the state.
 *
 * `toPublicMessage` is `calculator.js`'s `publicError`, passed in rather than imported
 * for the same reason `compareImprovement` (`improvement.js`) takes it as a parameter:
 * that module already imports this one, and the import back would be a cycle. §6.2.2
 * answers 204 always, so there is nothing here to read as a domain-level failure — only
 * the network call itself can fail, and `ApiError`'s message is already the calculator's
 * own network-unreachable copy.
 *
 * A failed call leaves `contributed` false, which re-enables the checkbox and leaves it
 * unticked — the "pre-press state" the brief asks for — rather than reporting a success
 * that did not happen. **Round four clears `contributeTicked` with it**, deliberately,
 * and this is the one place the two-step control does *not* keep the tick. Undo leaves
 * the box ticked because the visitor withdrew only the sending; a failure withdraws
 * nothing — it means the calculator could not be reached — and the recorded decision
 * here is that a failed press returns the control to exactly the state it was in before
 * the press. Leaving the tick standing under an error message would put the control in a
 * state a visitor can reasonably read as "it went through, with a warning". The error
 * message says what happened and the two steps are there to be taken again.
 *
 * **`contributeCelebrating` is set on success and cleared by this function, not by the
 * next render.** `renderResults` rebuilds the whole section on every `setState`
 * (`main.js`'s `render()`), so a flower that rendered for as long as `state.contributed`
 * stayed true would bloom again on every unrelated re-render — opening the improvement
 * panel after contributing, say. Setting a second, one-shot flag and clearing it with its
 * own `setTimeout` is what confines the animation to the actual transition, the same
 * pattern `calculator.js` and `downloadPdf` above already use for a timed state clear.
 */
export async function contributeCalculation(state, toPublicMessage = error => error.message || t('The calculator service could not be reached. Check your connection and try again.')) {
  if (state.contributing || state.contributed || !state.token) return
  setState({ contributing: true, contributeError: null })
  try {
    await contribute(state.token)
    setState({ contributing: false, contributed: true, contributeCelebrating: true })
    // **§7.2a: the choice has to outlive the page load, or the interface forgets it.** A
    // visitor who contributed, left for the methodology page and pressed Back must not be
    // invited to contribute again — §5.3's token upsert means no second row would be
    // written, but the page would be telling them it had forgotten. `liveState` rather than
    // the `state` parameter: this function is handed a snapshot taken up to five seconds
    // ago by `armContribute`'s timer, and `setState` has just mutated the live object.
    // `contributeCelebrating` is deliberately not in `RESULT_KEYS`, so the flower does not
    // travel with the flag.
    writeResultSnapshot(liveState)
    setTimeout(() => setState({ contributeCelebrating: false }), CONTRIBUTE_CELEBRATE_MS)
  } catch (error) {
    setState({ contributing: false, contributed: false, contributeTicked: false, contributeError: toPublicMessage(error) })
  }
}

/**
 * The foods whose figures came from their category's average, once each.
 *
 * **The engine decides this, not the browser.** `item_basis` is
 * `EntryResult.item_basis` (contract v1.59), rolled up in `engine/calculate.py`
 * from every breakdown row of both scenarios. Working it out here would mean
 * the results page, this file's text export and `api/pdf_render.py` each
 * reimplementing the same roll-up, in two languages, for one submission --
 * and the rule is that the screen and both exports tell one story.
 *
 * Only `'category'` is disclosed. `'mixed'` is the ordinary state rather than
 * an alarm: `prevention` factors are stored as category-level rows (contract
 * §2.2, the shape that closes O-7), so every entry that moves mass to
 * prevention has a category-priced line however well the set prices its food.
 * A notice raised on `'mixed'` would fire on a row that is deliberately
 * category-level, and a notice that fires on everything is read as furniture.
 *
 * @param {object} state
 * @returns {Array<{food: string, category: string}>}
 */
function categoryAverageFoods(state) {
  const seen = new Set()
  const out = []
  for (const { response } of state.result?.entry_results || []) {
    // Both conditions, and neither implies the other: an entry that named no
    // food is `not_applicable` and has nothing to disclose, and a response
    // that predates v1.59 carries no `item_basis` at all.
    if (response?.item_basis !== 'category' || !response?.food_item) continue
    if (seen.has(response.food_item)) continue
    seen.add(response.food_item)
    const item = findByCode(taxonomyForResult(state)?.food_items || [], response.food_item)
    const category = findByCode(taxonomyForResult(state)?.food_categories || [], item?.food_category ?? response.food_category)
    out.push({
      // The code is the fallback, never a blank: a taxonomy row can be
      // retired after a submission named it (contract §5.2), and a notice
      // that named nothing would be a caveat about an unnamed thing.
      food: item?.name || response.food_item,
      category: category?.name || response.food_category || t('its food category'),
    })
  }
  return out
}

/** The same sentences, as the lines both exports print. */
function categoryAverageLines(state, prefix) {
  return categoryAverageFoods(state).map(({ food, category }) =>
    `${prefix}${t('%(food)s is priced at the %(category)s average. The published factor set carries no factors for this food, so the figures here are its category\'s rather than its own.', { food, category })}`)
}
/**
 * The four sections the floating nav indexes, **in the page's order**.
 *
 * One list, read twice: `resultsFloatingNavigation` writes the links from it and
 * `bindResultsSectionSpy` observes the same four elements. Two lists would be two
 * lists to keep in step, and the order is the whole of this nav's correctness --
 * `test_the_results_sections_are_in_the_order_the_floating_nav_claims` asserts it
 * against the rendered page for exactly that reason.
 *
 * **Every entry here must be a place the page can actually come to rest**, and that
 * is a real constraint rather than a tidiness one (#126/#150). A jump leaves its
 * target at `SECTION_REST_TOP`, and `bindNavGestures`'s scroll path releases a pin
 * only once the target has *reached* that line and then left it. An entry close
 * enough to the end of the document that the scroll clamps before it gets there can
 * never set `pinnedSettled`, so the `aria-current` mark a press put on it is never
 * taken off again by a scroll -- and a dragged scrollbar, which is precisely the
 * gesture the scroll path exists for, fires no wheel, no touch and no key to take it
 * off either. That is why the *Start a new calculation* and *Download results*
 * entries #126 asked for are not here: they are the page's closing action row, which
 * sits about 430px from the end of the document, and `.result-actions` is one flex
 * row so both of them were also the same destination. They are in the panel as the
 * controls they are instead -- see `resultsFloatingNavigation`.
 *
 * The label is a function because `t()` has to run at render time: changing language
 * re-renders in place rather than reloading (`main.js`), so a label evaluated once at
 * module load would stay in the language the page was opened in. Each one is still a
 * plain literal `t('...')` call, which is what the extractor's regex can see.
 */
const RESULTS_NAV_SECTIONS = [
  //: **This list is in the page's order, and that is the whole of its
  //: correctness.** It read summary / improvements / equivalents / breakdown
  //: while the improvement panel sat below the downloads, three sections
  //: further down than the second slot claimed -- so its one link that was
  //: meant to save a scroll was the one that jumped past everything.
  ['impact-summary', () => t('Impact summary')],
  ['tangible-equivalents', () => t('Tangible equivalents')],
  ['breakdown-section', () => t('Breakdown by category')],
  ['improvement-section', () => t('Explore Improvements')],
]

/**
 * The results page's own actions, offered **inside the nav panel as controls**.
 *
 * #126 asked for *Download* and *Start a new calculator* in the results-page
 * navigation. They are not sections and they are not entries in
 * `RESULTS_NAV_SECTIONS`: as `#`-links they were two links onto one flex row, so
 * both jumped to the same place, only the first could ever be marked by the
 * observer, and neither could release the pin a press put on it (see that list's
 * note). As buttons they do the thing the issue asks for -- the download downloads
 * and *Start a new calculation* starts one -- from wherever the reader has got to,
 * without scrolling anywhere at all.
 *
 * **The `data-action` values are the action row's own**, so `calculator.js`'s one
 * delegated `main` click listener already serves them and nothing had to be added
 * there. That is also what makes the shared accessible name correct rather than the
 * defect it would be on a link: the panel's *Download results* and the action row's
 * *Download results* are two doors onto one action, not a link and a button that
 * happen to read alike while doing different things.
 *
 * Labels are functions for the same reason `RESULTS_NAV_SECTIONS`' are, and they are
 * the two strings the action row already uses, so twenty catalogues needed nothing.
 */
const RESULTS_NAV_ACTIONS = [
  ['start-over', () => t('Start a new calculation')],
  ['download-results', () => t('Download results')],
]

/**
 * Whether the stylesheet currently has the nav **docked in the gutter**.
 *
 * The breakpoint is 1600px and it is written down once, in `styles.css`, which is
 * the file that knows the viewport. This reads it back off the custom property
 * rather than repeating the number here: a media query in JavaScript and a media
 * query in CSS are two statements of one fact, and they drift.
 *
 * It answers one question -- which way the FIRST press of the handle goes. See
 * `resultsFloatingNavigation`'s note on `state.resultsNavOpen`.
 *
 * **It now has one live answer.** The docked regime draws no handle at all
 * (`styles.css`, `.results-floating-nav__handle { display: none }` inside the
 * `min-width: 1600px` block), so a press can only reach `calculator.js` from the
 * undocked regime, where this returns `false` and the first press therefore always
 * opens. The function is kept rather than deleted because `docs/interfaces.md`
 * §7.3a publishes it, and a contract change is three steps and a notification.
 *
 * @param {Document|Element} [root] Where to look for the nav; the document by default.
 * @returns {boolean} `false` when there is no nav, no DOM, or no docked regime.
 */
export function resultsNavIsDocked(root = typeof document === 'undefined' ? null : document) {
  const nav = root?.querySelector?.('.results-floating-nav')
  if (!nav || typeof getComputedStyle !== 'function') return false
  return getComputedStyle(nav).getPropertyValue('--results-floating-nav-docked').trim() === '1'
}

/**
 * Which section the reader is standing in, and the one observer that decides it.
 *
 * **Neither of these may go on `state`.** `setState` re-renders the whole results
 * page, so a scroll position kept there would rebuild several hundred elements per
 * scroll event -- and rebuild the nav under the reader's cursor while they were
 * reaching for it. The highlight is written straight onto the `<a>` instead, and
 * this module remembers which one so the mark can be re-applied after a render
 * that had nothing to do with scrolling.
 */
let currentSection = null

/**
 * The section a **click on a nav link** has claimed, until the reader scrolls away.
 *
 * `null` means the observer decides, which is every moment the visitor has not just
 * pressed a link. While it is set, `onSectionsCrossed` stands down entirely.
 *
 * **This exists because the observer could not be trusted to settle on the right
 * answer, and the fix to the tie-break does not make it redundant.** A jump is an
 * instruction, not an inference: the reader said "take me to *Tangible equivalents*",
 * and for as long as they have not moved again the honest answer to "where am I" is
 * the one they gave. The band is 162-360 at a 900px viewport and a jump lands its
 * target at 24, so a section under 138px tall would sit entirely above the band on
 * arrival and the spy would mark the one after it -- the same class of defect as the
 * one this round fixes, waiting on a shorter section being added to the four. The
 * pin takes the click path off the geometry altogether rather than depending on it
 * staying favourable; see `SECTION_BAND` for why the band is not simply moved up to
 * meet the rest line.
 *
 * **It is also what puts the mark on the link at the moment of the press.**
 * `html { scroll-behavior: smooth }`, so a click is followed by several hundred
 * milliseconds of travel during which the observer's honest answer is wherever the
 * page currently is -- the section the reader is leaving, then whatever it passes on
 * the way. Measured from the top of the page: 60ms after a press on *Explore
 * Improvements* the observer still says *Impact summary*. The pin answers the press,
 * not the animation.
 */
let pinnedSection = null

/**
 * Whether the pinned section has actually arrived where the jump was sending it.
 *
 * `html { scroll-behavior: smooth }` (styles.css), so a click is followed by a burst
 * of `scroll` events that are the *browser's* and not the reader's. Releasing the pin
 * on the first of them would undo the pin before the page had finished moving. So the
 * pin is released by a scroll only once the target has reached its rest position and
 * then left it -- and by a wheel, a touch drag or a scrolling key immediately, since
 * those are the reader's hand on the page whatever the animation is doing.
 */
let pinnedSettled = false

/** Installed once, on `document`, and never by a render. See `bindNavGestures`. */
let navGesturesBound = false

/**
 * **The one `IntersectionObserver` for the life of the page.**
 *
 * `render()` replaces `main.innerHTML` on every `setState` -- a keystroke in the
 * improvement panel is a full rebuild -- so the four `<section>` elements this
 * watches are destroyed and recreated constantly. An observer wired once at
 * start-up is left holding four detached nodes and reports nothing ever again; a
 * fresh `new IntersectionObserver` per render is one live observer per keystroke,
 * each still holding its own detached nodes.
 *
 * So: one observer object, created lazily, `disconnect()`ed and re-pointed at the
 * new elements on every bind. `tests/web/test_results_floating_nav_browser.py`
 * counts the constructions from a page init script and holds that count at one
 * across a whole page's worth of re-renders.
 */
let sectionSpy = null

/** Which of the four are inside the reading band right now, by element id. */
const sectionsInBand = new Set()

/**
 * Where a clicked section comes to rest, in CSS pixels.
 *
 * **Measured on the running stack, not assumed.** `styles.css` gives all four
 * sections `scroll-margin-top: 24px`, and there is no sticky element at the top of
 * this page to clear -- `.step-nav` is `position: sticky; bottom: 0` and sits at
 * 824-900 of a 900px viewport, and `.site-header` does not stick at all. So a jump
 * lands the target's border box at exactly `top: 24`, which all four were measured
 * doing (scrollY 427 / 1225 / 1548 / 1986 at 1600x900).
 *
 * It is the same 24 as the stylesheet's and the two must move together. It is what
 * tells a scroll the browser is performing from a scroll the reader is performing:
 * see `bindNavGestures`. It is also the number that decides whether the reading band
 * could be relied on for a click at all -- see `SECTION_BAND`, where it is not.
 */
const SECTION_REST_TOP = 24

/**
 * The band the reader is taken to be reading, as a `rootMargin`.
 *
 * Top 18% to 40% of the viewport: below the header, above the middle. A section is
 * "current" while it crosses that strip. The alternative -- whichever section has
 * the largest visible area -- cannot mark the short ones at all: *Tangible
 * equivalents* is a third of the height of *Breakdown by category* and would never
 * win a contest it is measured by area.
 *
 * **Left where it is, and that is a decision taken from the measurement rather than
 * in spite of it.** A jump lands its target at `SECTION_REST_TOP`, 24px, which is
 * 138px above this band's top edge at a 900px viewport -- so a section shorter than
 * 138px would sit entirely above the band after a jump to it and the spy would mark
 * its successor. Today the shortest of the four is 220px, with 82px to spare. The
 * obvious repair is to drop the band's top edge onto the 24px rest line, and it was
 * measured and rejected: the top of the band is the line at which the mark hands
 * over, and at 24px it hands over only once a section has left the viewport
 * altogether. A reader looking at 87px of *Tangible equivalents* above 800px of
 * *Breakdown by category* would be told they are in *Tangible equivalents*, which
 * trades a defect that needs a 138px section to appear for one that appears on every
 * scroll. The click path is made correct by pinning it (`pinnedSection`) instead,
 * which is the mechanism that does not depend on how tall anybody's sections are.
 */
const SECTION_BAND = '-18% 0px -60% 0px'

/** Write `aria-current="location"` onto the reader's section and onto nothing else. */
function markCurrentSection(nav) {
  if (!nav) return
  for (const link of nav.querySelectorAll('.results-floating-nav__links a')) {
    const id = (link.getAttribute('href') || '').slice(1)
    //: `"location"` and not `"page"`: this marks a position WITHIN the page the
    //: reader is on. `"page"` is the claim the site drawer's own marker makes
    //: about which page of the site is open, and two different claims sharing one
    //: value is how a screen reader ends up announcing both as the same thing.
    if (id && id === currentSection) link.setAttribute('aria-current', 'location')
    else link.removeAttribute('aria-current')
  }
}

function onSectionsCrossed() {
  //: A pinned section is the reader's own answer to this question and outranks the
  //: observer's. See `pinnedSection`.
  if (pinnedSection) return
  //: **The FIRST section in page order that is in the band**, which -- the sections
  //: being contiguous and in order -- is the one occupying the band's top edge. That
  //: edge is the reading line, and the section under it is the heading the reader
  //: most recently passed. It used to take the LAST, on the reasoning that the last
  //: is "the one the reader has most recently come to"; that is the right description
  //: of a section whose *top* has just crossed into the band from below, and the
  //: wrong one for the reader, who is still looking at the section above it. Measured
  //: at a 900px viewport, where the band is 162-360: a jump to *Tangible equivalents*
  //: rests it at 24-311, so 149px of it is in the band -- and the 36px gap after it
  //: puts *Breakdown by category* at 347-360, 13px of it also in the band. Taking the
  //: last marked *Breakdown*, which is the reported defect, one link out of step,
  //: exactly.
  let next = null
  for (const [id] of RESULTS_NAV_SECTIONS) {
    if (sectionsInBand.has(id)) { next = id; break }
  }
  //: Nothing in the band keeps the previous answer rather than clearing it: the
  //: gap between two sections is wider than the band at the top of the page and
  //: under the fold, and a nav that blanks out there reads as broken.
  if (!next || next === currentSection) return
  currentSection = next
  markCurrentSection(document.querySelector('.results-floating-nav'))
}

/**
 * A press on a nav link claims that section until the reader moves the page.
 *
 * Called from the one delegated listener in `bindNavGestures`, not from a handler
 * attached per render: `render()` replaces `main.innerHTML` on every `setState`, so
 * a listener on the `<a>` would be thrown away with the element that carried it, and
 * re-attaching one per render is the same leak `sectionSpy` exists to avoid.
 */
function pinSection(id) {
  if (!RESULTS_NAV_SECTIONS.some(([section]) => section === id)) return
  pinnedSection = id
  pinnedSettled = false
  currentSection = id
  markCurrentSection(document.querySelector('.results-floating-nav'))
}

/** Hand the question back to the observer, and answer it again from what it holds. */
function releasePin() {
  if (!pinnedSection) return
  pinnedSection = null
  pinnedSettled = false
  onSectionsCrossed()
}

/**
 * The keys that scroll a document. Anything else the reader presses is not a scroll,
 * and `Enter` on a nav link is emphatically not one -- `keydown` precedes `click`, so
 * an unfiltered key listener would have released the pin the keyboard press was in
 * the act of setting.
 */
const SCROLL_KEYS = new Set(['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' ', 'Spacebar'])


/**
 * The three listeners the pin needs, installed **once on `document`** for the life of
 * the page.
 *
 * Wired here rather than at module load because this module is imported by a Node
 * harness for the text export, where there is no `document` at all.
 *
 * * **click** sets the pin. Delegated, because the links are rebuilt by every render.
 * * **wheel / touchmove / a scrolling key** release it at once: that is the reader's
 *   hand on the page, and it is allowed to interrupt a smooth scroll that is still
 *   running. Keys pressed inside a form control are excluded -- an arrow key in the
 *   improvement panel's `<select>` moves the select, not the page.
 * * **scroll** releases it only after the target has arrived and then left, which is
 *   what makes a dragged scrollbar -- which fires no wheel and no key -- release the
 *   pin without the smooth scroll released it on its own first frame.
 *
 * **The gesture listeners overlap the scroll listener almost completely, and the
 * sliver they do not is why they are here.** Removing the `wheel` listener alone was
 * mutation-tested and left `test_the_readers_own_scroll_takes_the_pin_back_off`
 * green: a wheel produces scroll events too, the target had already arrived, and the
 * scroll path released the pin on the same gesture. The case that is theirs alone is
 * a gesture that arrives *before* the jump has landed and cancels it -- Chromium
 * abandons a programmatic smooth scroll on real user scroll input -- because
 * `pinnedSettled` would then never be set and the scroll path would never fire
 * again. The pin would be stuck on that section for the rest of the session. It could
 * not be reproduced through Playwright, whose synthesised wheel does not cancel the
 * animation (measured: the page still arrived at scrollY 1986), so these three are
 * kept on the reasoning rather than on a test, and the test that does hold is
 * `test_the_readers_own_scroll_takes_the_pin_back_off` against all four paths removed.
 */
function bindNavGestures() {
  if (navGesturesBound || typeof document === 'undefined') return
  navGesturesBound = true
  document.addEventListener('click', event => {
    const link = event.target?.closest?.('.results-floating-nav__links a[href^="#"]')
    if (link) {
      pinSection(link.getAttribute('href').slice(1))
      return
    }
    // The menu is an overlay in the narrow layout. Once it is open, any click
    // outside the nav should return it to the circular handle state. Keep this
    // delegated on document because the click may land in the header or footer,
    // outside the results page's own event boundary.
    if (!event.target?.closest?.('.results-floating-nav') && liveState.resultsNavOpen === true) {
      setState({ resultsNavOpen: false })
    }
  })
  document.addEventListener('wheel', releasePin, { passive: true })
  document.addEventListener('touchmove', releasePin, { passive: true })
  document.addEventListener('keydown', event => {
    if (!SCROLL_KEYS.has(event.key)) return
    if (event.target?.closest?.('input, textarea, select, [contenteditable]')) return
    releasePin()
  })
  document.addEventListener('scroll', () => {
    if (!pinnedSection) return
    const target = document.getElementById(pinnedSection)
    if (!target) return
    const arrived = Math.abs(target.getBoundingClientRect().top - SECTION_REST_TOP) <= 2
    if (arrived) pinnedSettled = true
    else if (pinnedSettled) releasePin()
  }, { passive: true })
}

/**
 * Point the section spy at whatever `render()` has just written into `<main>`.
 *
 * Called at the end of every `render()`, including the renders that are not the
 * results page: no nav means disconnect, so the observer is never left holding
 * elements that have left the document.
 *
 * The mark is re-applied synchronously here, before the observer's own first
 * callback arrives, because the nav the render just wrote carries no
 * `aria-current` at all -- it is written by this module and not by the template.
 * Without that line the highlight blinks off on every keystroke in the
 * improvement panel and comes back a frame later.
 *
 * @param {Element} root The container `render()` wrote into.
 */
export function bindResultsSectionSpy(root) {
  const nav = root?.querySelector?.('.results-floating-nav')
  const sections = nav
    ? RESULTS_NAV_SECTIONS.map(([id]) => root.querySelector(`#${id}`)).filter(Boolean)
    : []
  if (!sections.length) {
    sectionSpy?.disconnect()
    sectionsInBand.clear()
    currentSection = null
    //: A pin is a claim about a page that no longer exists. Left standing, it would
    //: silence the observer for the whole of the next visit to the results page.
    pinnedSection = null
    pinnedSettled = false
    return
  }
  bindNavGestures()
  if (!sectionSpy) {
    //: Absent in the Node harness that runs this module for the text export, and
    //: in any browser old enough not to have it. The nav still jumps; only the
    //: highlight is missing, which is what a progressive enhancement is.
    if (typeof IntersectionObserver !== 'function') return
    sectionSpy = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (entry.isIntersecting) sectionsInBand.add(entry.target.id)
        else sectionsInBand.delete(entry.target.id)
      }
      onSectionsCrossed()
    }, { rootMargin: SECTION_BAND, threshold: 0 })
  } else {
    //: The elements are new; the observer is not. `disconnect()` drops the four
    //: detached ones and keeps the object, which is what holds the count at one.
    sectionSpy.disconnect()
    sectionsInBand.clear()
  }
  //: At the top of the page nothing is in the band yet -- the first section's own
  //: heading is below it -- and a nav with no mark on it at the moment it appears
  //: reads as a nav whose mark is broken. The first section is where the reader is
  //: about to be, so it holds the mark until a real crossing moves it.
  if (currentSection === null) currentSection = RESULTS_NAV_SECTIONS[0][0]
  markCurrentSection(nav)
  for (const section of sections) sectionSpy.observe(section)
}

/**
 * The results page's section jump list, which lives in the page's **gutter**.
 *
 * The request behind it: at a wide viewport this page is still a ~1000px column, so
 * there is a band of empty page either side of it, and the page itself is long enough
 * that reaching a section means scrolling for a while. The list goes in that band.
 *
 * **It is not site navigation and no longer says it is.** The label was
 * `t('Site navigation')`, which is the site drawer's own string (`index.html`), so a
 * screen reader's landmark list showed two `<nav>` elements with one indistinguishable
 * name -- in every language, since both read the same key.
 *
 * **Where it is and when it exists are decided in CSS, not here**, because both depend
 * on the viewport and this function runs once per render with no idea of it. The
 * stylesheet draws it only where there is a gutter to draw it in, and has two defaults
 * there: docked and open from 1600px up, a closed handle between 1100 and 1599.
 * `data-open` is written **only** once the visitor has toggled it, so the attribute's
 * absence still means "the stylesheet decides" -- it now means it about two defaults
 * instead of one, which is why the first press asks `resultsNavIsDocked()` what it is
 * inverting rather than assuming "open".
 */
function resultsFloatingNavigation(state) {
  const label = t('Sections on this page')
  const links = RESULTS_NAV_SECTIONS
    .map(([id, text]) => `<li><a href="#${id}">${escapeHtml(text())}</a></li>`)
    .join('')
  //: Absent until the visitor decides, so the CSS default stands. `aria-expanded`
  //: follows the same value: stating `false` while the stylesheet has the panel open
  //: is the contradiction a screen-reader user meets first.
  const toggled = state?.resultsNavOpen
  const openAttribute = toggled === undefined ? '' : ` data-open="${toggled}"`
  const expanded = toggled === undefined ? '' : ` aria-expanded="${toggled}"`
  //: The handle carries an `id` so that `main.js` can put focus back on it after the
  //: re-render its own press causes. Without one, `document.activeElement.id` is `''`,
  //: focus lands on `<main>`, and a keyboard visitor who opens the list is thrown to
  //: the top of the page instead of into it.
  //:
  //: **The two actions below are buttons, and that is the answer to #126 rather than
  //: a shortcut past it** -- see `RESULTS_NAV_ACTIONS`. They carry the action row's
  //: own `data-action` values, so the delegated listener in `calculator.js` runs the
  //: same code the row's own buttons run; there is nothing nav-specific behind them.
  //: They are outside the `<ul>` because that list is the section index and these are
  //: not sections, and `role="group"` is what says "these belong together" without
  //: claiming they are a second list of places to go.
  const actions = RESULTS_NAV_ACTIONS
    .map(([action, text]) => `<button class="results-floating-nav__action" type="button" data-action="${action}">${escapeHtml(text())}</button>`)
    .join('')
  return `<nav class="results-floating-nav"${openAttribute} aria-label="${escapeHtml(label)}"><button class="results-floating-nav__handle" id="results-floating-nav-handle" type="button" data-action="toggle-results-nav" aria-controls="results-floating-nav-menu"${expanded} aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}"><span aria-hidden="true">⋮</span></button><div class="results-floating-nav__panel" id="results-floating-nav-menu"><ul class="results-floating-nav__links">${links}</ul><div class="results-floating-nav__actions" role="group">${actions}</div></div></nav>`
}

export function renderResults(state) {
  const result = state.result
  const entryResults = result?.entry_results || []
  if (!entryResults.length) return `<section class="content-section"><h1>${escapeHtml(t('Results unavailable'))}</h1><p>${escapeHtml(t('No calculation result has been returned.'))}</p></section>`
  // §6.2: `totals` is what the headline figures are rendered from, and the engine computes
  // it. The `aggregateResults` this replaced added each entry's metric total up in the
  // browser and rendered the user's own step-3 arithmetic as the total mass, so the two
  // largest numbers on the page were numbers the engine never produced (§7.6.1).
  const totals = result.totals || {}
  const mock = result.factor_set?.is_mock
  const warning = mock ? `<aside class="disclaimer" role="status"><span class="info-icon" aria-hidden="true">i</span><div><strong>${escapeHtml(t('Placeholder data'))}</strong><p>${escapeHtml(t(DEMONSTRATION_NOTICE))}</p></div></aside>` : ''
  // Contract v1.59. The same shape as the placeholder banner above and for a
  // related reason -- both say *this number is not what it looks like* -- but
  // independent of it: a real factor set can still price a food only at its
  // category, and a placeholder one can price a food individually. One line
  // per food, so no plural form is needed in twenty catalogues.
  const averaged = categoryAverageFoods(state)
  const averagedNotice = averaged.length
    ? `<aside class="disclaimer" role="status"><span class="info-icon" aria-hidden="true">i</span><div><strong>${escapeHtml(t('Food category average'))}</strong>${averaged.map(({ food, category }) => `<p>${escapeHtml(t('%(food)s is priced at the %(category)s average. The published factor set carries no factors for this food, so the figures here are its category\'s rather than its own.', { food, category }))}</p>`).join('')}</div></aside>`
    : ''
  const version = result.factor_set?.version_label || t('Not supplied')
  // `stepNav` is the LAST child of this section and has to stay there: it is
  // `position: sticky; bottom: 0`, which pins only while its containing block
  // extends past the fold. The improvement panel renders above it for that
  // reason — appending anything after the bar unpins it early, and its first
  // action was 1,230-2,075px past the fold before it existed. "Edit your data"
  // and "Download results" moved into it; "Start a new calculation" did not,
  // because it is a confirm-guarded reset rather than a step action, and the
  // header's home button already offers it.
  return `<section class="content-section wide results-page" aria-labelledby="results-title">${resultsFloatingNavigation(state)}<p class="eyebrow">${escapeHtml(t('Step %(step)s', { step: 6 }))}</p><h1 id="results-title">${escapeHtml(t('Your estimated impact'))}</h1><p class="section-intro">${escapeHtml(entryResults.length === 1
      ? t('Results returned by the calculation service for one supply-chain entry.')
      // **`count` is entries, and an entry is a leaf.** A single forked chain is one
      // supply-chain entry rendered as several, so the old wording — "for 3 supply-chain
      // entries" — was a claim about the visitor's own submission that stopped being
      // true. The noun is dropped rather than replaced with a second count nobody asked
      // for; the review step is where the two numbers are reconciled.
      : t('Results returned by the calculation service for %(count)s entries.', { count: entryResults.length }))}</p>${resultsPeriod(state)}${warning}${averagedNotice}
    <section class="results-section" id="impact-summary" aria-labelledby="summary-title"><div class="result-section-heading"><span class="section-number">01</span><div><h2 id="summary-title">${escapeHtml(t('Impact summary'))}</h2><p>${escapeHtml(t('A high-level view of the recorded food waste.'))}</p></div></div><div class="results-grid">${summaryCards(totals, taxonomyForResult(state))}</div>${moneySummary(totals)}</section>
    <section class="results-section" id="tangible-equivalents" aria-labelledby="equivalents-title"><div class="result-section-heading"><span class="section-number">02</span><div><h2 id="equivalents-title">${escapeHtml(t('Tangible equivalents'))}</h2><p>${escapeHtml(t('Plain-language comparisons appear when supplied by the calculation service.'))}</p></div></div>${equivalences(totals)}</section>
    ${breakdownSection(state, entryResults)}
    ${ImprovementScenario(state)}
    ${ComparisonResults(state)}
    <section class="methodology-compact" id="results-methodology" aria-labelledby="results-methodology-title"><h2 id="results-methodology-title">${escapeHtml(t('Methodology & Limitations'))}</h2><p>${escapeHtml(t('Results are estimates. Impact calculations are supplied by the calculation API; the front end performs unit conversion only.'))}</p><p>${escapeHtml(t('Factor version'))}: ${escapeHtml(version)}.</p><details><summary>${escapeHtml(t('View methodology'))}</summary><div><p>${escapeHtml(t('Data sources and calculation factors are maintained and approved by Kai Commitment.'))}</p><p>${escapeHtml(t('Waste as a share of food handled is a ratio of the two masses you typed, not a factor-based figure, so the placeholder data above does not affect it.'))}</p></div></details></section>
    <div class="result-actions"><button class="button button-secondary" type="button" data-action="start-over">${escapeHtml(t('Start a new calculation'))}</button><div class="download-actions"><button class="button button-primary" type="button" data-action="download-results">${escapeHtml(t('Download results'))}</button><button class="button button-primary" type="button" data-action="download-pdf" ${state.pdfExporting ? 'disabled' : ''}>${escapeHtml(t('Download PDF'))}</button></div>${state.pdfError ? `<p class="field-error" role="alert">${escapeHtml(state.pdfError)}</p>` : ''}</div>
    ${contributeBlock(state)}
    ${stepNav({ step: 5, back: 4, backLabel: t('Edit your data'), action: null })}
  </section>`
}
