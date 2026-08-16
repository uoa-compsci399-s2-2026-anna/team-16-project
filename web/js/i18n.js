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
 * **A stored choice first, then `navigator.languages`.** The chooser at the top
 * of every page writes one cookie; absent a choice — or with the choice set to
 * "follow the system" — the browser's own list decides, exactly as it did
 * before the chooser existed. See `readStoredChoice` for what is stored and why
 * it is not the fingerprint §2.3 forbids.
 *
 * **Only the highest-priority tag is consulted.** If it has no catalogue the
 * page renders in English; the rest of the list is not walked. The reasoning
 * is on `negotiate` below, and it is the same rule `admin/i18n.py::negotiate`
 * implements against `Accept-Language` — the two surfaces cannot be allowed to
 * answer a visitor differently.
 *
 * `navigator.languages` is still read rather than `navigator.language`,
 * because it is the *ordered* list and order is what identifies the
 * highest-priority tag. What changed is how far down it the negotiation is
 * allowed to reach, not which property is read.
 *
 * **The calculator is static files served by nginx and never reaches
 * FastAPI**, which is why this is done here and not with `Accept-Language`.
 * There is no server in the path that could negotiate, and nothing to cache
 * wrongly: every visitor is served the same HTML and the browser decides. The
 * admin panel does the opposite, for the opposite reason — it renders through
 * FastAPI, so it reads the header and sets `Vary: Accept-Language`.
 *
 * `?lang=` forces a language for one page load. **Nothing emits it — including
 * the chooser** — and it writes nothing: it exists for testing, screenshots and
 * support, and a link somebody shares must not silently re-language the
 * recipient's browser for good. **An unrecognised value is ignored**, and the
 * page then negotiates as though it had not been there — the stored choice,
 * then the browser's highest-priority tag, then English. It is deliberately
 * outside the single-tag rule: `?lang=` is somebody typing a language on
 * purpose rather than a browser setting, so a typo in it falls back to the
 * negotiation rather than consuming it. `?lang=qq` on a Chinese browser is
 * still Chinese.
 *
 * The parameter and the chooser cannot be mistaken for one another because they
 * share no mechanism: the chooser is a `<select>` that writes a cookie and
 * re-renders in place, and it never touches the URL.
 *
 * ## Why the chooser is built here rather than sitting in `index.html`
 *
 * **The calculator is ES modules end to end.** With scripting off there is no
 * calculator at all — no steps, no taxonomy, no results, and `<main>` still
 * says "Loading calculator…". A chooser that needs JavaScript therefore adds no
 * degradation the page did not already have, and building it here means it can
 * never exist as a control that is present and does nothing. `index.html`
 * carries a `<noscript>` note about the calculator as a whole, which is the
 * honest statement; the language control is not what broke.
 *
 * The panel is the opposite case and is deliberately built the opposite way: it
 * renders through FastAPI, works without JavaScript, and so its chooser is a
 * real `<form method="post">`. See `admin/i18n.py`.
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
 * The one thing this system stores about a visitor's language.
 *
 * **A cookie rather than `localStorage`, and the usual reason is wrong.** Both
 * surfaces are the same origin — nginx serves `/` and `/admin` on one port — so
 * `localStorage` would in fact be shared between them. The decisive reason is
 * that **the panel renders server-side and has to know the language before it
 * emits HTML**, and `localStorage` is unreadable at that moment. One cookie at
 * path `/` is the only mechanism both a Python process and a browser can read,
 * which is what keeps this a single choice rather than two that drift apart.
 *
 * ## Why this is not the fingerprint §2.3 forbids
 *
 * §2.3 forbids storing an IP address, a user agent or a browser fingerprint.
 * Two independent properties keep this cookie outside that, and **both are
 * needed** — this is the reason, not a reassurance:
 *
 * 1. **It records something the visitor deliberately declared**, not something
 *    inferred from their browser. Reading `Accept-Language` and forgetting it,
 *    and storing "this visitor chose English", are different acts with
 *    different justifications. The first observes; the second obeys.
 * 2. **Its value space is closed, tiny and free of entropy** — twenty-one
 *    possible values, shared identically by everyone who picks the same
 *    language. A field that cannot distinguish two visitors cannot correlate
 *    them, whatever else it records.
 *
 * The second is the load-bearing half. Property 1 on its own would equally
 * justify storing a name somebody typed into a form, which would be a
 * fingerprint by any measure. **It is the absence of entropy, not the presence
 * of consent, that makes this incapable of identifying anyone.**
 *
 * ## What it is not entangled with
 *
 * `submission.token` — the anonymous de-duplication token, which expires after
 * an hour — is a different name, a different lifetime and a different purpose.
 * This cookie does not appear in `submission`, in `audit_log` or in the access
 * log, and it neither extends nor refreshes that token. Because it sits at path
 * `/`, which cannot be scoped away when the panel and the calculator both need
 * it, the browser also attaches it to `/api/v1/calculate`; **the API receives it
 * and ignores it**, which is tested rather than assumed.
 */
export const COOKIE_NAME = 'kaicalc_lang'

/**
 * "Follow the system" is **a stored value, not the absence of one.**
 *
 * The obvious reason is that "chose to follow" and "never chose" would
 * otherwise be indistinguishable and the chooser could not show what is in
 * effect. The stronger reason is mechanical: reverting to follow-the-system
 * becomes an ordinary write rather than a cookie deletion. Deleting a cookie
 * reliably means re-sending it with `Max-Age=0` and an exactly matching path
 * and domain, and getting that wrong leaves the old value in place — the
 * chooser appears to revert and then snaps back on the next page. Writing
 * `auto` has no such failure mode.
 */
export const FOLLOW_SYSTEM = 'auto'

/** A year. Long enough that a returning visitor keeps their choice; finite so
 *  that an abandoned browser does not carry it forever. */
const COOKIE_MAX_AGE = 31536000

/**
 * The stored choice, or `null` when there is none to honour.
 *
 * `auto` and an unrecognised value both answer `null`, which is what makes an
 * unknown value harmless: a cookie naming a language that has since been
 * removed negotiates from the browser instead of rendering a blank page.
 */
export function readStoredChoice(jar, index) {
  const raw = String(jar || '')
    .split(';')
    .map((part) => part.trim())
    .filter((part) => part.startsWith(`${COOKIE_NAME}=`))
    .map((part) => decodeURIComponent(part.slice(COOKIE_NAME.length + 1)))
    .pop()
  if (!raw || raw === FOLLOW_SYSTEM) return null
  // Matched through the same lookup as everything else, so a stored `zh-TW`
  // reaches Traditional Chinese rather than being compared as a literal.
  return (index && match(raw, index)) || null
}

/**
 * Resolve one page load's language. **The order is the contract**, and it is
 * the same order `admin/i18n.py::resolve` implements.
 *
 * `?lang=` is matched first and separately — never prepended to the browser's
 * list — so an unrecognised value falls through to the stored choice rather
 * than consuming the single slot the negotiation consults.
 */
export function resolve(forced, stored, preferred, index) {
  return match(forced, index) || stored || negotiate(preferred, index)
}

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
 * A tag nobody claims returns `null` rather than a guess. `negotiate` turns
 * that into English; it does **not** try the visitor's next preference, and
 * the reason is written out there.
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
 * The catalogue for the visitor's **highest-priority** tag, or English.
 *
 * **Only `preferred[0]` is consulted. The rest of the list is not walked.**
 * This is the rule most likely to be "fixed" back, because walking the list is
 * what RFC 4647 lookup does with a list and is the more obvious reading of a
 * browser that offers several languages. It was the behaviour here until
 * 2026-08-13, and the repository owner ruled against it. The reasoning:
 *
 * - **A browser's language list does not reliably describe what a person can
 *   read.** The first entry is usually deliberate. The second and third are
 *   frequently residue — a preinstalled system locale, an input method added
 *   once, a setting changed years ago and forgotten. Honouring them as a
 *   genuine second language means letting an unreliable signal override a
 *   reliable fallback.
 * - **English is a safe floor for this audience and an unfamiliar language is
 *   not.** Everyone who reaches this calculator reads English; that is the
 *   assumption the ruling makes explicit. The worst outcome under this rule is
 *   an English page. The worst outcome under the walk is a page in a language
 *   the reader does not have — and with no picker, cannot navigate out of.
 *
 * The rule removes the walk, not the lookup: `en-NZ` still reaches `en`,
 * `zh-CN` still reaches `zh`, and `zh-TW` still reaches Traditional Chinese
 * through its catalogue's own claim, because all three happen *inside*
 * `match` on that one tag.
 *
 * @param {string[]} preferred `navigator.languages`, in the browser's own
 *   priority order. A forced `?lang=` is **not** passed through here — it is
 *   matched by the caller before this runs, so that an unrecognised one falls
 *   back to this negotiation instead of consuming its single slot.
 */
export function negotiate(preferred, index) {
  return match((preferred || [])[0], index) || DEFAULT_LANGUAGE
}

const active = {
  language: DEFAULT_LANGUAGE,
  endonym: 'English',
  machineTranslated: false,
  dir: 'ltr',
  strings: {},
  //: What is *stored*, which is not what is rendered. With `auto` stored and a
  //: Chinese browser the page is Chinese and the chooser reads "Follow the
  //: system" — the chooser has to show the choice, not its consequence.
  choice: FOLLOW_SYSTEM,
  //: Every catalogue the manifest declares, kept so the chooser can label its
  //: own options from the one fetch this module already makes.
  catalogues: [],
  index: {},
}

/** English is not a catalogue file — its strings are the keys — so it is
 *  synthesised here exactly as `admin/i18n.py::_load` synthesises it, and for
 *  the same reason: a file mapping every string to itself would be a second
 *  place for the English wording to drift. */
const ENGLISH_OPTION = {
  language: DEFAULT_LANGUAGE,
  endonym: 'English',
  machine_translated: false,
}

/** Every language the chooser can offer, English first, then the manifest's own
 *  order — which is the client's census ordering, not alphabetical. */
export function offeredLanguages() {
  return [ENGLISH_OPTION, ...active.catalogues]
}

export const activeChoice = () => active.choice

async function applyCatalogue(language) {
  if (language === DEFAULT_LANGUAGE) {
    active.language = DEFAULT_LANGUAGE
    active.endonym = 'English'
    active.machineTranslated = false
    active.dir = 'ltr'
    active.strings = {}
    return
  }
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

async function load() {
  // **The scheme, not the presence of `window`.** `tests/web/` runs these
  // modules under Node against a taxonomy it builds - the seam
  // `buildResultsReport` and `entryDestinations` were pulled out for - and it
  // supplies a `window` stub of its own, so a `typeof window` check passes
  // there and then throws on `fetch('file:///…')`, which Node refuses. Under
  // Node the module URL is `file:`; in a browser it is `http:` or `https:`.
  // English is the right answer for the Node runs: `t()` becomes the identity
  // and every existing assertion on English output keeps holding.
  if (!LOCALES_BASE.protocol.startsWith('http')) return
  if (typeof window === 'undefined' || typeof fetch !== 'function') return
  const forced = new URLSearchParams(window.location?.search || '').get('lang')
  const manifest = await readJson(new URL('index.json', LOCALES_BASE))
  if (!manifest) return
  active.catalogues = manifest.catalogues || []
  // **English is put into the index, and the manifest does not list it.**
  // English has no catalogue file - its strings are the keys - so `index.json`
  // has nothing to say about it. Building the lookup from the manifest alone
  // therefore made `'en'` an unknown tag, and both `readStoredChoice` and
  // `storeChoice` rejected it: choosing English stored `auto`, which on a
  // Chinese browser rendered Chinese. That is the single case this whole
  // feature was asked for, and it silently did nothing.
  //
  // Negotiation is unaffected. `match('en-NZ')` used to return null and let
  // `negotiate` fall through to `DEFAULT_LANGUAGE`; it now truncates to `'en'`
  // and returns it. Same answer, one step earlier.
  const index = tagIndex([{ language: DEFAULT_LANGUAGE, tags: ['en'] }, ...active.catalogues])
  active.index = index

  const stored = readStoredChoice(document.cookie, index)
  active.choice = stored || FOLLOW_SYSTEM

  // The forced value is matched on its own rather than pushed onto the front
  // of the list. Since only the head of the list is consulted, prepending it
  // would make an unrecognised `?lang=` eat the browser's own first
  // preference and land every typo on English. It is also matched ahead of the
  // stored choice — and writes nothing, so a shared link re-languages exactly
  // one page load and leaves the recipient's own choice standing.
  await applyCatalogue(
    resolve(forced, stored, navigator.languages || [navigator.language], index)
  )
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
 *
 * **Both halves pin their English key before the first overwrite**, because the
 * chooser makes this function run more than once per page. `data-i18n` with no
 * value means "my own text is the key", and after one pass that text is Thai —
 * a second pass would look up the Thai as a key, find nothing, and leave the
 * page stuck in the first language it was switched to. The key is therefore
 * written back into the empty `data-i18n` attribute, and the original attribute
 * values are held in a `WeakMap`, so every later pass translates from the
 * English source rather than from the previous translation.
 */
const ATTRIBUTE_SOURCES = new WeakMap()

export function applyToDocument(root = document) {
  for (const element of root.querySelectorAll('[data-i18n]')) {
    const key = element.getAttribute('data-i18n') || element.textContent.trim()
    element.setAttribute('data-i18n', key)
    element.textContent = t(key)
  }
  for (const element of root.querySelectorAll('[data-i18n-attr]')) {
    let sources = ATTRIBUTE_SOURCES.get(element)
    if (!sources) {
      sources = {}
      ATTRIBUTE_SOURCES.set(element, sources)
    }
    for (const name of element.getAttribute('data-i18n-attr').split(',')) {
      const attribute = name.trim()
      if (!(attribute in sources)) sources[attribute] = element.getAttribute(attribute)
      const value = sources[attribute]
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
  // **Rebuilt on every call, not only created once.** The chooser makes this
  // run again after a language change, and the notice has to follow: leaving a
  // machine-translated language must remove it, because a notice that outlived
  // the language it warned about is a false statement about a reviewed page,
  // and moving between two machine-translated languages must re-word it, or
  // Thai would be warned about in Arabic.
  const existing = document.getElementById('machine-translation-notice')
  if (existing) existing.remove()
  if (!active.machineTranslated) return
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

/** The chooser's own three strings. Module-level constants rather than inline
 *  literals so `tests/web/i18n_keys.py` can find them by reference the way it
 *  finds `MACHINE_TRANSLATION_NOTICE`. */
export const LANGUAGE_LABEL = 'Language'
export const FOLLOW_SYSTEM_LABEL = 'Follow the system'
export const MACHINE_TRANSLATED_OPTION = '%(language)s — machine translated'

/**
 * Write the choice down and re-render the page in it.
 *
 * **A write, never a delete** — including for "follow the system", which stores
 * the literal `auto`. See `FOLLOW_SYSTEM` for why a delete is the wrong shape.
 *
 * `SameSite=Lax` because nothing here is submitted cross-site; no `Secure`,
 * because the cookie carries no secret and `Secure` would stop it working on
 * the `http://localhost:18080` this stack is developed and demonstrated on; no
 * `HttpOnly`, because this line is the thing that writes it.
 */
export function storeChoice(value) {
  const safe = value === FOLLOW_SYSTEM || active.index[String(value).toLowerCase()]
    ? value
    : FOLLOW_SYSTEM
  document.cookie =
    `${COOKIE_NAME}=${encodeURIComponent(safe)}; path=/; max-age=${COOKIE_MAX_AGE}; SameSite=Lax`
  active.choice = safe
  return safe
}

/**
 * Switch language in place, without reloading.
 *
 * **A reload would be simpler and is wrong.** The wizard's state lives in
 * memory (`web/js/state.js`), so reloading would throw away every entry
 * somebody had typed — changing language four steps into a calculation would
 * silently empty the form. So the catalogue is swapped, the static chrome is
 * re-translated, and the caller re-renders whatever it owns.
 */
export async function chooseLanguage(value, afterChange) {
  const stored = storeChoice(value)
  await applyCatalogue(
    stored === FOLLOW_SYSTEM
      ? negotiate(navigator.languages || [navigator.language], active.index)
      : stored
  )
  applyDocumentLanguage()
  applyToDocument()
  if (typeof afterChange === 'function') afterChange()
}

/** One option's visible text. **The endonym is never passed through `t()`** —
 *  a translated endonym is a contradiction, and the suite forbids a catalogue
 *  entry equal to its own English source. Only the marker around it is
 *  translated, and it renders in the language currently on screen, which is the
 *  language the person reading the list is reading. */
function optionLabel(entry) {
  if (!entry.machine_translated) return entry.endonym
  return t(MACHINE_TRANSLATED_OPTION, { language: entry.endonym })
}

/** The namespace an `<svg>` and its children must be created in. `createElement('svg')`
 *  produces an HTML element named "svg" that renders as nothing at all — the failure is
 *  silent and looks like a CSS problem, which is why this is a named constant rather
 *  than a literal repeated five times below. */
const SVG_NS = 'http://www.w3.org/2000/svg'

/**
 * The chooser's globe. **Drawn here, because it cannot be fetched.**
 *
 * The public Content-Security-Policy is `img-src 'self' data:` with no third-party
 * origin (`docker/nginx.conf`, contract §7.6 rule 7), there is no icon font, and there
 * is no build step to inline one with. So the icon is eleven attributes of SVG: a
 * sphere, an equator and a meridian ellipse, which is the smallest drawing that still
 * reads as a globe at 18px.
 *
 * **`aria-hidden`, and that is not a shortcut.** The `<label>` beside it already names
 * this control; a second name would make a screen reader say "globe, Language". It is
 * decoration for the eye, and below 720px — where the label is `.sr-only` and the word
 * is not drawn — it is the only thing on screen that says what the control is for.
 *
 * Colour comes from CSS (`stroke: currentColor`), not from attributes, so the same
 * drawing serves the white header and the Kale one without a second copy.
 */
function globeIcon() {
  const svg = document.createElementNS(SVG_NS, 'svg')
  svg.setAttribute('class', 'language-bar__globe')
  svg.setAttribute('viewBox', '0 0 20 20')
  svg.setAttribute('aria-hidden', 'true')
  // Keeps it out of the tab order in the browsers that once put SVG in it. The
  // control after it is the one thing here that should take focus.
  svg.setAttribute('focusable', 'false')

  const sphere = document.createElementNS(SVG_NS, 'circle')
  sphere.setAttribute('class', 'language-bar__globe-sphere')
  sphere.setAttribute('cx', '10')
  sphere.setAttribute('cy', '10')
  sphere.setAttribute('r', '7.4')

  const equator = document.createElementNS(SVG_NS, 'path')
  equator.setAttribute('d', 'M2.6 10h14.8')

  // The meridian is an ellipse rather than two arcs: one element, symmetric about both
  // axes, so it mirrors under `dir="rtl"` by being unchanged.
  const meridian = document.createElementNS(SVG_NS, 'ellipse')
  meridian.setAttribute('cx', '10')
  meridian.setAttribute('cy', '10')
  meridian.setAttribute('rx', '3.5')
  meridian.setAttribute('ry', '7.4')

  svg.append(sphere, equator, meridian)
  return svg
}

/**
 * Build the chooser and put it at the top inline-start of the page.
 *
 * **"Top left" is a physical direction and two of these languages render
 * right-to-left.** It is implemented as the *inline-start* of a bar above the
 * header, so it is top-left in English and top-right in Arabic and Urdu. The
 * whole layout was converted to logical properties for this reason and a test
 * refuses a physical direction property in the stylesheet; a control pinned
 * physically left in a mirrored page would land at the reading-end of the
 * header, opposite the logo it is meant to sit beside.
 *
 * The bar goes **between the machine-translation notice and the header**, not
 * above the notice: the notice is a statement about the whole page and its own
 * test pins it as `document.body.firstElementChild`.
 */
export function installLanguageChooser(afterChange) {
  if (document.getElementById('language-chooser')) return null
  const header = document.getElementById('site-header') || document.querySelector('.site-header')
  if (!header) return null

  const bar = document.createElement('div')
  bar.className = 'language-bar'
  const label = document.createElement('label')
  label.className = 'language-bar__label'
  label.setAttribute('for', 'language-chooser')
  const select = document.createElement('select')
  select.className = 'language-bar__select'
  select.id = 'language-chooser'
  select.name = 'lang'

  const paint = () => {
    label.textContent = t(LANGUAGE_LABEL)
    select.replaceChildren()
    const follow = document.createElement('option')
    follow.value = FOLLOW_SYSTEM
    follow.textContent = t(FOLLOW_SYSTEM_LABEL)
    select.append(follow)
    for (const entry of offeredLanguages()) {
      const option = document.createElement('option')
      option.value = entry.language
      option.lang = entry.language
      option.textContent = optionLabel(entry)
      select.append(option)
    }
    // The *choice*, not the rendered language: with `auto` stored on a Chinese
    // browser the page is Chinese and this still reads "Follow the system",
    // because that is what is in effect and what the person picked.
    select.value = active.choice
  }

  select.addEventListener('change', () => {
    chooseLanguage(select.value, () => {
      paint()
      if (typeof afterChange === 'function') afterChange()
    })
  })

  paint()
  // The globe is appended once, here, and not inside `paint()`: `paint()` runs again on
  // every language change and would otherwise stack a second drawing on each switch.
  bar.append(globeIcon(), label, select)
  // **Inside the header's own row, not in a strip above it, and the reason is
  // measured rather than aesthetic.** A strip of its own costs 57px on every
  // page: a 44px control plus padding and a rule. This calculator deleted an
  // 87px step-indicator band to stop short steps scrolling - `styles.css` says
  // "do not put the constant back" over the arithmetic that band left behind -
  // and `test_a_short_step_is_not_floored_by_a_stale_min_height` fails the
  // moment anything persistent is added above the fold. Verified both ways
  // with that test and `KAICALC_MUTATION_CSS`.
  //
  // The header is already 93px tall and holds a 67px logo, so a 44px control
  // fits in the space that is there. The bar therefore joins the existing row
  // and costs nothing, and "top inline-start" still describes where it is: it
  // is the first thing in the first row of the page.
  //
  // It is placed *beside* the brand lockup, never inside it - the logo keeps
  // its own element, its own size and its own spacing, and nothing is drawn
  // over or through it.
  const row = header.querySelector('.header-inner') || header
  row.append(bar)
  return select
}
