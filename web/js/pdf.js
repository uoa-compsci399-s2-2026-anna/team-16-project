const PAGE_WIDTH = 595.28
const PAGE_HEIGHT = 841.89
const LEFT = 48
const TOP = 742
const BOTTOM = 58

const WIN_1252 = new Map([
  [0x20ac, 0x80], [0x201a, 0x82], [0x0192, 0x83], [0x201e, 0x84],
  [0x2026, 0x85], [0x2020, 0x86], [0x2021, 0x87], [0x02c6, 0x88],
  [0x2030, 0x89], [0x0160, 0x8a], [0x2039, 0x8b], [0x0152, 0x8c],
  [0x017d, 0x8e], [0x2018, 0x91], [0x2019, 0x92], [0x201c, 0x93],
  [0x201d, 0x94], [0x2022, 0x95], [0x2013, 0x96], [0x2014, 0x97],
  [0x02dc, 0x98], [0x2122, 0x99], [0x0161, 0x9a], [0x203a, 0x9b],
  [0x0153, 0x9c], [0x017e, 0x9e], [0x0178, 0x9f],
])

// The PDF uses Helvetica's WinAnsi encoding. Preserve its complete repertoire and replace
// glyphs the built-in font cannot display instead of emitting invalid UTF-8 into a PDF
// literal string. The report's source values and units are otherwise copied unchanged.
function winAnsi(text) {
  return [...String(text)].map(char => {
    const point = char.codePointAt(0)
    if (point >= 0x20 && point <= 0x7e) return char
    if (point >= 0xa0 && point <= 0xff) return char
    if (WIN_1252.has(point)) return String.fromCharCode(WIN_1252.get(point))
    if (char === '→') return '->'
    if (char === '×') return 'x'
    return '?'
  }).join('')
}

const pdfString = text => winAnsi(text)
  .replaceAll('\\', '\\\\')
  .replaceAll('(', '\\(')
  .replaceAll(')', '\\)')

function wrap(text, limit) {
  if (!text) return ['']
  const words = text.trim().split(/\s+/)
  const lines = []
  let current = ''
  for (const word of words) {
    if (!current) {
      current = word
    } else if (`${current} ${word}`.length <= limit) {
      current += ` ${word}`
    } else {
      lines.push(current)
      current = word
    }
  }
  if (current) lines.push(current)
  return lines.flatMap(line => {
    if (line.length <= limit) return [line]
    const pieces = []
    for (let start = 0; start < line.length; start += limit) pieces.push(line.slice(start, start + limit))
    return pieces
  })
}

function reportPages(report) {
  const source = report.split('\n')
  const title = source.shift() || 'Food Waste Impact Calculator - Results'
  const pages = [[]]
  let y = TOP
  let afterBlank = true

  const add = item => {
    if (y - item.height < BOTTOM) {
      pages.push([])
      y = TOP
    }
    pages.at(-1).push({ ...item, y })
    y -= item.height
  }

  for (const raw of source) {
    if (!raw.trim()) {
      y -= 7
      afterBlank = true
      continue
    }
    const bullet = /^\s+-\s/.test(raw)
    const heading = afterBlank && !bullet && !raw.includes(':')
    const indent = bullet ? 13 : 0
    const size = heading ? 13 : 9.5
    const height = heading ? 21 : 14
    const limit = bullet ? 82 : 88
    const prefix = bullet ? '- ' : ''
    const body = bullet ? raw.replace(/^\s+-\s/, '') : raw.trim()
    wrap(body, limit).forEach((line, index) => add({
      text: `${index === 0 ? prefix : '  '}${line}`,
      font: heading ? 'F2' : 'F1',
      size,
      height,
      indent,
      colour: heading ? '0 0.25 0.18' : '0.13 0.22 0.19',
    }))
    afterBlank = false
  }
  return { title, pages }
}

function pageStream(title, items, pageNumber, pageCount) {
  const commands = [
    'q',
    '0 0.25 0.18 rg',
    `0 ${PAGE_HEIGHT - 72} ${PAGE_WIDTH} 72 re f`,
    '0.16 0.78 0.48 rg',
    `0 ${PAGE_HEIGHT - 76} ${PAGE_WIDTH} 4 re f`,
    'Q',
    'BT /F2 10 Tf 1 1 1 rg 48 801 Td (KAI COMMITMENT) Tj ET',
    `BT /F2 19 Tf 1 1 1 rg 48 779 Td (${pdfString(title)}) Tj ET`,
  ]
  for (const item of items) {
    commands.push(`BT /${item.font} ${item.size} Tf ${item.colour} rg ${LEFT + item.indent} ${item.y} Td (${pdfString(item.text)}) Tj ET`)
  }
  commands.push('0.78 0.84 0.81 RG 48 40 m 547 40 l S')
  commands.push(`BT /F1 8 Tf 0.35 0.43 0.4 rg 48 25 Td (Food Waste Impact Calculator) Tj ET`)
  commands.push(`BT /F1 8 Tf 0.35 0.43 0.4 rg 506 25 Td (Page ${pageNumber} of ${pageCount}) Tj ET`)
  return `${commands.join('\n')}\n`
}

function binaryBytes(value) {
  return Uint8Array.from(value, char => char.charCodeAt(0) & 0xff)
}

/** Build a valid, paginated A4 PDF from the same report content formerly saved as TXT. */
export function buildTextReportPdf(report) {
  const { title, pages } = reportPages(report)
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>',
  ]
  const pageIds = []
  pages.forEach((items, index) => {
    const pageId = objects.length + 1
    const contentId = pageId + 1
    pageIds.push(pageId)
    const stream = pageStream(title, items, index + 1, pages.length)
    objects.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAGE_WIDTH} ${PAGE_HEIGHT}] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents ${contentId} 0 R >>`)
    objects.push(`<< /Length ${stream.length} >>\nstream\n${stream}endstream`)
  })
  objects[1] = `<< /Type /Pages /Kids [${pageIds.map(id => `${id} 0 R`).join(' ')}] /Count ${pages.length} >>`

  let pdf = '%PDF-1.4\n%\xE2\xE3\xCF\xD3\n'
  const offsets = [0]
  objects.forEach((object, index) => {
    offsets.push(pdf.length)
    pdf += `${index + 1} 0 obj\n${object}\nendobj\n`
  })
  const xref = pdf.length
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`
  pdf += offsets.slice(1).map(offset => `${String(offset).padStart(10, '0')} 00000 n \n`).join('')
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`
  return binaryBytes(pdf)
}
