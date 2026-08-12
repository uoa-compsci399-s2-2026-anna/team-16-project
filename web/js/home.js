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
