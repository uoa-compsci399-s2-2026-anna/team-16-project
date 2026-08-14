import { fetchNews } from './news.js'

let latestRequestGeneration = 0

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
 * **`post.imageUrl` is deliberately not rendered, and rendering it needs a CSP
 * change first. Read this before you add the `<img>`.**
 *
 * `news.js` normalises `imageUrl` from `wp:featuredmedia`, so the field is on
 * every object this receives and is a plausible thing to reach for. Nothing
 * renders it, which is why the page works.
 *
 * The public Content-Security-Policy (`docker/nginx.conf`, `location /`) sets
 * `img-src 'self' data:`. A WordPress featured image is on neither: it is on
 * whatever origin the client's media library serves from. An `<img>` built from
 * this field is refused by the browser, silently as far as the page is
 * concerned — a card with a broken image and a console line nobody is reading.
 *
 * **The directive was left as it is rather than widened now, on purpose.** The
 * origin is a guess until somebody looks: kaicommitment.org.nz serves the site,
 * but WordPress media commonly comes off a CDN (`i0.wp.com` and friends when
 * Jetpack is on), and a directive widened to the wrong host is both looser than
 * before and still broken. Widening a policy for a feature nobody has built is
 * how a CSP stops meaning anything.
 *
 * It is not left to be discovered by a user, either.
 * `tests/web/test_csp.py::test_no_public_page_violates_its_own_policy` loads
 * this page with the policy enforced and collects every
 * `securitypolicyviolation` the document raises, so the first commit that
 * renders a remote image fails that test by name, with the blocked URI in the
 * message — which is also the one place the real origin can be read off.
 *
 * So: add the `<img>`, run that test, take the origin out of the failure, add
 * it to `img-src` in `docker/nginx.conf` and to `REQUIRED_CSP` in
 * `tests/test_d_statistics_content.py`, in that order.
 */
export function createNewsCard(post = {}) {
  const article = element('article', { className: 'home-news-card' })
  const title = typeof post.title === 'string' && post.title.trim()
    ? post.title.trim()
    : 'Kai Commitment update'
  const excerpt = typeof post.excerpt === 'string' && post.excerpt.trim()
    ? post.excerpt.trim()
    : 'Read the latest update from Kai Commitment.'
  const date = displayDate(post.date)
  const link = safeNewsLink(post.link)

  if (date) article.append(date)
  else article.append(element('p', { className: 'home-news-date', text: 'Date unavailable' }))

  article.append(element('h3', { text: title }))
  article.append(element('p', { text: excerpt }))

  if (link) {
    article.append(element('a', {
      className: 'home-news-link',
      text: 'Read this update',
      attributes: {
        href: link,
        target: '_blank',
        rel: 'noopener noreferrer',
        'aria-label': `Read “${title}” on the Kai Commitment website (opens in a new tab)`,
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
      text: 'Latest news is temporarily unavailable. You can still use the calculator and explore the documentation.',
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

export async function loadNews(options = {}) {
  const generation = ++latestRequestGeneration
  const target = options.target || document.querySelector('#news-feed')
  if (target) target.setAttribute('aria-busy', 'true')

  try {
    const posts = await (options.fetchNews || fetchNews)(6)
    if (generation !== latestRequestGeneration) return null
    renderNews(posts, target)
    return posts
  } catch (error) {
    if (generation === latestRequestGeneration) renderNews([], target)
    throw error
  } finally {
    if (generation === latestRequestGeneration && target) {
      target.setAttribute('aria-busy', 'false')
    }
  }
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  const leavePage = () => {
    latestRequestGeneration += 1
  }
  window.addEventListener('pagehide', leavePage)
  window.addEventListener('beforeunload', leavePage)
  if (document.querySelector('#news-feed')) {
    loadNews().catch(error => {
      // API failures have already become an empty feed in fetchNews. Re-surface an
      // unexpected program defect without leaving it as a silent rejected promise.
      setTimeout(() => { throw error }, 0)
    })
  }
}
