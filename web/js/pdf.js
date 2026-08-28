const PAGE_WIDTH = 595.28
const PAGE_HEIGHT = 841.89
const LEFT = 48
const TOP = 714
const BOTTOM = 62

// Kai Commitment brand palette (Brand Guidelines, August 2026).
const KALE_PDF = '0 0.196 0.137'
const PEA_PDF = '0.157 0.784 0.510'
const BODY_PDF = '0.13 0.22 0.19'
const KALE = '#003223'
const PEA = '#28C882'
const BODY = '#213832'

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
  // Scripts without word-separating spaces (for example Chinese and Japanese) need a
  // narrower character budget because one glyph is roughly twice as wide as a Latin
  // lowercase letter at the same font size.
  const effectiveLimit = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]/u.test(text)
    ? Math.floor(limit * 0.52)
    : limit
  const words = text.trim().split(/\s+/)
  const lines = []
  let current = ''
  for (const word of words) {
    if (!current) {
      current = word
    } else if (`${current} ${word}`.length <= effectiveLimit) {
      current += ` ${word}`
    } else {
      lines.push(current)
      current = word
    }
  }
  if (current) lines.push(current)
  return lines.flatMap(line => {
    const characters = [...line]
    if (characters.length <= effectiveLimit) return [line]
    const pieces = []
    for (let start = 0; start < characters.length; start += effectiveLimit) {
      pieces.push(characters.slice(start, start + effectiveLimit).join(''))
    }
    return pieces
  })
}

function reportPages(report) {
  const source = report.split('\n')
  const title = source.shift() || 'Food Waste Impact Calculator - Results'
  const groups = []
  let group = []
  source.forEach((line, index) => {
    if (line.trim()) group.push(index)
    else if (group.length) {
      groups.push(group)
      group = []
    }
  })
  if (group.length) groups.push(group)
  const summaryGroup = groups.find(indexes => indexes.length >= 2 && indexes.every(index => !/^\s+-\s/.test(source[index]) && /[:：]/.test(source[index]))) || []
  const summaryIndexes = new Set(summaryGroup)
  const summaryEnd = summaryGroup.at(-1) ?? -1
  const metadataGroup = groups.find(indexes => /^(Factor version|因子版本)[:：]/i.test(source[indexes[0]].trim())) || []
  const metadataIndexes = new Set(metadataGroup)
  const pages = [[]]
  let y = TOP

  const newPage = () => {
    pages.push([])
    y = TOP
  }

  const add = item => {
    pages.at(-1).push({ ...item, y })
    y -= item.height
  }

  for (const indexes of groups) {
    const block = []
    indexes.forEach((sourceIndex, position) => {
      const raw = source[sourceIndex]
      const bullet = /^\s+-\s/.test(raw)
      const hasColon = /[:：]/.test(raw)
      const heading = position === 0 && !bullet && !hasColon
      const nextLine = source[sourceIndex + 1]?.trim() || ''
      const entryHeading = position === 0 && sourceIndex > summaryEnd && !bullet && hasColon && /[:：]/.test(nextLine)
      const label = !bullet && /[:：]$/.test(raw.trim())
      const summary = summaryIndexes.has(sourceIndex)
      const metadata = metadataIndexes.has(sourceIndex)
      const kind = heading ? 'heading' : entryHeading ? 'entry' : label ? 'label' : summary ? 'summary' : metadata ? 'metadata' : bullet ? 'bullet' : 'body'
      const emphasis = ['heading', 'entry', 'label'].includes(kind)
      const indent = bullet ? 13 : 0
      const size = heading ? 14 : entryHeading ? 12 : summary ? 11.25 : label ? 10.75 : metadata ? 9.2 : 10.5
      const height = heading ? 24 : entryHeading ? 21 : summary ? 18 : label ? 18 : metadata ? 15 : 16.5
      const limit = metadata ? 112 : bullet ? 72 : 78
      const prefix = bullet ? '- ' : ''
      const body = bullet ? raw.replace(/^\s+-\s/, '') : raw.trim()
      wrap(body, limit).forEach((line, index) => block.push({
        text: `${index === 0 ? prefix : '  '}${line}`,
        font: emphasis ? 'F2' : 'F1',
        size,
        height,
        indent,
        kind,
        colour: emphasis ? KALE_PDF : BODY_PDF,
      }))
    })

    const groupGap = pages.at(-1).length ? 12 : 0
    const blockHeight = block.reduce((total, item) => total + item.height, 0)
    const usableHeight = TOP - BOTTOM
    const remainingHeight = y - groupGap - BOTTOM
    // Keep short sections together. Long entry sections may use the remaining space and
    // continue on the next page, avoiding a nearly empty page before a large block.
    if (blockHeight <= usableHeight && blockHeight > remainingHeight && (blockHeight <= 220 || remainingHeight < 110)) newPage()
    else y -= groupGap
    block.forEach((item, index) => {
      const keepWithNext = index === 0 && ['heading', 'entry'].includes(item.kind) && block[index + 1]
      let requiredHeight = item.height + (keepWithNext ? block[index + 1].height : 0)
      if (item.kind === 'label') {
        const nextLabel = block.findIndex((candidate, candidateIndex) => candidateIndex > index && candidate.kind === 'label')
        const sectionEnd = nextLabel === -1 ? block.length : nextLabel
        const sectionHeight = block.slice(index, sectionEnd).reduce((total, candidate) => total + candidate.height, 0)
        if (sectionHeight <= usableHeight) requiredHeight = sectionHeight
      }
      if (y - requiredHeight < BOTTOM) newPage()
      add(item)
    })
  }
  return { title, pages }
}

function circlePath(cx, cy, radius) {
  const k = 0.5522847498 * radius
  return `${cx + radius} ${cy} m ${cx + radius} ${cy + k} ${cx + k} ${cy + radius} ${cx} ${cy + radius} c ${cx - k} ${cy + radius} ${cx - radius} ${cy + k} ${cx - radius} ${cy} c ${cx - radius} ${cy - k} ${cx - k} ${cy - radius} ${cx} ${cy - radius} c ${cx + k} ${cy - radius} ${cx + radius} ${cy - k} ${cx + radius} ${cy} c`
}

function pageStream(title, items, pageNumber, pageCount) {
  const commands = [
    'q',
    `${KALE_PDF} rg`,
    `0 ${PAGE_HEIGHT - 84} ${PAGE_WIDTH} 84 re f`,
    `${PEA_PDF} rg`,
    `0 ${PAGE_HEIGHT - 88} ${PAGE_WIDTH} 4 re f`,
    `${PEA_PDF} RG 0.75 w`,
    `${circlePath(595, 805, 19)} S`,
    `${circlePath(595, 805, 31)} S`,
    `${circlePath(595, 805, 43)} S`,
    'Q',
    'BT /F2 10 Tf 1 1 1 rg 48 801 Td (KAI COMMITMENT) Tj ET',
    `BT /F2 19 Tf 1 1 1 rg 48 779 Td (${pdfString(title)}) Tj ET`,
  ]
  const summary = items.filter(item => item.kind === 'summary')
  if (summary.length) {
    const top = summary[0].y + 11
    const bottom = summary.at(-1).y - 7
    commands.push('0.94 0.98 0.96 rg')
    commands.push(`42 ${bottom} 511 ${top - bottom} re f`)
    commands.push(`${PEA_PDF} rg 42 ${bottom} 4 ${top - bottom} re f`)
  }
  for (const item of items) {
    if (item.kind === 'heading') {
      commands.push(`${PEA_PDF} rg 48 ${item.y - 3} 4 16 re f`)
    } else if (item.kind === 'entry') {
      commands.push('0.86 0.91 0.89 RG 0.5 w')
      commands.push(`48 ${item.y + 9} m 547 ${item.y + 9} l S`)
    }
    const headingOffset = item.kind === 'heading' ? 12 : 0
    commands.push(`BT /${item.font} ${item.size} Tf ${item.colour} rg ${LEFT + item.indent + headingOffset} ${item.y} Td (${pdfString(item.text)}) Tj ET`)
  }
  commands.push('0.78 0.84 0.81 RG 0.5 w 48 40 m 547 40 l S')
  commands.push(`BT /F1 8 Tf 0.35 0.43 0.4 rg 48 25 Td (Food Waste Impact Calculator) Tj ET`)
  commands.push(`BT /F1 8 Tf 0.35 0.43 0.4 rg 506 25 Td (Page ${pageNumber} of ${pageCount}) Tj ET`)
  return `${commands.join('\n')}\n`
}

function binaryBytes(value) {
  return Uint8Array.from(value, char => char.charCodeAt(0) & 0xff)
}

function hasWinAnsiGlyph(char) {
  const point = char.codePointAt(0)
  return (point >= 0x20 && point <= 0x7e)
    || (point >= 0xa0 && point <= 0xff)
    || WIN_1252.has(point)
    || char === '\n'
    || char === '\r'
    || char === '\t'
    || char === '→'
    || char === '×'
}

/** Whether the selectable-text PDF needs a browser-rendered Unicode fallback. */
export const reportNeedsUnicodeFallback = report => [...String(report)].some(char => !hasWinAnsiGlyph(char))

function concatBytes(parts) {
  const length = parts.reduce((total, part) => total + part.length, 0)
  const joined = new Uint8Array(length)
  let offset = 0
  for (const part of parts) {
    joined.set(part, offset)
    offset += part.length
  }
  return joined
}

function jpegBytes(dataUrl) {
  const binary = atob(dataUrl.split(',', 2)[1])
  return Uint8Array.from(binary, char => char.charCodeAt(0))
}

function imagePdf(images) {
  const objects = [
    binaryBytes('<< /Type /Catalog /Pages 2 0 R >>'),
    new Uint8Array(),
  ]
  const pageIds = []
  images.forEach(image => {
    const pageId = objects.length + 1
    const imageId = pageId + 1
    const contentId = pageId + 2
    const stream = `q\n${PAGE_WIDTH} 0 0 ${PAGE_HEIGHT} 0 0 cm\n/Im0 Do\nQ\n`
    pageIds.push(pageId)
    objects.push(binaryBytes(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAGE_WIDTH} ${PAGE_HEIGHT}] /Resources << /XObject << /Im0 ${imageId} 0 R >> >> /Contents ${contentId} 0 R >>`))
    objects.push(concatBytes([
      binaryBytes(`<< /Type /XObject /Subtype /Image /Width ${image.width} /Height ${image.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${image.bytes.length} >>\nstream\n`),
      image.bytes,
      binaryBytes('\nendstream'),
    ]))
    objects.push(binaryBytes(`<< /Length ${stream.length} >>\nstream\n${stream}endstream`))
  })
  objects[1] = binaryBytes(`<< /Type /Pages /Kids [${pageIds.map(id => `${id} 0 R`).join(' ')}] /Count ${images.length} >>`)

  const parts = [binaryBytes('%PDF-1.4\n%\xE2\xE3\xCF\xD3\n')]
  const offsets = [0]
  let length = parts[0].length
  objects.forEach((object, index) => {
    const wrapped = concatBytes([binaryBytes(`${index + 1} 0 obj\n`), object, binaryBytes('\nendobj\n')])
    offsets.push(length)
    parts.push(wrapped)
    length += wrapped.length
  })
  const xref = length
  const table = offsets.slice(1).map(offset => `${String(offset).padStart(10, '0')} 00000 n \n`).join('')
  parts.push(binaryBytes(`xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${table}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`))
  return concatBytes(parts)
}

function rasterTextReportPdf(report) {
  const { title, pages } = reportPages(report)
  const canvasWidth = 1240
  const scale = canvasWidth / PAGE_WIDTH
  const canvasHeight = Math.round(PAGE_HEIGHT * scale)
  const images = pages.map((items, index) => {
    const canvas = document.createElement('canvas')
    canvas.width = canvasWidth
    canvas.height = canvasHeight
    const context = canvas.getContext('2d')
    if (!context) throw new Error('Canvas is unavailable for the Unicode PDF export')
    const x = value => value * scale
    const y = value => (PAGE_HEIGHT - value) * scale
    context.fillStyle = '#ffffff'
    context.fillRect(0, 0, canvas.width, canvas.height)
    context.fillStyle = KALE
    context.fillRect(0, 0, canvas.width, x(84))
    context.fillStyle = PEA
    context.fillRect(0, x(84), canvas.width, x(4))
    context.strokeStyle = PEA
    context.lineWidth = x(0.75)
    for (const radius of [19, 31, 43]) {
      context.beginPath()
      context.arc(x(595), y(805), x(radius), 0, Math.PI * 2)
      context.stroke()
    }
    context.textBaseline = 'alphabetic'
    context.fillStyle = '#ffffff'
    context.font = `700 ${x(10)}px Geologica, Helvetica, Arial, sans-serif`
    context.fillText('KAI COMMITMENT', x(48), y(801))
    context.font = `700 ${x(19)}px Geologica, Helvetica, Arial, sans-serif`
    context.fillText(title, x(48), y(779), x(499))
    const summary = items.filter(item => item.kind === 'summary')
    if (summary.length) {
      const top = summary[0].y + 11
      const bottom = summary.at(-1).y - 7
      context.fillStyle = '#EFF9F4'
      context.fillRect(x(42), y(top), x(511), x(top - bottom))
      context.fillStyle = PEA
      context.fillRect(x(42), y(top), x(4), x(top - bottom))
    }
    for (const item of items) {
      if (item.kind === 'heading') {
        context.fillStyle = PEA
        context.fillRect(x(48), y(item.y + 13), x(4), x(16))
      } else if (item.kind === 'entry') {
        context.strokeStyle = '#DCE7E1'
        context.lineWidth = x(0.5)
        context.beginPath()
        context.moveTo(x(48), y(item.y + 9))
        context.lineTo(x(547), y(item.y + 9))
        context.stroke()
      }
      context.fillStyle = item.font === 'F2' ? KALE : BODY
      const family = item.font === 'F2' ? 'Geologica, Helvetica, Arial, sans-serif' : '"Kumbh Sans", Helvetica, Arial, sans-serif'
      context.font = `${item.font === 'F2' ? 700 : 400} ${x(item.size)}px ${family}`
      const headingOffset = item.kind === 'heading' ? 12 : 0
      context.fillText(item.text, x(LEFT + item.indent + headingOffset), y(item.y), x(499 - item.indent - headingOffset))
    }
    context.strokeStyle = '#c7d6cf'
    context.lineWidth = 1
    context.beginPath()
    context.moveTo(x(48), y(40))
    context.lineTo(x(547), y(40))
    context.stroke()
    context.fillStyle = '#596e66'
    context.font = `400 ${x(8)}px "Kumbh Sans", Helvetica, Arial, sans-serif`
    context.fillText('Food Waste Impact Calculator', x(48), y(25))
    context.textAlign = 'right'
    context.fillText(`Page ${index + 1} of ${pages.length}`, x(547), y(25))
    return { width: canvas.width, height: canvas.height, bytes: jpegBytes(canvas.toDataURL('image/jpeg', 0.92)) }
  })
  return imagePdf(images)
}

function vectorTextReportPdf(report) {
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

/** Build a valid, paginated A4 PDF from the same report content formerly saved as TXT. */
export function buildTextReportPdf(report) {
  if (reportNeedsUnicodeFallback(report) && typeof document !== 'undefined') {
    const probe = document.createElement?.('canvas')
    if (probe?.getContext) return rasterTextReportPdf(report)
  }
  return vectorTextReportPdf(report)
}
