/**
 * The documentation page: D's rendering, HEAD's translation plumbing.
 *
 * **D's version wins on substance.** §6.3 carries `source_note` and `data_quality`
 * on every factor row, and v1.1's stated reason for those columns is that a
 * calculator which cannot say which of its numbers are measured and which are
 * borrowed cannot be defended in public. §6.3 is the only public surface where a
 * number can say so, and the page this replaces published neither, along with no
 * constants, no upstream, no downstream and no equivalences. D's mock banner is
 * also the better one: `role="alert"`, no dismiss path, and gated on
 * `is_mock === true` rather than on truthiness, which §7.6.2 requires because an
 * unconditional disclaimer becomes a disclaimer on real data the day real factors
 * are published.
 *
 * **The translation plumbing is kept, and that is not a compromise.** D branched
 * from the 12 August `main` and built against contract v1.17; interface
 * translation landed at v1.24-v1.27 underneath. Dropping the plumbing would take
 * `<html lang>`/`dir` and the language chooser off this page — both of which
 * `tests/web/test_i18n_browser.py` measures here specifically — and orphan every
 * catalogue key this page owns across twenty-one languages.
 *
 * So: wherever D kept a string, HEAD's exact English and its `t()` call are kept
 * with it. **Batch two, which is this state of the file, translated the rest** —
 * every section title, intro, column header and empty state D introduced, plus
 * the mock-data warning — deliberately after batch one, so it translated the
 * strings as they ended up rather than as they arrived.
 *
 * **What is still deliberately English is not an omission.** Every *value* in
 * these tables is either a staff-typed field (§7.7.7: published exactly as
 * written — `source_note`, `data_quality`, notes, and every `code`), a decimal
 * that crosses the wire as a string and is printed as it arrived, or a metric
 * unit. The page translates its own furniture and republishes the service's
 * content untouched, which is what makes a translated documentation page still
 * a record of what was published.
 */

import { ApiError, getFactors } from './api.js'
import { applyDocumentLanguage, applyToDocument, installLanguageChooser, t } from './i18n.js'

applyDocumentLanguage()
applyToDocument()

const content = document.getElementById('factor-content')
const status = document.getElementById('factor-status')
let loadGeneration = 0
let pageActive = true

// The fetched payload and the failure are held so that a language change
// re-renders from what was already fetched rather than calling the API again.
// Re-fetching would work and is wrong: the factors do not depend on the
// language, and a page that hits the API every time somebody reads a label in
// another language is a page that rate-limits itself.
let factors = null
let failure = null

function element(tagName, options = {}, children = []) {
  const node = document.createElement(tagName)
  if (options.className) node.className = options.className
  if (options.text !== undefined) node.textContent = String(options.text)
  for (const [name, value] of Object.entries(options.attributes || {})) {
    node.setAttribute(name, String(value))
  }
  for (const child of children) {
    if (child !== null && child !== undefined) node.append(child)
  }
  return node
}

function recorded(value, fallback = null) {
  const absent = fallback === null ? t('Not supplied') : fallback
  return value === null || value === undefined || value === '' ? absent : String(value)
}

function makeHeading(id, title, introduction) {
  const heading = element('h2', { text: title, attributes: { id } })
  const children = [heading]
  if (introduction) children.push(element('p', { text: introduction, className: 'methodology-section-intro' }))
  return children
}

function makeTable({ caption, columns, rows }) {
  const table = element('table')
  table.append(element('caption', { text: caption }))
  const headerRow = element('tr')
  for (const column of columns) {
    headerRow.append(element('th', { text: column.label, attributes: { scope: 'col' } }))
  }
  table.append(element('thead', {}, [headerRow]))

  const body = element('tbody')
  for (const row of rows) {
    const tableRow = element('tr')
    columns.forEach((column, index) => {
      const cell = element(index === 0 ? 'th' : 'td', {
        text: column.value(row),
        attributes: index === 0 ? { scope: 'row' } : {},
      })
      if (column.code) {
        const code = element('code', { text: cell.textContent })
        cell.replaceChildren(code)
      }
      tableRow.append(cell)
    })
    body.append(tableRow)
  }
  table.append(body)
  return element('div', {
    className: 'table-scroll',
    attributes: {
      tabindex: '0',
      role: 'region',
      'aria-label': t('%(caption)s, horizontally scrollable', { caption }),
    },
  }, [table])
}

function makeCollectionSection(id, title, introduction, collection, emptyMessage, tableOptions) {
  const section = element('section', {
    className: 'review-block methodology-factor-section',
    attributes: { 'aria-labelledby': id },
  }, makeHeading(id, title, introduction))
  if (!Array.isArray(collection) || collection.length === 0) {
    section.append(element('p', { className: 'empty-state', text: emptyMessage }))
  } else {
    section.append(makeTable({ ...tableOptions, rows: collection }))
  }
  return section
}

function makeMockWarning() {
  const copy = element('div', {}, [
    element('strong', { text: t('Placeholder data') }),
    element('p', { text: t('This published factor set is marked as mock and is not a verified scientific result.') }),
  ])
  return element('aside', {
    className: 'disclaimer methodology-warning',
    attributes: { role: 'alert', 'aria-label': t('Placeholder data') },
  }, [element('span', { className: 'info-icon', text: '!', attributes: { 'aria-hidden': 'true' } }), copy])
}

/**
 * The factor-set metadata this page publishes, and **nothing else**.
 *
 * **`id` was here and is gone.** §1.1 makes `code` the cross-layer identifier and
 * §7 forbids the front end learning a database primary key; a projection is not a
 * permission. `Object.hasOwn` filtered it today only because §6.3's `factor_set`
 * happens to carry four keys, so the page rendered nothing and looked correct —
 * and it would have started printing a primary key on a public page the day B
 * added `id` to the projection, with no test anywhere failing.
 *
 * The list is now exactly §6.3's four fields for the same reason. `name`,
 * `version` and `effective_from` were equally dead, and a list that renders
 * whatever the response happens to carry is a list that publishes whatever the
 * response happens to carry. Adding a field here is a contract change first.
 */
const METADATA_FIELDS = [
  ['version_label', () => t('Version'), factor_set => factor_set.version_label],
  ['published_at', () => t('Published'), factor_set => factor_set.published_at],
  ['notes', () => t('Notes'), factor_set => factor_set.notes],
  ['is_mock', () => t('Mock data'), factor_set => factor_set.is_mock],
]

function makeMetadata(factor_set) {
  const section = element('section', {
    className: 'review-block methodology-factor-section',
    attributes: { 'aria-labelledby': 'factor-set-heading' },
  }, makeHeading('factor-set-heading', t('Factor set'), t('Metadata identifying the factor set returned by the calculator service.')))
  const descriptionList = element('dl', { className: 'review-destinations methodology-metadata' })
  const fields = METADATA_FIELDS.filter(([key]) => Object.hasOwn(factor_set, key))

  if (fields.length === 0) {
    section.append(element('p', { className: 'empty-state', text: t('No factor-set metadata was returned.') }))
    return section
  }
  for (const [key, label, read] of fields) {
    const fieldValue = read(factor_set)
    const value = key === 'is_mock'
      ? (fieldValue === true ? t('Yes') : fieldValue === false ? t('No') : t('Not supplied'))
      : recorded(fieldValue)
    descriptionList.append(element('div', {}, [element('dt', { text: label() }), element('dd', { text: value })]))
  }
  section.append(descriptionList)
  return section
}

function renderFactors(payload) {
  const fragment = document.createDocumentFragment()
  const factor_set = payload?.factor_set && typeof payload.factor_set === 'object' ? payload.factor_set : {}
  if (factor_set.is_mock === true) fragment.append(makeMockWarning())
  fragment.append(makeMetadata(factor_set))

  fragment.append(makeCollectionSection(
    'constants-heading',
    t('Published constants'),
    t('Constants are named values referenced by published formulas.'),
    payload?.constants,
    t('No published constants were returned.'),
    {
      caption: t('Published constants'),
      columns: [
        { label: t('Code'), value: row => recorded(row?.code), code: true },
        { label: t('Value'), value: row => recorded(row?.value) },
        { label: t('Unit'), value: row => recorded(row?.unit) },
        { label: t('Note'), value: row => recorded(row?.note) },
      ],
    },
  ))

  fragment.append(makeCollectionSection(
    'formulas-heading',
    t('Published formulas'),
    t('Expressions are shown exactly as supplied by the service.'),
    payload?.formulas,
    t('No published formulas were returned.'),
    {
      caption: t('Published formulas'),
      columns: [
        { label: t('Metric'), value: row => recorded(row?.metric), code: true },
        { label: t('Expression'), value: row => recorded(row?.expression), code: true },
        { label: t('Notes'), value: row => recorded(row?.notes) },
      ],
    },
  ))

  fragment.append(makeCollectionSection(
    'upstream-heading',
    t('Upstream factors'),
    t('A destination of All destinations means that the row applies unless a destination-specific upstream factor is available.'),
    payload?.upstream,
    t('No upstream factors were returned.'),
    {
      caption: t('Published upstream impact factors'),
      columns: [
        { label: t('Sector'), value: row => recorded(row?.sector), code: true },
        { label: t('Food category'), value: row => recorded(row?.food_category, t('All food categories')), code: true },
        { label: t('Destination'), value: row => recorded(row?.destination, t('All destinations')), code: true },
        { label: t('Metric'), value: row => recorded(row?.metric), code: true },
        { label: t('Value per kg'), value: row => recorded(row?.value_per_kg) },
        { label: t('Source note'), value: row => recorded(row?.source_note) },
        { label: t('Data quality'), value: row => recorded(row?.data_quality) },
      ],
    },
  ))

  fragment.append(makeCollectionSection(
    'downstream-heading',
    t('Downstream factors'),
    // §2.2 (v1.31): a downstream row has **two** optional scopes now, and the
    // order between them is not something a reader can infer from the rows —
    // both "All sectors" and "All food categories" appear in the same table
    // and neither column says which one gives way. The sentence has to.
    t('A sector of All sectors, or a food category of All food categories, is a row that applies wherever no more specific row exists. Where a row naming a sector and a row naming only a food category could both apply, the one naming a sector is used. Negative values are retained because they represent published offsets.'),
    payload?.downstream,
    t('No downstream factors were returned.'),
    {
      caption: t('Published downstream impact factors'),
      columns: [
        { label: t('Destination'), value: row => recorded(row?.destination), code: true },
        { label: t('Sector'), value: row => recorded(row?.sector, t('All sectors')), code: true },
        { label: t('Food category'), value: row => recorded(row?.food_category, t('All food categories')), code: true },
        { label: t('Metric'), value: row => recorded(row?.metric), code: true },
        { label: t('Value per kg'), value: row => recorded(row?.value_per_kg) },
        { label: t('Source note'), value: row => recorded(row?.source_note) },
        { label: t('Data quality'), value: row => recorded(row?.data_quality) },
      ],
    },
  ))

  fragment.append(makeCollectionSection(
    'equivalences-heading',
    t('Published equivalences'),
    t('Equivalences translate a source metric into a more familiar comparison.'),
    payload?.equivalences,
    t('No published equivalences were returned.'),
    {
      caption: t('Published metric equivalences'),
      columns: [
        { label: t('Code'), value: row => recorded(row?.code), code: true },
        { label: t('Name'), value: row => recorded(row?.name) },
        { label: t('Source metric'), value: row => recorded(row?.source_metric), code: true },
        { label: t('Value per unit'), value: row => recorded(row?.value_per_unit) },
        // The template itself is staff-authored and §3 interpolates it in the
        // engine, so the *value* stays as published; only this header moves.
        { label: t('Label template'), value: row => recorded(row?.label_template) },
        { label: t('Source note'), value: row => recorded(row?.source_note) },
        { label: t('Sort order'), value: row => recorded(row?.sort_order) },
      ],
    },
  ))
  content.replaceChildren(fragment)
}

function renderApiError(message) {
  status.replaceChildren(element('div', { className: 'error-state' }, [
    element('h2', { text: t('Methodology unavailable') }),
    element('p', { text: message }),
  ]))
}

/** Redraw from what is already held, in whatever language is now active. */
function render() {
  if (failure !== null) {
    content.replaceChildren()
    renderApiError(failure)
    return
  }
  if (factors === null) return
  status.replaceChildren()
  renderFactors(factors)
}

installLanguageChooser(render)

export async function loadMethodology() {
  const generation = ++loadGeneration
  status.replaceChildren(element('p', { className: 'loading-state', text: t('Loading the published methodology…') }))
  content.setAttribute('aria-busy', 'true')
  try {
    const payload = await getFactors()
    if (!pageActive || generation !== loadGeneration) return
    factors = payload
    failure = null
    render()
  } catch (error) {
    if (!pageActive || generation !== loadGeneration) return
    if (!(error instanceof ApiError)) throw error
    factors = null
    failure = error.message
    render()
  } finally {
    if (pageActive && generation === loadGeneration) content.setAttribute('aria-busy', 'false')
  }
}

window.addEventListener('pagehide', () => {
  pageActive = false
  loadGeneration += 1
}, { once: true })

await loadMethodology()
