/**
 * The navigation drawer's *enhancements*. Contract §7.9.
 *
 * **The drawer works without this file, and that is the whole design.** It is a
 * `<details>` element: the browser opens and closes it, gives it a keyboard, and
 * announces its expanded state to a screen reader, with no script at all. `home.html`,
 * `stats.html` and `methodology.html` are static content that must not lose their
 * navigation when scripting is off, and a drawer built out of a button and a class
 * toggle would have done exactly that. The calculator renders nothing without
 * JavaScript anyway, so it loses nothing either way.
 *
 * What this module adds is the two things `<details>` alone cannot do:
 *
 * 1. **`aria-expanded` on the handle.** The handle is a triangle and the direction it
 *    points is the only visible statement of the drawer's state — a shape cannot say
 *    "expanded" to anybody who is not looking at it. Browsers do map `<summary>`'s
 *    open state to the accessibility tree, so this is belt and braces rather than the
 *    only route; it is written **only from JavaScript**, never into the markup,
 *    because an `aria-expanded="false"` shipped in HTML on a page with no scripting
 *    is a lie the moment the drawer is opened, and a stale one is worse than none.
 *
 * 2. **`Escape`, with focus returned to the handle.** Closing a drawer and leaving
 *    focus on a link that is no longer rendered strands a keyboard user in the
 *    document body. **Focus is moved on that path and on no other**: this project has
 *    one accessibility regression on record from a focus call placed where it fired
 *    on every state change, which threw keyboard users out of a radio group they were
 *    arrow-keying through. The `toggle` listener below therefore only writes an
 *    attribute.
 *
 * @module drawer
 */

/**
 * Wire one drawer. Idempotent, and a no-op on a page that has none.
 *
 * @param {Document} root Document to search. Injectable so a test can pass its own.
 * @returns {HTMLDetailsElement|null} The drawer, or `null` if the page has none.
 */
export function installDrawer(root = document) {
  const drawer = root.querySelector('#site-drawer')
  if (!drawer || drawer.dataset.drawerReady === 'true') return drawer || null
  const handle = drawer.querySelector('summary')
  if (!handle) return null
  drawer.dataset.drawerReady = 'true'

  const describeState = () => handle.setAttribute('aria-expanded', String(drawer.open))
  describeState()
  drawer.addEventListener('toggle', describeState)

  // On the document, not on the drawer: `Escape` has to work from wherever focus
  // happens to be, which after tabbing into the panel is one of the links inside it
  // and after a mouse click is the body.
  root.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !drawer.open) return
    drawer.open = false
    handle.focus()
  })

  return drawer
}

if (typeof document !== 'undefined') installDrawer()
