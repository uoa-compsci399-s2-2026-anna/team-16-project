/**
 * Interface translation for the public calculator. Contract open item O-8.
 *
 * **The English source string is the key**, the same as `admin/i18n.py`.
 * `t('Start calculator')` looks up `'Start calculator'`. There is no key
 * namespace, a missing key renders its own English source, and a catalogue is
 * a JSON file whose whole content a translator can read side by side. The two
 * surfaces cannot share a reader — a Python process and a browser — so they
 * share the contract instead: one key rule, one fallback rule, one file shape,
 * one notice rule.
 *
 * ## How the language is chosen
 *
 * **From `navigator.languages`, per session, and nothing is stored.** No
 * picker, no cookie, no `localStorage`. The owner's ruling is that the browser
 * already says what it reads and the interface should simply honour it.
 *
 * `navigator.languages` and not `navigator.language`: the second is one tag,
 * and a visitor whose first preference this calculator has no catalogue for
 * would drop straight to English while their second preference sat unread in a
 * list the browser was already sending. The ordered list is the whole point.
 *
 * **The calculator is static files served by nginx and never reaches
 * FastAPI**, which is why this is done here and not with `Accept-Language`.
 * There is no server in the path that could negotiate, and nothing to cache
 * wrongly: every visitor is served the same HTML and the browser decides. The
 * admin panel does the opposite, for the opposite reason — it renders through
 * FastAPI, so it reads the header and sets `Vary: Accept-Language`.
 *
 * `?lang=` forces a language for one page load. Nothing emits it: it exists
 * for testing, screenshots and support. **An unrecognised value is ignored**,
 * and the page then negotiates as though it had not been there —
 * `navigator.languages` first, English last.
 *
 * ## The flash of English
 *
 * `index.html` ships with English in it and this module replaces that text
 * once its catalogue has loaded, so a slow connection shows English first.
 * That is the price of no build step: the alternative is a server that
 * renders per language, which the static origin deliberately is not. The
 * catalogues are small and same-origin, so the window is short.
 */

/** Where the catalogues live, resolved against this module rather than the
 *  site root — the same reason `api.js` resolves its fixtures that way, so
 *  serving the tree from any directory keeps working. */
const LOCALES_BASE = new URL('../locales/', import.meta.url)

/** The language the source strings are written in. It has no catalogue: for
 *  English the key *is* the answer. */
export const DEFAULT_LANGUAGE = 'en'

/** `en` renders as `en-NZ`: the copy is New Zealand English and the region is
 *  part of that. Every other language is its own tag. */
const DOCUMENT_LANGUAGE = { en: 'en-NZ' }

/** `%(name)s`, the placeholder syntax `admin/i18n.py` uses, so a translator
 *  meets one convention across both surfaces and one test covers both. */
const PLACEHOLDER = /%\((\w+)\)s/g

/**
 * nginx answers a missing file with a 302 to `/` (see docker/nginx.conf's
 * `@not_a_page`), so a fetch for a catalogue that does not exist resolves
 * with `ok: true` and an HTML body. Checking `redirected` is what tells the
 * two apart; without it a missing catalogue would throw a JSON parse error
 * instead of falling back to English.
 */
async function readJson(url) {
  const response = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!response.ok || response.redirected) return null
  try {
    return await response.json()
  } catch {
    return null
  }
}

/**
 * One BCP-47 tag to a language with a catalogue, or `null`.
 *
 * RFC 4647 lookup, and the same algorithm `admin/i18n.py::match` implements:
 * try the whole tag, then drop the last subtag, and repeat. `en-NZ` reaches
 * `en`; `zh-CN` and `zh-Hans` reach `zh`.
 *
 * **Truncation alone would send `zh-TW` to Simplified Chinese**, which is not
 * a graceful degradation — it is the wrong script. So a catalogue declares the
 * tags it speaks for in `index.json`, and an exact claim is matched before any
 * truncation runs. `zh-Hant` claims `zh-TW`, `zh-HK` and `zh-MO`.
 *
 * A tag nobody claims returns `null` rather than a guess, so the caller tries
 * the visitor's next preference before falling back to English.
 */
export function match(tag, index) {
  if (!tag) return null
  let normalised = String(tag).trim().toLowerCase().replaceAll('_', '-')
  while (normalised) {
    if (index[normalised]) return index[normalised]
    const cut = normalised.lastIndexOf('-')
    if (cut === -1) return null
    normalised = normalised.slice(0, cut)
  }
  return null
}

/** Every claimed tag, lower-cased, to the language claiming it. */
export function tagIndex(catalogues) {
  const index = {}
  for (const entry of catalogues || []) {
    for (const tag of [entry.language, ...(entry.tags || [])]) {
      const key = String(tag).trim().toLowerCase()
      if (!(key in index)) index[key] = entry.language
    }
  }
  return index
}

/**
 * The first of `candidates` that has a catalogue, or English.
 *
 * @param {string[]} candidates Ordered preference — a forced `?lang=` first,
 *   then `navigator.languages`.
 */
export function negotiate(candidates, index) {
  for (const candidate of candidates) {
    const language = match(candidate, index)
    if (language) return language
  }
  return DEFAULT_LANGUAGE
}

const active = {
  language: DEFAULT_LANGUAGE,
  endonym: 'English',
  machineTranslated: false,
  dir: 'ltr',
  strings: {},
}

async function load() {
  // `tests/web/` runs these modules under Node against a taxonomy it builds,
  // which is the seam `buildResultsReport` and `entryDestinations` were pulled
  // out for. There is no `window` and no catalogue to fetch there, and English
  // is the right answer: `t()` becomes the identity and every existing
  // assertion on English output keeps holding.
  if (typeof window === 'undefined' || typeof fetch !== 'function') return
  const forced = new URLSearchParams(window.location.search).get('lang')
  const manifest = await readJson(new URL('index.json', LOCALES_BASE))
  if (!manifest) return
  const index = tagIndex(manifest.catalogues)
  // `navigator.languages` is the ordered list the browser already sends; the
  // forced value goes in front of it rather than replacing it, so an
  // unrecognised `?lang=` leaves the negotiation exactly as it was.
  const candidates = [forced, ...(navigator.languages || [navigator.language])].filter(Boolean)
  const language = negotiate(candidates, index)
  if (language === DEFAULT_LANGUAGE) return
  const catalogue = await readJson(new URL(`${language}.json`, LOCALES_BASE))
  // A catalogue that is missing or malformed leaves the page in English. A
  // calculator that fails to render because a translation file did not load
  // would be a worse outcome than one that renders in the wrong language.
  if (!catalogue || !catalogue.strings) return
  active.language = catalogue.language
  active.endonym = catalogue.endonym || catalogue.language
  active.machineTranslated = Boolean(catalogue.machine_translated)
  active.dir = catalogue.dir === 'rtl' ? 'rtl' : 'ltr'
  active.strings = catalogue.strings
}

// Top-level await: every module that imports this one is guaranteed a loaded
// catalogue before its first render, so no screen has to be re-rendered when
// the translation arrives and no `t()` can run against an empty catalogue.
await load()

export const activeLanguage = () => active.language
export const activeEndonym = () => active.endonym
export const isMachineTranslated = () => active.machineTranslated
export const activeDirection = () => active.dir

/**
 * Translate one source string.
 *
 * @param {string} message The English source, which is also the key.
 * @param {object} [variables] Values for its `%(name)s` placeholders.
 * @returns {string}
 */
export function t(message, variables) {
  const translated = active.strings[message] ?? message
  if (!variables) return translated
  return translated.replace(PLACEHOLDER, (whole, name) =>
    (name in variables ? String(variables[name]) : whole))
}

/**
 * Translate the static HTML the browser parsed before this module ran.
 *
 * `data-i18n` with no value means "my own trimmed text is the key", which
 * keeps the English in exactly one place; a value means "use this key
 * instead", for the cases where the rendered text carries markup.
 * `data-i18n-attr` lists attributes whose current values are keys.
 */
export function applyToDocument(root = document) {
  for (const element of root.querySelectorAll('[data-i18n]')) {
    const key = element.getAttribute('data-i18n') || element.textContent.trim()
    element.textContent = t(key)
  }
  for (const element of root.querySelectorAll('[data-i18n-attr]')) {
    for (const name of element.getAttribute('data-i18n-attr').split(',')) {
      const attribute = name.trim()
      const value = element.getAttribute(attribute)
      if (value) element.setAttribute(attribute, t(value))
    }
  }
}

/**
 * The document's language and writing direction, and the notice.
 *
 * **`lang` is not decoration.** A page of Thai announced as `en-NZ` is read
 * aloud by a screen reader in English phonetics, which is unreadable rather
 * than merely wrong, and it is what tells a browser whether to offer its own
 * translation on top of ours.
 *
 * **The machine-translation notice goes here, at the top of the document, on
 * every page, and cannot be dismissed.** It used to hang off a language
 * switcher, on the option somebody was about to choose. With no switcher
 * there is nothing to hang it on, and the page itself is the only surface a
 * reader of a machine-translated page is guaranteed to be looking at. Driven
 * by `machine_translated` in the catalogue, so a language that arrives from a
 * machine pass labels itself and no list in this file has to be extended
 * nineteen times.
 *
 * It is written twice: once in the language being read, and once in English.
 * The English half is there because the first sentence a machine-translated
 * page has to get right is the one warning you it was machine translated, and
 * that sentence went through the same machine as the rest of the file.
 */
export function applyDocumentLanguage() {
  const root = document.documentElement
  root.lang = DOCUMENT_LANGUAGE[active.language] || active.language
  root.dir = active.dir
  if (!active.machineTranslated) return
  if (document.getElementById('machine-translation-notice')) return
  const notice = document.createElement('p')
  notice.id = 'machine-translation-notice'
  notice.className = 'machine-translation-notice'
  notice.setAttribute('role', 'status')
  const translated = document.createElement('span')
  translated.textContent = t(MACHINE_TRANSLATION_NOTICE)
  const english = document.createElement('span')
  english.className = 'machine-translation-notice-en'
  english.lang = 'en'
  english.textContent = MACHINE_TRANSLATION_NOTICE
  notice.append(translated, english)
  document.body.prepend(notice)
}

/** Exported so the results export can carry the same sentence as the screen. */
export const MACHINE_TRANSLATION_NOTICE =
  'This interface was machine translated and has not been reviewed by a speaker of this language. The figures are unaffected; the wording may be wrong.'
