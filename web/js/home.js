/**
 * The home page: the hero, and the Kai Commitment news feed. Contract §7.5, §7.7.
 *
 * **The posts themselves are never translated.** Title, excerpt and date come
 * from the client's WordPress site; they are published content in the language
 * the client wrote them in, which is the same rule §7.7.7 states for anything
 * staff can edit. What is translated is the furniture around them — the fallback
 * wording when a post carries no title, the date-unavailable line, the link text
 * and its accessible name — so a Thai reader gets a Thai page listing English
 * headlines rather than an English page.
 */

import { fetchNews } from './news.js'
import { applyDocumentLanguage, applyToDocument, installLanguageChooser, t } from './i18n.js?v=20260816-1'
// The site drawer's `Escape` handler and `aria-expanded`. Side-effect import: the

let latestRequestGeneration = 0
// Held so a language change re-renders the cards without asking WordPress again.
let latestPosts = null

function element(tag, options = {}) {
  const node = document.createElement(tag)
  if (options.className) node.className = options.className
  if (options.text !== undefined) node.textContent = String(options.text)
  for (const [name, value] of Object.entries(options.attributes || {})) {
    node.setAttribute(name, String(value))
  }
  return node
}

function displayDate(value) {
  const date = new Date(value)
  if (typeof value !== 'string' || !value || Number.isNaN(date.getTime())) return null

  const time = element('time', { attributes: { datetime: value } })
  // `en-NZ`, not the active language. A date *format* is O-4 — localisation
  // beyond language — which is open and promises nothing; §7.7.7 keeps figures
  // off locale-aware formatting on both surfaces.
  time.textContent = new Intl.DateTimeFormat('en-NZ', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  }).format(date)
  return time
}

function safeNewsLink(value) {
  if (typeof value !== 'string' || !value.trim()) return ''
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : ''
  } catch {
    return ''
  }
}

/**
 * **`post.imageUrl` is still not rendered, but the CSP no longer blocks it and the
 * order of work has changed. Read this before you add the `<img>` — the previous
 * version of this note told you to work in an order that no longer applies.**
 *
 * `news.js` normalises `imageUrl` from `wp:featuredmedia`, so the field is on every
 * object this receives and is a plausible thing to reach for. Nothing renders it,
 * which is why the page works; nothing has been added here because rendering the
 * image is a design change and this note is not one.
 *
 * **What changed.** `img-src` used to be a flat `'self' data:`, and widening it meant
 * *guessing* the media origin — kaicommitment.org.nz serves the client's site, but
 * WordPress media commonly comes off a CDN (`i0.wp.com` and friends when Jetpack is
 * on), and a directive widened to the wrong host is both looser than before and still
 * broken. That argument was about a guess, and there is no guess left to make: the
 * news origin is now `KAICALC_NEWS_ORIGIN`, and `docker/web-config.sh` builds
 * `img-src` from the same value it builds `connect-src` and `config.js` from. It
 * grants nothing new in practice either — `connect-src` already reaches that origin.
 *
 * So on a configured deployment an `<img src={post.imageUrl}>` whose host is the news
 * site loads, and on an unconfigured one there is no news section to put it in.
 *
 * **The one case still to check is media on a separate host.** If the client's
 * library serves from a CDN rather than from the site origin, that host is not in
 * `img-src` and the image is refused — silently, as a card with a broken image and a
 * console line nobody is reading. `KAICALC_NEWS_IMAGE_ORIGINS` exists for exactly
 * that: a space-separated list added to `img-src` and to nothing else.
 *
 * It is not left to be discovered by a user, either.
 * `tests/web/test_csp.py::test_no_public_page_violates_its_own_policy` loads this page
 * with the policy enforced and collects every `securitypolicyviolation` the document
 * raises, so the first commit that renders an image from an unlisted host fails that
 * test by name with the blocked URI in the message — which is where the CDN's real
 * origin can be read off.
 *
 * So: add the `<img>`, run that test, and if it fails put the host it names into
 * `KAICALC_NEWS_IMAGE_ORIGINS`. No file in this repository has to change for that,
 * which is the difference from before.
 */
export function createNewsCard(post = {}) {
  const article = element('article', { className: 'home-news-card' })
  // The post's own title and excerpt are the client's published words and pass
  // through untouched; only the fallbacks, which this page wrote, are translated.
  const title = typeof post.title === 'string' && post.title.trim()
    ? post.title.trim()
    : t('Kai Commitment update')
  const excerpt = typeof post.excerpt === 'string' && post.excerpt.trim()
    ? post.excerpt.trim()
    : t('Read the latest update from Kai Commitment.')
  const date = displayDate(post.date)
  const link = safeNewsLink(post.link)

  if (date) article.append(date)
  else article.append(element('p', { className: 'home-news-date', text: t('Date unavailable') }))

  article.append(element('h3', { text: title }))
  article.append(element('p', { text: excerpt }))

  if (link) {
    article.append(element('a', {
      className: 'home-news-link',
      text: t('Read this update'),
      attributes: {
        href: link,
        target: '_blank',
        rel: 'noopener noreferrer',
        'aria-label': t('Read “%(title)s” on the Kai Commitment website (opens in a new tab)', { title }),
      },
    }))
  }

  return article
}

export function renderNews(posts, target = document.querySelector('#news-feed')) {
  if (!target) return

  const usablePosts = Array.isArray(posts)
    ? posts.filter(post => post !== null && typeof post === 'object')
    : []

  if (usablePosts.length === 0) {
    target.replaceChildren(element('p', {
      className: 'empty-state',
      text: t('Latest news is temporarily unavailable. You can still use the calculator and explore the documentation.'),
      attributes: { role: 'status' },
    }))
    target.setAttribute('aria-busy', 'false')
    return
  }

  const fragment = document.createDocumentFragment()
  for (const post of usablePosts) fragment.append(createNewsCard(post))
  target.replaceChildren(fragment)
  target.setAttribute('aria-busy', 'false')
}

/**
 * Take the whole news section off the page.
 *
 * Used only when no news origin is configured, which is not a failure: most deployments
 * of this calculator have no WordPress behind them. The heading, the standfirst and the
 * feed all go, because leaving the furniture with an "unavailable" notice underneath it
 * reports an outage for a service that was never asked to exist — and the reader has no
 * way to tell those two apart.
 */
function removeNewsSection(target) {
  const section = target && typeof target.closest === 'function' ? target.closest('.home-news') : null
  const doomed = section || target
  if (doomed && typeof doomed.remove === 'function') doomed.remove()
}

export async function loadNews(options = {}) {
  const generation = ++latestRequestGeneration
  const target = options.target || document.querySelector('#news-feed')
  if (target) target.setAttribute('aria-busy', 'true')

  try {
    const posts = await (options.fetchNews || fetchNews)(6)
    if (generation !== latestRequestGeneration) return null
    // `null` is "this deployment has no news origin"; `[]` is "the site was asked and
    // gave nothing usable". Only the second is worth telling a reader about.
    if (posts === null) {
      latestPosts = null
      removeNewsSection(target)
      return null
    }
    latestPosts = posts
    renderNews(posts, target)
    return posts
  } catch (error) {
    if (generation === latestRequestGeneration) {
      latestPosts = []
      renderNews([], target)
    }
    throw error
  } finally {
    if (generation === latestRequestGeneration && target) {
      target.setAttribute('aria-busy', 'false')
    }
  }
}

/** Redraw the cards in whatever language is now active, from what was already
 *  fetched. The static chrome is `applyToDocument()`'s; this is the feed's. */
export function rerenderInActiveLanguage() {
  if (latestPosts !== null) renderNews(latestPosts)
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  const leavePage = () => {
    latestRequestGeneration += 1
  }
  window.addEventListener('pagehide', leavePage)
  window.addEventListener('beforeunload', leavePage)

  // **The language machinery is unconditional. Only the news is conditional.**
  //
  // These three lines used to sit inside the `#news-feed` guard below, which made
  // `<html lang>`, every `data-i18n` string and the language chooser itself hang off
  // whether this deployment happens to have a WordPress behind it. That held together
  // only by an accident of ordering — the feed element is in the parsed markup, and it
  // is `loadNews` that removes it later — so an unconfigured deployment kept its
  // chooser. Any edit that takes the news block out of `home.html`, or any page that
  // reaches this module without one, would have silently served an untranslated page
  // announced as `en-NZ` with no way to switch. A reader cannot repair that; it is not
  // a dependency worth having, and there is no reason for one.
  applyDocumentLanguage()
  applyToDocument()
  installLanguageChooser(rerenderInActiveLanguage)

  if (document.querySelector('#news-feed')) {
    loadNews().catch(error => {
      // API failures have already become an empty feed in fetchNews. Re-surface an
      // unexpected program defect without leaving it as a silent rejected promise.
      setTimeout(() => { throw error }, 0)
    })
  }
}
