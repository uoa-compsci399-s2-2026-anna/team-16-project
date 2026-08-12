import { ApiError, getFactors } from './api.js'

const content = document.getElementById('factor-content')
const status = document.getElementById('factor-status')
let loadGeneration = 0
let pageActive = true

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

function recorded(value, fallback = 'Not recorded') {
  return value === null || value === undefined || value === '' ? fallback : String(value)
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
    attributes: { tabindex: '0', role: 'region', 'aria-label': `${caption}, horizontally scrollable` },
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
    element('strong', { text: 'Placeholder data warning' }),
    element('p', { text: 'This published factor set is marked as mock. Its values are placeholders and must not be treated as verified scientific results.' }),
  ])
  return element('aside', {
    className: 'disclaimer methodology-warning',
    attributes: { role: 'alert', 'aria-label': 'Placeholder data warning' },
  }, [element('span', { className: 'info-icon', text: '!', attributes: { 'aria-hidden': 'true' } }), copy])
}

const METADATA_FIELDS = [
  ['id', 'ID', factor_set => factor_set.id],
  ['name', 'Name', factor_set => factor_set.name],
  ['version', 'Version', factor_set => factor_set.version],
  ['version_label', 'Version label', factor_set => factor_set.version_label],
  ['effective_from', 'Effective date', factor_set => factor_set.effective_from],
  ['published_at', 'Published', factor_set => factor_set.published_at],
  ['notes', 'Notes', factor_set => factor_set.notes],
  ['is_mock', 'Mock data', factor_set => factor_set.is_mock],
]

function makeMetadata(factor_set) {
  const section = element('section', {
    className: 'review-block methodology-factor-section',
    attributes: { 'aria-labelledby': 'factor-set-heading' },
  }, makeHeading('factor-set-heading', 'Factor set', 'Metadata identifying the factor set returned by the calculator service.'))
  const descriptionList = element('dl', { className: 'review-destinations methodology-metadata' })
  const fields = METADATA_FIELDS.filter(([key]) => Object.hasOwn(factor_set, key))

  if (fields.length === 0) {
    section.append(element('p', { className: 'empty-state', text: 'No factor-set metadata was returned.' }))
    return section
  }
  for (const [key, label, read] of fields) {
    const fieldValue = read(factor_set)
    const value = key === 'is_mock'
      ? (fieldValue === true ? 'Yes' : fieldValue === false ? 'No' : 'Not recorded')
      : recorded(fieldValue)
    descriptionList.append(element('div', {}, [element('dt', { text: label }), element('dd', { text: value })]))
  }
  section.append(descriptionList)
  return section
}

function renderFactors(factors) {
  const fragment = document.createDocumentFragment()
  const factor_set = factors?.factor_set && typeof factors.factor_set === 'object' ? factors.factor_set : {}
  if (factor_set.is_mock === true) fragment.append(makeMockWarning())
  fragment.append(makeMetadata(factor_set))

  fragment.append(makeCollectionSection(
    'constants-heading',
    'Published constants',
    'Constants are named values referenced by published formulas.',
    factors?.constants,
    'No published constants were returned.',
    {
      caption: 'Published constants',
      columns: [
        { label: 'Code', value: row => recorded(row?.code), code: true },
        { label: 'Value', value: row => recorded(row?.value) },
        { label: 'Unit', value: row => recorded(row?.unit) },
        { label: 'Note', value: row => recorded(row?.note) },
      ],
    },
  ))

  fragment.append(makeCollectionSection(
    'formulas-heading',
    'Published formulas',
    'Expressions are shown exactly as supplied by the service.',
    factors?.formulas,
    'No published formulas were returned.',
    {
      caption: 'Published formulas',
      columns: [
        { label: 'Metric', value: row => recorded(row?.metric), code: true },
        { label: 'Expression', value: row => recorded(row?.expression), code: true },
        { label: 'Notes', value: row => recorded(row?.notes) },
      ],
    },
  ))

  fragment.append(makeCollectionSection(
    'upstream-heading',
    'Upstream factors',
    'A destination of All destinations means that the row applies unless a destination-specific upstream factor is available.',
    factors?.upstream,
    'No upstream factors were returned.',
    {
      caption: 'Published upstream impact factors',
      columns: [
        { label: 'Sector', value: row => recorded(row?.sector), code: true },
        { label: 'Food category', value: row => recorded(row?.food_category, 'All food categories'), code: true },
        { label: 'Destination', value: row => recorded(row?.destination, 'All destinations'), code: true },
        { label: 'Metric', value: row => recorded(row?.metric), code: true },
        { label: 'Value per kg', value: row => recorded(row?.value_per_kg) },
        { label: 'Source note', value: row => recorded(row?.source_note) },
        { label: 'Data quality', value: row => recorded(row?.data_quality) },
      ],
    },
  ))

  fragment.append(makeCollectionSection(
    'downstream-heading',
    'Downstream factors',
    'A food category of All food categories is the generic row used where no category-specific factor is available. Negative values are retained because they represent published offsets.',
    factors?.downstream,
    'No downstream factors were returned.',
    {
      caption: 'Published downstream impact factors',
      columns: [
        { label: 'Destination', value: row => recorded(row?.destination), code: true },
        { label: 'Food category', value: row => recorded(row?.food_category, 'All food categories'), code: true },
        { label: 'Metric', value: row => recorded(row?.metric), code: true },
        { label: 'Value per kg', value: row => recorded(row?.value_per_kg) },
        { label: 'Source note', value: row => recorded(row?.source_note) },
        { label: 'Data quality', value: row => recorded(row?.data_quality) },
      ],
    },
  ))

  fragment.append(makeCollectionSection(
    'equivalences-heading',
    'Published equivalences',
    'Equivalences translate a source metric into a more familiar comparison.',
    factors?.equivalences,
    'No published equivalences were returned.',
    {
      caption: 'Published metric equivalences',
      columns: [
        { label: 'Code', value: row => recorded(row?.code), code: true },
        { label: 'Name', value: row => recorded(row?.name) },
        { label: 'Source metric', value: row => recorded(row?.source_metric), code: true },
        { label: 'Value per unit', value: row => recorded(row?.value_per_unit) },
        { label: 'Label template', value: row => recorded(row?.label_template) },
        { label: 'Source note', value: row => recorded(row?.source_note) },
        { label: 'Sort order', value: row => recorded(row?.sort_order) },
      ],
    },
  ))
  content.replaceChildren(fragment)
}

function renderApiError(error) {
  status.replaceChildren(element('div', { className: 'error-state' }, [
    element('h2', { text: 'Methodology unavailable' }),
    element('p', { text: error.message }),
  ]))
}

export async function loadMethodology() {
  const generation = ++loadGeneration
  status.replaceChildren(element('p', { className: 'loading-state', text: 'Loading the published methodology…' }))
  content.setAttribute('aria-busy', 'true')
  try {
    const factors = await getFactors()
    if (!pageActive || generation !== loadGeneration) return
    renderFactors(factors)
    status.replaceChildren()
  } catch (error) {
    if (!pageActive || generation !== loadGeneration) return
    if (!(error instanceof ApiError)) throw error
    renderApiError(error)
  } finally {
    if (pageActive && generation === loadGeneration) content.setAttribute('aria-busy', 'false')
  }
}

window.addEventListener('pagehide', () => {
  pageActive = false
  loadGeneration += 1
}, { once: true })

await loadMethodology()
