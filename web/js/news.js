import { ApiError, getNewsPosts } from './api.js'

const EMPTY_POST = Object.freeze({
  title: '',
  excerpt: '',
  link: '',
  date: '',
  imageUrl: '',
})

function renderedText(value) {
  if (typeof value !== 'string' || !value) return ''

  // Template contents are inert: WordPress markup is never attached to the active
  // document and embedded images/scripts cannot become live page content.
  const template = document.createElement('template')
  template.innerHTML = value
  template.content.querySelectorAll('script, style, template').forEach(element => element.remove())
  return (template.content.textContent || '').replace(/\s+/g, ' ').trim()
}

function httpUrl(value) {
  if (typeof value !== 'string') return ''
  const candidate = value.trim()
  if (!candidate) return ''

  try {
    const parsed = new URL(candidate)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? candidate : ''
  } catch {
    return ''
  }
}

function isoDate(value) {
  if (typeof value !== 'string') return ''
  const candidate = value.trim()
  const match = /^(\d{4})-(\d{2})-(\d{2})T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,3})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?$/.exec(candidate)
  if (!match || !Number.isFinite(Date.parse(candidate))) return ''

  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
  return month >= 1 && month <= 12 && day >= 1 && day <= daysInMonth[month - 1]
    ? candidate
    : ''
}

function normalisePost(post) {
  if (post === null || typeof post !== 'object') return { ...EMPTY_POST }
  const featuredMedia = post._embedded?.['wp:featuredmedia']
  return {
    title: renderedText(post.title?.rendered),
    excerpt: renderedText(post.excerpt?.rendered),
    link: httpUrl(post.link),
    date: isoDate(post.date),
    // Normalised because §7.5 lists it, and rendered nowhere yet. `img-src` now follows
    // the configured news origin (docker/web-config.sh), so the origin is no longer a
    // guess — read the note on `createNewsCard` in home.js before wiring it up.
    imageUrl: httpUrl(Array.isArray(featuredMedia) ? featuredMedia[0]?.source_url : ''),
  }
}

/**
 * The latest posts, `[]` when the site could not be read, or **`null` when no news origin
 * is configured at all**.
 *
 * The third case is the one worth keeping distinct. `[]` means WordPress was asked and
 * the answer was unusable, which is transient and worth telling a reader about. `null`
 * means this deployment has no WordPress — a supported arrangement, not a fault — and the
 * home page drops the whole section rather than standing a notice about a service nobody
 * configured.
 */
export async function fetchNews(limit = 6) {
  let posts
  try {
    posts = await getNewsPosts(limit)
  } catch (error) {
    if (error instanceof ApiError) return []
    throw error
  }
  if (posts === null) return null
  if (!Array.isArray(posts)) return []
  return posts.map(post => {
    const { title, excerpt, link, date, imageUrl } = normalisePost(post)
    return { title, excerpt, link, date, imageUrl }
  })
}
