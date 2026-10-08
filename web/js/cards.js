/**
 * The collapsible card, and the open set behind it.
 *
 * **It lives here rather than in `calculator.js` because a third screen needs it**
 * (v1.100). #134 built it for step 3, #138 and #142 took it for steps 2.5 and 4, and
 * the results page's improvement panel is the fourth consumer — one card per food
 * type, for #134's own reason on a screen that is not a step at all. `calculator.js`
 * already imports `improvement.js`, so `improvement.js` could not import back without
 * a cycle; the chrome moves instead, which is the answer that leaves one
 * implementation rather than two.
 *
 * **Everything below is the same code and the same reasoning, cut by line and pasted.**
 * Nothing was rewritten or summarised in the move: the notes #134, #138 and #142 wrote
 * into these functions are the reason they behave the way they do, and a paraphrase of
 * them in a new file would be a second statement of a decision rather than the decision.
 *
 * The open set itself stays in `state.openCards`, where it already was, so nothing here
 * owns state — these are a reader, two predicates and a renderer over it.
 */

import { state } from './state.js'
import { escapeHtml } from './view.js'
import { t } from './i18n.js'

/**
 * A card's key, as it is written into an attribute and read back out.
 *
 * `encodeURIComponent`, because a `leafKey` carries a NUL as its separator and an
 * attribute round-trips it only encoded. Shared with `calculator.js`, which imports it
 * for the same reason its own `leafOf` exists: the pair has to agree.
 */
export const keyAttr = key => encodeURIComponent(key)

/**
 * A collapsible card's identity, as a member of `state.openCards`.
 *
 * **The step is part of the id, not just the key.** Steps 3 and 4 both draw one card per
 * leaf, so `leafKey(leaf)` alone would make "dairy open on step 3" and "dairy open on
 * step 4" the same fact — and the two steps are answered minutes apart, with different
 * reasons to be open. NUL-joined for `leafKey`'s own reason: a step number followed by a
 * key that begins with a digit cannot otherwise be told from another pair.
 */
export const cardId = (step, key) => `${step}\u0000${key}`

/**
 * **Whether a step draws so few cards that folding buys nothing**, which is the half of
 * decision 2 the markup needs as well as the open set.
 *
 * `cardIsOpen` asks it to force a lone card open. `collapsibleCard` asks it to leave the
 * header's toggle **out of the markup entirely**: a card that cannot be shut has nothing
 * for a toggle to do, and a chevron that changes nothing when pressed is a worse answer
 * than no chevron — it is reachable by Tab, it carries `aria-expanded="true"` that never
 * becomes `false`, and a keyboard visitor is told a control exists that does not.
 *
 * One expression, asked twice, so the two cannot disagree about which cards are fixed.
 */
export const cardIsFixedOpen = count => count <= 1

/**
 * Whether one collapsible card is open right now.
 *
 * **`count <= 1` is decision 2 of #134, and it lives here so that every consumer of the
 * chrome gets it from one place:** *a single card is not collapsed*, because one food
 * type is the commonest journey and collapsing it opens the step on an empty screen. It
 * is asked before `state.openCards`, so a lone card cannot be closed at all — there is
 * nothing for folding to buy when there is nothing below it to be missed.
 *
 * Otherwise the default is collapsed, which is what an empty `openCards` says, and what
 * #134 asks for: the client's problem is a card below the fold being skipped, so the
 * step has to be short enough to see whole.
 *
 * **It is exercised since #138 and #142, and it was not before.** `leafPanel` returns the
 * plain single-leaf panel before any card is built, so a step-3 card is only ever one of
 * two or more; #134 recorded that replacing this clause with `false` left 27 of step 3's
 * tests green, and left the note that whichever of #138 and #142 landed first would be
 * what put a test under it. Both now draw a card at a count of one — one chosen category
 * on step 2.5, one food type on step 4 — so the clause is live, and
 * `test_card_folding_browser.py` measures it on both of them.
 *
 * @param {number} step The step that owns the card.
 * @param {string} key The card's own key — a `leafKey` on steps 3 and 4.
 * @param {number} count How many cards the step is drawing.
 */
export const cardIsOpen = (step, key, count) => cardIsFixedOpen(count) || (state.openCards || []).includes(cardId(step, key))

/** `state.openCards` with one card added, idempotently. */
export const openedCard = (step, key) => {
  const id = cardId(step, key)
  const open = state.openCards || []
  return open.includes(id) ? open : [...open, id]
}

/**
 * **What the completion badge says, in one place, because two things render it.**
 *
 * `collapsibleCard` prints it at render time and `updateCardBadges` rewrites it on a
 * keystroke — §7.2's documented exception, taken here for `updateCombinedTotal`'s exact
 * reason: a re-render per keystroke destroys the focused input, and a badge that only
 * moved on Continue would be a tick the visitor could not trust while typing. Two
 * writers, one definition of the two states, so neither can drift into saying something
 * the other does not.
 *
 * **Not colour-only.** A word and `data-state` for both states, plus a tick when
 * complete. Incomplete has no mark; its empty mark span is hidden. The tick is
 * `aria-hidden` and the word is not, so the button's accessible name carries the state in
 * words — a screen reader is told what a sighted reader is shown, which is an acceptance
 * criterion of #134 and not a nicety.
 *
 * `settled` comes from `leafSettled`, which reads `leafProblem` — see there for what the
 * tick promises and why the optional money figures cannot withhold it.
 */
export const cardStatus = settled => (settled
  ? { state: 'complete', mark: '✓', text: t('Complete') }
  : { state: 'incomplete', mark: '', text: t('Incomplete') })

/**
 * **The collapsible step card, built once** (#134, and the chrome #138 and #142 consume).
 *
 * One `<fieldset>`, a header that opens and closes it, a status badge, an optional strip
 * that stays visible while the card is shut, and a body that is `hidden` when it is. The
 * decisions below are baked in here rather than at the call sites, so that steps 2.5, 3
 * and 4 cannot land on a different version of any of them:
 *
 * * **`<legend>` carries the group's name and nothing else, and it is `.sr-only`.** The
 *   legend is what gives the `<fieldset>` its accessible name, so it cannot hold the
 *   badge too — the fieldset would then be named "Dairy Complete", and the name of a
 *   group of inputs would change while the visitor typed in them. The name a sighted
 *   reader sees is the header button's, which carries the badge beside it. (It also
 *   keeps `panel.querySelector('legend').textContent` the food's own name, which three
 *   browser tests dereference and one of them compares against the error message's
 *   prose.)
 * * **A `<button>`, not a `<details>`/`<summary>`.** `render()` replaces
 *   `main.innerHTML` on every `setState`, so a native `open` attribute is erased by the
 *   next unrelated update; the open set lives in `state.openCards` and is rendered out
 *   of it. A button is what `aria-expanded` belongs on, it is reached and operated by
 *   the keyboard with no code, and it has a stable `id` so that `main.js`'s subscriber
 *   returns focus to it across the re-render the toggle causes — which is what makes the
 *   state change *announced* rather than merely applied.
 * * **`hidden` rather than `display: none` in a class.** The body's controls must be
 *   unreachable by Tab and unfocusable while the card is shut, which `hidden` is defined
 *   to do; a class is one stylesheet mistake away from a focusable invisible input.
 * * **A lone card has no toggle at all**, because `cardIsFixedOpen` says it can never be
 *   shut. The header is then a plain `<div>` carrying the same name and the same badge,
 *   with no chevron, no `aria-expanded` and nothing in the tab order — see
 *   `cardIsFixedOpen` for why a control that changes nothing is the worse answer. The one
 *   declaration such a header must not inherit from `.step-card__toggle` is
 *   `cursor: pointer`, and it is neutralised by
 *   `.step-card__toggle--static { cursor: default }` in `web/css/styles.css`, beside the
 *   class it modifies. It shipped here as a `style` attribute, because this change was
 *   not permitted to touch that file; #160 moved it and the attribute is gone.
 * * **`always` is the strip the shut card still shows**, between the header and the body
 *   and so outside the `hidden`. #142 needs step 4's Remaining readable without opening
 *   anything; `leafSummary` already renders it with its unit and an `aria-live`, and
 *   `updateLine` already rewrites it in place on a keystroke, so putting the element it
 *   already builds outside the fold is the whole of that criterion and no figure is
 *   computed twice.
 * * **`min-inline-size: 0` on the `<fieldset>`, and it is load-bearing rather than
 *   tidiness.** It is `.form-panel.step-card`'s second declaration in
 *   `web/css/styles.css`; it shipped here as a `style` attribute for the same reason as
 *   the cursor above, and #160 moved it there too.
 *   A fieldset's initial `min-inline-size` is `min-content`, so unlike a
 *   `<div>` it REFUSES to shrink below the widest thing inside it — and step 4's
 *   `.destination-row` is a two-column grid whose minimum is 220 + 24 + 260 = 504px.
 *   Measured on this build: at 500px the card was forced to 588px and the document
 *   scrolled sideways to 608, at every width from 481 (where the row stops being one
 *   column) to about 610 (where 504px fits again). Before #142 those rows lived in a
 *   `<div class="leaf-group">`, which shrinks and lets `.destination-list`'s own
 *   `overflow: hidden` clip; the `<fieldset>` does not, and
 *   `tests/web/test_horizontal_overflow.py` measures only 320 and 390, where the row is
 *   a single column and the band is invisible, so the band has a test of its own in
 *   `tests/web/test_leaf_layout_browser.py`. The stylesheet carries the same fix in
 *   another dimension as `.choice-fieldset { min-width: 0 }`; it carried a third copy on
 *   the matrix's own inputs until #160 deleted the withdrawn grid's rules.
 *
 * **Neither declaration is in a `style` attribute any more.** Both were, while this
 * change was not permitted to touch `web/css/styles.css` — `min-inline-size: 0` above
 * and `cursor: default` on the static header — and v1.84's entry recorded both as owed
 * there. #160 moved them, to `.form-panel.step-card` and
 * `.step-card__toggle--static` respectively, and this function now emits no inline style
 * at all. Nothing else changed: the computed values and the measured boxes are the same
 * either way.
 *
 * **Deliberately plain.** The card's whole ground — background, border, radius — is one
 * element, `.step-card`, and it carries no `backdrop-filter`, no `transform` and no
 * positioned descendant of its own. WP2's frosted-glass token attaches there without
 * re-laying out anything inside it.
 *
 * @param {object} options
 * @param {number} options.step The step that owns the card; half of its id.
 * @param {string} options.key The card's own key; the other half.
 * @param {string} options.anchor A slug unique within the step, for the element ids.
 * @param {string} options.name The card's name, shown and announced.
 * @param {number} options.count How many cards the step is drawing — decision 2's input.
 * @param {boolean} options.forceOpen Open whatever the open set says, because the card
 *   holds a message about itself.
 * @param {{state: string, mark: string, text: string}} options.status What the badge
 *   reads; `cardStatus` on steps 3 and 4, `itemStatus` on step 2.5.
 * @param {string} options.body The card's contents, as HTML, hidden while it is shut.
 * @param {string} options.always HTML that stays visible while it is shut.
 * @param {string} options.extraClass Classes the consumer's own selectors need.
 * @param {string} options.dataAttr Data attributes the consumer's own handlers need.
 */
export function collapsibleCard({ step, key, anchor, name, count, forceOpen = false, status, body, always = '', extraClass = '', dataAttr = '' }) {
  const bodyId = `card-body--${anchor}`
  const fixed = cardIsFixedOpen(count)
  const open = forceOpen || cardIsOpen(step, key, count)
  const badge = `<span class="step-card__status" data-card-status data-state="${status.state}"><span class="step-card__mark" aria-hidden="true">${status.mark}</span><span data-card-status-text>${escapeHtml(status.text)}</span></span>`
  const header = fixed
    ? `<div class="step-card__toggle step-card__toggle--static"><span class="step-card__name">${escapeHtml(name)}</span>${badge}</div>`
    : `<button id="card-toggle--${anchor}" class="step-card__toggle" type="button" data-action="toggle-card" data-card-step="${step}" data-card="${keyAttr(key)}" aria-expanded="${open ? 'true' : 'false'}" aria-controls="${bodyId}"><span class="step-card__chevron ${open ? 'expanded' : ''}" aria-hidden="true">&#8964;</span><span class="step-card__name">${escapeHtml(name)}</span>${badge}</button>`
  return `<fieldset class="form-panel step-card ${open ? 'step-card--open' : ''} ${extraClass}" ${dataAttr}><legend class="sr-only">${escapeHtml(name)}</legend>${header}${always}<div class="step-card__body" id="${bodyId}" ${open ? '' : 'hidden'}>${body}</div></fieldset>`
}
