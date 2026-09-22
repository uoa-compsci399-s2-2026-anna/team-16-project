/**
 * Step 5's reporting period: two typable date-and-time fields and a hand-built
 * calendar dialog behind each of them (v1.67, §6.2, §7.3a).
 *
 * **Why this module exists at all.** The client's period is a shift — 08:10 to
 * 16:20 — and a word from a list cannot say that. §6.2 gained `period_start` and
 * `period_end` for it, and the four presets became *templates*: pressing *One
 * week* fills the interval with the seven days ending now and `time_frame` goes
 * on recording which button was pressed.
 *
 * **Nothing is fetched and nothing is built.** §7.6 rule 7 forbids a runtime
 * asset from a third-party host, which is why Chart.js is vendored; the same
 * rule forbids a date-picker library, so the calendar below is written out by
 * hand in the plain ES modules the rest of `web/js` is written in.
 *
 * **The native controls were refused deliberately** (owner decision 2): the
 * client's team is on Apple hardware, so Safari's rendering is the one that
 * matters, and neither `<input type="date">` nor `<input type="time">` can be
 * relied on to look like — or behave like — the same control in the browser the
 * rest of the team uses. Both are therefore `type="text"`.
 *
 * ## The time control is typed **and** there is now a dial — a decision that
 * was reversed, on 2026-09-22, by the owner
 *
 * The first pass of this module, and PR #114 with it, recorded the opposite
 * ruling in this spot, and it is quoted here rather than deleted because the
 * reasoning behind it is still true and is the reason the dial has the shape it
 * has:
 *
 * > *"The time control is a text box, not a dial. A dial resolves 1,440
 * > positions, two selects make sixty minutes a scroll on the field whose whole
 * > job is to beat opening anything."*
 *
 * The three arguments under it were: a clock face is the wrong affordance at
 * minute precision, because a shift is *entered* — "0810" — and not rotated;
 * two `<select>`s lose on the same ground and double the tab stops per bound;
 * and a stepper needing 1,440 presses to cross a day is §7.3a's "a control that
 * exists and does nothing useful is worse than no control".
 *
 * **What changed.** The owner used the finished field and asked for the dial
 * (item 4 of five, 2026-09-22). That is their call and it is not relitigated
 * here. What the old reasoning buys is the *shape* of the answer, and it is
 * exactly why Android pairs a dial with a keyboard mode:
 *
 *   * **It is an addition, never a replacement.** The text box is unchanged and
 *     is still the fast path. Typing `0810` and tabbing straight past the clock
 *     button without opening anything both do today what they did before, and
 *     `tests/web/test_period_typing_browser.py` still says so.
 *   * **A dial cannot be driven by a keyboard and has no accessible name for
 *     1,440 positions**, which is the old paragraph's first argument restated as
 *     an accessibility fact. So the dialog carries two number boxes and a toggle
 *     beside the face, the face itself is `aria-hidden="true"`, and **which of
 *     the two a press opens is decided by how the button was pressed** — see
 *     `openClockMode`.
 *   * **Minutes snap to one minute and only every fifth minute is labelled.** A
 *     five-minute snap is the version of this control that would have deserved
 *     the old ruling: a roster that says 08:07 is real, and a dial that refused
 *     it would be slower *and* wrong. The labels are a reading aid; the
 *     resolution is one minute, which is `submission.period_start`'s own.
 *   * Seconds are still not offered, for the unchanged reason:
 *     `submission.period_start` is a `DATETIME` with no fractional precision and
 *     the requirement is minutes (§2.3).
 *
 * `inputmode="numeric"` on the four text boxes, rather than `type="number"`: a
 * number input will not hold `14/09/2026` at all, and `selectionStart` throws on
 * one — which is the same reason `calculator.js`'s minus guard cannot read a
 * caret. The clock dialog's own two boxes *are* `type="number"`: they hold one
 * integer each, nothing masks them, and nothing reads a caret out of them.
 *
 * `inputmode="numeric"` rather than `type="number"`: a number input will not
 * hold `14/09/2026` at all, and `selectionStart` throws on one — which is the
 * same reason `calculator.js`'s minus guard cannot read a caret.
 *
 * ## Dates are formatted `en-NZ`, in every language, and so are the month and
 * weekday names
 *
 * `stats.js`'s timestamp and `home.js`'s news date both pin
 * `Intl.DateTimeFormat('en-NZ', …)` with the reason written beside them: a date
 * *format* is O-4 (localisation beyond language), which is open and promises
 * nothing. The sentence around a date is translated; the date is not
 * reformatted.
 *
 * **The calendar's month names, weekday names and first day of the week are the
 * same question, and this module deliberately does not answer it.** Passing the
 * active language to `Intl` instead of `'en-NZ'` would give translated month
 * names for free, with no catalogue entries and no work — which is exactly why
 * it is tempting, and exactly why it is refused here. It would settle O-4 on one
 * screen, unilaterally, while `stats.js` and `home.js` go on pinning `en-NZ` two
 * pages away; the calculator would then print "septembre 2026" above a news list
 * dated "21 September 2026". **The alternative exists, it is one argument wide,
 * and it belongs to O-4** — when O-4 is decided, this module, `stats.js` and
 * `home.js` change together or not at all.
 *
 * `FIRST_DAY_OF_WEEK` follows from the same pin rather than being a second,
 * contradictory answer: `en-NZ` weeks start on Monday, so the grid does.
 *
 * ## Where the 24-hour rule lives
 *
 * **Here**, in `MAX_HOURS_AHEAD`. `api/schemas.py`'s `PERIOD_CEILING_HOURS` is
 * **38** and the two numbers are not a disagreement — read the comment over that
 * constant before touching either. The stored instants are local wall-clock time
 * carrying no zone, so the server cannot tell which side of the date line
 * `2026-09-22T17:55` was typed on and has to allow the 24 hours this form
 * permits plus the widest civil UTC offset in use (+14). This form is the one
 * place where "now" is the visitor's own now and the two clocks being compared
 * are the same clock, so this is where the real bound can be exact.
 *
 * **Do not copy 38 into this file and do not tighten the server to 24.** Either
 * change makes one of the two checks wrong: 38 here would let a visitor enter a
 * period a day and a half in the future, and 24 there would refuse a shift
 * somebody in Auckland entered correctly, at the time it happened, with nothing
 * in the payload they could change to make it pass.
 * `tests/web/test_period_form_bounds.py` reads both numbers out of both files
 * and fails if either moves.
 *
 * ## Surviving `render()`
 *
 * `render()` replaces `main.innerHTML` on every `setState`, so an open dialog
 * held in the DOM would close on the next keystroke anywhere on the step. Three
 * things therefore live in `state` and are re-derived from it on every render:
 *
 *   * **whether the dialog is open** — `state.periodPicker` is `null` or
 *     `{ field, cursor, openerId, view }`;
 *   * **which day the roving `tabindex` is on** — `state.periodPicker.cursor`,
 *     which also decides the month on screen, so there is no second key that can
 *     disagree with it;
 *   * **which of the three grids is drawn** — `state.periodPicker.view`, see
 *     below;
 *   * **whether the clock is open, on which bound, at which stage, in which
 *     mode, and over which hour and minute** — `state.periodClock` is `null` or
 *     `{ field, stage, mode, hours, minutes, openerId }`;
 *   * **what is typed in the four text boxes** — `state.periodFields`.
 *
 * Focus is restored by `main.js`, which re-focuses `document.activeElement.id`
 * after a same-step render. That works for the grid only because **the roving
 * cell's `id` names its role and not its date**: whichever cell is the cursor
 * carries `id="period-grid-focus"`, so an arrow key that moves the cursor moves
 * the id with it and the generic restore lands on the new cell. An id of
 * `period-day-2026-09-21` would survive the render intact and pull focus back
 * onto the day the visitor just left.
 *
 * Opening and closing are the two moves `main.js` cannot make on its own — the
 * opener button still exists and still has focus, so the generic restore would
 * keep focus outside the dialog it has just opened. Both are done here, in a
 * `requestAnimationFrame` after the synchronous re-render, which is
 * `calculator.js`'s own precedent for the breakdown tabs.
 *
 * ## Three grids, one dialog
 *
 * **The range is fifty-seven years and, with one grid, the lower half of it was
 * unreachable.** 1970-01-01 to now + 24 hours is crossed by `‹` 684 times or by
 * `Shift`+`PageUp` 57 times, which is not a way anybody enters a date in 1994.
 * So the caption decomposes into two buttons — the month and the year — and
 * each opens a grid of its own: `state.periodPicker.view` is `'days'`,
 * `'months'` or `'years'`, and one press of the year button crosses the range.
 *
 * Four things follow, and each of them is a rule rather than a detail.
 *
 *   * **The dialog always opens on days.** A picker that remembered a year grid
 *     from last time would open on the wrong question.
 *   * **The roving `id="period-grid-focus"` is reused unchanged**, in all three
 *     views: whichever cell is the cursor carries it, so `main.js`'s focus
 *     restore goes on working across the full re-render every keypress causes.
 *     There is never more than one grid on screen, so there is never a second
 *     claimant to the id.
 *   * **`Esc` nests.** From a year or a month grid it returns to the day grid;
 *     only from the day grid does it close the dialog. A single `Esc` that
 *     closed everything from three levels deep loses the visitor's place for
 *     nothing.
 *   * **Only `chooseDay` writes a field, so only `chooseDay` demotes a preset.**
 *     Choosing a year or a month moves the cursor and changes what is drawn; it
 *     states no date, and `time_frame` records which shortcut was pressed rather
 *     than which grid was opened.
 *
 * ## The clock, and why it is two controls in one dialog
 *
 * Beside each *time* box is a clock button, mirroring the calendar button beside
 * each date box. What it opens is a second dialog — `state.periodClock` — built
 * on the same four rules the calendar's is: `role="dialog"`, `aria-modal`, focus
 * moved in on open and **returned to the button that opened it** on close, `Esc`
 * to dismiss and `Tab` held inside.
 *
 * It has **two modes and they are peers.**
 *
 *   * **The dial**: 24 hours on two rings — 1–12 outside, 13–23 and 00 inside,
 *     which is what a 24-hour clock needs and what Material does — then minutes,
 *     snapped to one minute with every fifth minute labelled. Hours first,
 *     minutes following automatically on release, and the readout's two halves
 *     are buttons so either stage can be gone back to. Pointer maths on
 *     `pointerdown`/`pointermove`/`pointerup`, so a mouse, a finger and a pen
 *     are one code path and a drag from the centre is the same gesture as a tap.
 *   * **The keyboard mode**: two number boxes and a toggle, exactly as Android's
 *     has. **This is not a fallback.** The dial is `aria-hidden="true"` because
 *     a dial has no accessible structure to expose, so the number boxes are the
 *     only keyboard and screen-reader route into the control, and the chosen
 *     value is announced from a polite live region as the dial moves.
 *
 * **Which mode opens follows how the button was pressed** (`openClockMode`): a
 * pointer press opens the dial, a keyboard press opens the number boxes.
 * `event.detail === 0` is what tells a keyboard-synthesised click from a real
 * one, and it is one character carrying the whole of the paragraph above.
 *
 * Three things the clock deliberately does **not** do:
 *
 *   * **It does not re-implement the 24-hour ceiling.** `periodProblem` owns
 *     that rule and is the only place it is written down. The clock writes
 *     `HH:MM` into `state.periodFields` and lets the existing check answer,
 *     exactly as `chooseDay` does. A second copy of the bound inside the dial
 *     would be a second rule to keep in step, and it would be the wrong one.
 *   * **It does not fill a date.** `chooseDay` fills a missing *time* with
 *     `00:00` because a date on its own is half a bound and the form would
 *     refuse it; the reverse is not symmetric. A date guessed from a time would
 *     be a value the visitor never stated, on the column the whole feature
 *     exists to record honestly.
 *   * **It does not mirror under `dir="rtl"`**, which is a deliberate
 *     inconsistency with the calendar — see the note over `faceContents`.
 *
 * Writing a time **is** an edit, so confirming one demotes a preset to `custom`
 * through `demotion()`, exactly as choosing a day does.
 *
 * ## The typing path does not call `setState`
 *
 * §7.3a's documented exception, for its documented reason. A `setState` per
 * keystroke would re-render the step, and `change` on a text input fires while
 * focus is leaving it, so a re-render there can pull focus back out of the
 * control the visitor tabbed to. The four text boxes therefore mutate
 * `state.periodFields` directly and patch the error line, the `aria-invalid`
 * flags and the Calculate button by hand — `applyPeriodProblem` is the single
 * function the render path and the typing path both go through, so the two
 * cannot come to different conclusions about the same four strings.
 *
 * ## The boxes punctuate themselves, and tidy up when the caret leaves
 *
 * **Both are appearance and neither is correctness.** `parseDateText` already
 * accepted `1/1/2026` and `parseTimeText` already returned a padded `08:10` for
 * `0810`, so a visitor who typed the untidy form submitted the right instant and
 * was left looking at an untidy box. `tests/web/test_period_submission_browser.
 * py` types the untidy form, blurs, and asserts the request body is byte-for-byte
 * the one the tidy form produces; if that test ever needs relaxing, something
 * here has started changing what is sent and is wrong.
 *
 * *While typing*, `maskPeriodStep` decides and `maskPeriodText` places the `/`
 * and the `:`. Four things would each break it on their own, and each is closed
 * where it is implemented rather than here:
 *
 *   * the mask runs on `insertText` and `insertFromPaste` only, so backspace can
 *     delete a separator instead of watching it reappear (`handlePeriodInput`);
 *   * the caret is restored by **counting digits**, not characters, so editing
 *     the middle of `01/09/2026` does not throw it to the end (`caretAfterDigits`);
 *   * the mask switches itself off for a value the moment a character that is
 *     not a digit is typed into it, and takes back out anything it had already
 *     put in, so `1/1/2026`, `2026-09-14`, `14.09.2026` and `08.10` — all of
 *     which the parsers accept — are never fought (`maskPeriodStep`);
 *   * an IME's composition is left alone between `compositionstart` and
 *     `compositionend`, or the composed text is destroyed as it is being made
 *     — `inputmode="numeric"` makes that unlikely, not impossible
 *     (`handlePeriodComposition`).
 *
 * Whether the separators in a box are the mask's own is kept on the element, in
 * `data-period-masked`. It has to be kept somewhere: a mid-value edit moves them
 * out of position, so the text alone cannot answer it, and a mask that read the
 * text would switch itself off on a visitor's first correction.
 *
 * *On `focusout`*, a value that parses is rewritten to the shape the rest of this
 * module writes — `DATE_DISPLAY`'s `dd/mm/yyyy`, `parseTimeText`'s `HH:MM` — and
 * a value that does **not** parse is left exactly as it was typed, because
 * somebody who wrote a wrong date needs to see what they wrote. `8` → `08:00`
 * and `8:5` → `08:05` are a widening of `parseTimeText` itself and not a second,
 * looser parser used only on blur: the typed path, the blur path and
 * `chooseDay`'s fallback all ask the same function, and two rules for one field
 * is how a box comes to show a value the form then refuses.
 */

import { state, setState } from './state.js'
import { escapeHtml } from './view.js'
import { t } from './i18n.js'

/**
 * How far past the visitor's own clock a reporting period may reach.
 *
 * **24, and `api/schemas.py`'s `PERIOD_CEILING_HOURS` is 38.** See this module's
 * header: the difference is arithmetic (24 + the widest civil UTC offset), not
 * slack, and neither number may be copied onto the other side.
 */
export const MAX_HOURS_AHEAD = 24

/** §6.2's floor. A period before the epoch is a typo, not a reporting period. */
const FLOOR = new Date(1970, 0, 1, 0, 0, 0, 0)

/**
 * Monday.
 *
 * Not a preference and not a guess: it follows from the `en-NZ` pin above, so
 * that the grid's first column and the names written over it are one decision
 * rather than two. `Intl.Locale('en-NZ').weekInfo` would say the same thing
 * where it is implemented, and says nothing at all in Firefox — a runtime lookup
 * that silently answers "Sunday" in one browser is worse than a constant with
 * the reason written next to it.
 */
const FIRST_DAY_OF_WEEK = 1

/**
 * Every date this feature prints, and the locale is pinned in all four.
 *
 * Built once at module level rather than per cell: a six-week grid formats
 * 42 accessible names per render, and `Intl.DateTimeFormat` construction is the
 * expensive half of the call.
 */
const LOCALE = 'en-NZ'
const DATE_DISPLAY = new Intl.DateTimeFormat(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric' })
const DAY_NAME = new Intl.DateTimeFormat(LOCALE, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
const MONTH_CAPTION = new Intl.DateTimeFormat(LOCALE, { month: 'long', year: 'numeric' })
/** The caption's two halves, for the two buttons it decomposed into. The year is
 *  not formatted at all: `2026` is the same four characters in every catalogue,
 *  and running it through `Intl` would be a place for a grouping separator to
 *  appear in a year. */
const MONTH_NAME = new Intl.DateTimeFormat(LOCALE, { month: 'long' })
const WEEKDAY_LONG = new Intl.DateTimeFormat(LOCALE, { weekday: 'long' })
const WEEKDAY_SHORT = new Intl.DateTimeFormat(LOCALE, { weekday: 'short' })

/** The two bounds, in the order they are read and rendered. */
const BOUNDS = ['start', 'end']

const pad = value => String(value).padStart(2, '0')

/** A local `Date` as `YYYY-MM-DD`. Never `toISOString()`, which converts to UTC
 *  and so answers the previous day for every evening in New Zealand. */
const isoDay = date => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`

const dayOf = iso => {
  const [year, month, day] = iso.split('-').map(Number)
  return new Date(year, month - 1, day)
}

/**
 * Days added, **with the clock time carried along.**
 *
 * The hours and minutes are not decoration here: *One week* is "the seven days
 * ending now", and an `addDays` that rebuilt the date from `(y, m, d)` alone
 * silently anchored every preset's start at midnight — so pressing it at 18:41
 * filled 14/09 **00:00** to 21/09 18:41, which is seven days and nineteen hours
 * and is not what the button says. Measured in Chromium before it was fixed.
 */
const addDays = (date, count) => new Date(date.getFullYear(), date.getMonth(), date.getDate() + count, date.getHours(), date.getMinutes())

/**
 * Months added with the day clamped rather than overflowed.
 *
 * JavaScript's own `setMonth` rolls over: 31 March minus one month is 3 March,
 * so *One month* pressed on the 31st would fill an interval of 28 days and call
 * it a month, and `PageUp` from 31 March would land the cursor in the wrong
 * month entirely. Clamping to the last day of the target month is what a person
 * means by "the same date, a month earlier".
 */
function addMonths(date, count) {
  const year = date.getFullYear()
  const month = date.getMonth() + count
  const lastDay = new Date(year, month + 1, 0).getDate()
  return new Date(year, month, Math.min(date.getDate(), lastDay), date.getHours(), date.getMinutes())
}

/**
 * The visitor's own now, to the minute.
 *
 * Seconds and milliseconds are dropped because the columns keep none and
 * because a ceiling that moved within the minute would make the same typed
 * value legal and then illegal while nothing on screen changed.
 */
function now() {
  const value = new Date()
  value.setSeconds(0, 0)
  return value
}

/** The latest instant this form accepts: the visitor's now plus `MAX_HOURS_AHEAD`. */
const ceiling = () => new Date(now().getTime() + MAX_HOURS_AHEAD * 3600 * 1000)

// ---------------------------------------------------------------------------
// Parsing what was typed
// ---------------------------------------------------------------------------

/**
 * `dd/mm/yyyy`, and `yyyy-mm-dd` as well because it costs one branch.
 *
 * The two are told apart by which end carries four digits, so there is no
 * ambiguity to guess at: `03/04/2026` is the 3rd of April, in `en-NZ` order,
 * and `2026-04-03` is the same day written the other way round. A two-digit
 * year is refused outright rather than windowed into a century — `14/09/26`
 * could be 1926 or 2026 and this form has a floor at 1970 for exactly the
 * reason that guessing a century is not a thing it should do.
 */
const DATE_PATTERN = /^(\d{1,4})[/\-. ](\d{1,2})[/\-. ](\d{1,4})$/

export function parseDateText(text) {
  const match = DATE_PATTERN.exec(String(text ?? '').trim())
  if (!match) return null
  const [, first, middle, last] = match
  let year, month, day
  if (first.length === 4) {
    year = Number(first); month = Number(middle); day = Number(last)
  } else if (last.length === 4) {
    day = Number(first); month = Number(middle); year = Number(last)
  } else {
    return null
  }
  const date = new Date(year, month - 1, day)
  // The round trip is what refuses 31/02/2026: `new Date(2026, 1, 31)` is
  // happily the 3rd of March, and a form that accepted it would store a date
  // the visitor did not type and print it back to them.
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) return null
  return isoDay(date)
}

/**
 * `08:10`, `8:10`, `08.10`, `0810`, `8:5` and a bare `8`.
 *
 * The bare four digits are the fast path the plan asks for — somebody entering
 * a shift types the number they read off a roster. 24-hour only: an `am`/`pm`
 * suffix would be a second format to parse, to print and to translate, on a
 * field whose whole point is that it is quicker than opening anything.
 *
 * **The last two shapes are the second pass's widening, and they are widened
 * rather than on blur.** `8` means eight o'clock and `8:5` means five past
 * eight to everybody who types them, and the box now tidies both into `08:00`
 * and `08:05` when the caret leaves it. A second, looser parser used only by
 * the blur path would be two rules for one field: the typed path, the blur path
 * and `chooseDay`'s "is there already a time here?" fallback all ask this
 * function, and a shape one of them accepted and another did not is how a box
 * comes to show a value the form then refuses.
 *
 * A bare **three** digits is still refused. `810` is five past eight to one
 * reader and ten past eight to another, and this form has a floor at 1970 for
 * exactly the reason that guessing is not a thing it does.
 *
 * Nothing here narrows: every string the earlier patterns accepted still
 * parses to the same `HH:MM`.
 */
const TIME_PATTERN = /^(\d{1,2})[:.](\d{1,2})$/
const COMPACT_TIME_PATTERN = /^(\d{2})(\d{2})$/
const BARE_HOUR_PATTERN = /^(\d{1,2})$/

export function parseTimeText(text) {
  const trimmed = String(text ?? '').trim()
  const match = TIME_PATTERN.exec(trimmed) || COMPACT_TIME_PATTERN.exec(trimmed) || BARE_HOUR_PATTERN.exec(trimmed)
  if (!match) return null
  const hours = Number(match[1])
  // `undefined` for the bare hour, and midnight is the only minute a bare hour
  // can mean. `Number(undefined)` is `NaN`, which would pass `> 59` and print
  // `NaN` into the box, so the default is written rather than left to coercion.
  const minutes = Number(match[2] ?? 0)
  if (hours > 23 || minutes > 59) return null
  return `${pad(hours)}:${pad(minutes)}`
}

/** The wire shape of one bound, `YYYY-MM-DDTHH:MM`, or `''`. §6.2 appends the
 *  seconds; nothing in this module ever computes with the value. */
const instantOf = (isoDate, time) => (isoDate && time ? `${isoDate}T${time}` : '')

const dateOfInstant = instant => {
  const [datePart, timePart] = instant.split('T')
  const [year, month, day] = datePart.split('-').map(Number)
  const [hours, minutes] = timePart.split(':').map(Number)
  return new Date(year, month - 1, day, hours, minutes)
}

/**
 * One stored instant, as a person reads it: `14/09/2026 08:10` (v1.68).
 *
 * **Exported from here rather than written again where it is read.**
 * `results.js` prints the period on the results page and into the text
 * download, and `api/pdf_render.py` prints the same sentence onto the PDF; a
 * second `Intl.DateTimeFormat` beside either of them would be a second place
 * the `en-NZ` pin has to be changed when O-4 is decided, and — worse — a second
 * chance for the period to be *printed* in a shape the field that collected it
 * would not *accept*. `DATE_DISPLAY` is the formatter the four text boxes are
 * filled from, so what is read back is character-for-character what was typed.
 *
 * The time is taken verbatim off the wire shape rather than run through a
 * formatter: it is already `HH:MM` on a 24-hour clock, which is what
 * `parseTimeText` produced and what the box shows. `slice(0, 5)` because §6.2
 * appends `:00` seconds and a reporting period has no seconds to print.
 *
 * @param {string} instant `YYYY-MM-DDTHH:MM`, or `''`.
 * @returns {string} `dd/mm/yyyy hh:mm`, or `''` for an absent instant.
 */
export function formatInstant(instant) {
  if (!instant) return ''
  return `${DATE_DISPLAY.format(dateOfInstant(instant))} ${String(instant).split('T')[1].slice(0, 5)}`
}

// ---------------------------------------------------------------------------
// The separators the four boxes insert while they are being typed
// ---------------------------------------------------------------------------

/**
 * Where a separator belongs, **counted in digits rather than in characters**.
 *
 * `dd/mm/yyyy` breaks after the 2nd and the 4th digit; `HH:MM` breaks after the
 * 2nd. Every function below is written in digit counts for the same reason the
 * caret is restored by one: a character offset into `01/09/2026` means a
 * different thing before and after the mask has run, and a digit offset means
 * the same thing in both.
 */
const MASKS = {
  date: { separator: '/', breaks: [2, 4] },
  time: { separator: ':', breaks: [2] },
}

const maskFor = field => (field.endsWith('Date') ? MASKS.date : MASKS.time)

const isDigit = character => character >= '0' && character <= '9'

/**
 * Digits, separated.
 *
 * **Nothing is truncated.** A ninth digit in a date spills into the year group
 * rather than being swallowed, so `140920261` reads back as `14/09/20261` — a
 * value the parser refuses and the visitor can see is wrong. A mask that ate
 * the keystroke would leave a box showing `14/09/2026` that the visitor is sure
 * they typed a 1 into, which is the worse of the two failures by a distance.
 *
 * The separator is inserted **when the next group opens**, never eagerly after
 * the last digit of a group: `14` stays `14` and becomes `14/0` on the third
 * digit. An eager trailing `/` would put the caret on the wrong side of a
 * character that is not there yet.
 */
function maskDigits(digits, mask) {
  const groups = []
  let cut = 0
  for (const breakAt of mask.breaks) {
    if (digits.length <= breakAt) break
    groups.push(digits.slice(cut, breakAt))
    cut = breakAt
  }
  groups.push(digits.slice(cut))
  return groups.join(mask.separator)
}

/**
 * The separators this mask put in, taken back out.
 *
 * Needed because of what the third trap looks like when it is typed rather than
 * pasted. A visitor entering `2026-09-14` by hand types four digits *before*
 * they type the `-`, and four bare digits are indistinguishable from `dd/mm`:
 * the mask has already written `20/26` by the time the character that says
 * "this is year-first" arrives. Leaving it there reads back `20/26-09-14`,
 * which parses to nothing and which no visitor would recognise as a refusal.
 *
 * **Measured, in Chromium, typed one key at a time** — `page.fill` never shows
 * this, because a fill is one event carrying the whole string and the `-` is in
 * it from the start.
 *
 * Only a separator of this mask's own, at one of this mask's own break points,
 * is removed. Anything else the visitor put there stays: `14/09-2026` mixes
 * separators, `parseDateText` accepts it, and stripping the `/` out of it would
 * delete a character somebody typed on purpose.
 *
 * **`keepFrom`/`keepTo` fence off the characters this very keystroke added**,
 * and they are not a refinement. `2026/09/14` also parses, and the `/` the
 * visitor types after `2026` lands at the fourth digit — which is one of this
 * mask's own break points, so without the fence it would be read as the mask's
 * and deleted on the keystroke that typed it. Measured: the box read
 * `202609/14` at the end.
 *
 * @param {string} value the text to strip.
 * @param {object} mask the separator and break points for this box.
 * @param {number} [keepFrom] first index of the characters this event inserted.
 * @param {number} [keepTo] one past the last of them.
 */
function unmaskOwn(value, mask, keepFrom = -1, keepTo = -1) {
  let out = ''
  let digits = 0
  for (let index = 0; index < value.length; index += 1) {
    const character = value[index]
    if (isDigit(character)) { digits += 1; out += character; continue }
    const justTyped = index >= keepFrom && index < keepTo
    if (!justTyped && character === mask.separator && mask.breaks.includes(digits)) continue
    out += character
  }
  return out
}

/** How many characters two strings share from the start. Used to find where in
 *  the new value this event's own insertion landed. */
function sharedPrefix(before, after) {
  let index = 0
  while (index < before.length && index < after.length && before[index] === after[index]) index += 1
  return index
}

/**
 * One box's digits, separated.
 *
 * @param {string} value the box's text.
 * @param {string} field one of `startDate`, `startTime`, `endDate`, `endTime`.
 * @returns {string} the same digits, with this field's separators in place.
 */
export function maskPeriodText(value, field) {
  const mask = maskFor(field)
  return maskDigits([...String(value ?? '')].filter(isDigit).join(''), mask)
}

/** Whether a string is digits and nothing else. `''` counts. */
const DIGITS_ONLY = /^\d*$/

/**
 * **One keystroke's worth of punctuation, as a pure function** — the whole of
 * the mask's decision, with no DOM in it, so that
 * `tests/web/test_period_rules.py` can drive it over sequences a page load per
 * case could never afford.
 *
 * Three questions, in this order.
 *
 *  1. **Was a non-digit typed?** Then the mask is off for this value, and
 *     anything it had already put in comes back out (`unmaskOwn`). This is the
 *     third trap: the field accepts `-`, `.`, a space and year-first order, and
 *     a mask that reformatted those would refuse input shapes this field takes
 *     today — a regression dressed as a feature.
 *  2. **Were the separators already in the box the mask's own?** `masked` says
 *     so, and a box of bare digits is trivially the mask's to punctuate. If
 *     neither, hands off: `1/1/2026` is the visitor's and stays theirs.
 *  3. Otherwise re-derive the whole value from its digits.
 *
 * **`masked`, and not the shape of the value, is what carries question two
 * across a mid-value edit.** A visitor editing the middle of `14/09/2026`
 * leaves the separators one place out of position — `154/09/2026` — so the
 * value *after* the edit looks like somebody else's punctuation, and a rule
 * read off the text alone would switch the mask off on the first correction and
 * never turn it back on. The flag is a fact about the box, not about the
 * characters currently in it, which is why it survives that.
 *
 * **`before` is used by the first branch only, and that is measured rather than
 * assumed.** The obvious reading is that question two should ask about the text
 * as it stood before the keystroke; it makes no difference, because a digit
 * insertion cannot add or remove a non-digit and so `before` and `typed` are
 * digits-only together or not at all. Both variants were run over 92,912
 * combinations of value, insertion point, insertion, `masked` and field: zero
 * divergences. `typed` is used because it is also right for the one edit that
 * is *not* an insertion — a paste over a selected value, where `before` is the
 * text that has just been replaced and answers about a box that no longer
 * exists.
 *
 * @param {object} step
 * @param {string} step.before the box's text before this insertion. Used to
 *   locate this event's own characters inside `typed`, so the first branch can
 *   fence them off; see `unmaskOwn`.
 * @param {string} step.typed the box's text as the browser has left it.
 * @param {string|null} step.inserted the characters this event added, or `null`
 *   when the browser did not say — a paste whose content is only on
 *   `dataTransfer`, which is treated as "not digits" rather than guessed at.
 * @param {boolean} step.masked whether the separators in `before` are the
 *   mask's own.
 * @param {string} field one of `startDate`, `startTime`, `endDate`, `endTime`.
 * @returns {{ value: string, masked: boolean }}
 */
export function maskPeriodStep(step, field) {
  const mask = maskFor(field)
  const typed = String(step.typed ?? '')
  if (typeof step.inserted !== 'string' || step.inserted === '' || !DIGITS_ONLY.test(step.inserted)) {
    if (!step.masked) return { value: typed, masked: false }
    const at = sharedPrefix(String(step.before ?? ''), typed)
    const length = typeof step.inserted === 'string' ? step.inserted.length : 0
    return { value: unmaskOwn(typed, mask, at, at + length), masked: false }
  }
  if (!step.masked && !DIGITS_ONLY.test(typed)) return { value: typed, masked: false }
  const value = maskPeriodText(typed, field)
  return { value, masked: value.includes(mask.separator) }
}

/** How many digits sit before the caret. The first half of the second trap. */
const digitsBefore = (value, caret) => [...String(value).slice(0, caret)].filter(isDigit).length

/**
 * Where the caret goes once the mask has run: **after the same number of
 * digits**, not at the same character offset.
 *
 * The second trap. Editing the middle of `01/09/2026` and restoring a character
 * offset throws the caret to the end of the box, because the mask may have added
 * a character in front of it. Counting digits is stable across the rewrite.
 *
 * A separator immediately after that digit is stepped over, so the caret lands
 * where the next digit will go rather than in front of a `/` the visitor would
 * then have to arrow past.
 */
function caretAfterDigits(value, count) {
  if (count <= 0) return 0
  let seen = 0
  for (let index = 0; index < value.length; index += 1) {
    if (!isDigit(value[index])) continue
    seen += 1
    if (seen < count) continue
    let after = index + 1
    while (after < value.length && !isDigit(value[after])) after += 1
    return after
  }
  return value.length
}

// ---------------------------------------------------------------------------
// The rules the form holds
// ---------------------------------------------------------------------------

const EMPTY_FIELDS = { startDate: '', startTime: '', endDate: '', endTime: '' }

export const periodFields = () => ({ ...EMPTY_FIELDS, ...(state.periodFields || {}) })

/** One bound, read out of what is typed. `issue` is `''` when the bound is
 *  either complete and legal or completely empty. */
function readBound(fields, bound) {
  const dateText = String(fields[`${bound}Date`] ?? '').trim()
  const timeText = String(fields[`${bound}Time`] ?? '').trim()
  if (!dateText && !timeText) return { empty: true, issue: '', instant: '' }
  const isoDate = parseDateText(dateText)
  const time = parseTimeText(timeText)
  if (!dateText || !timeText) {
    return { empty: false, issue: 'incomplete', field: `period-${bound}-${dateText ? 'time' : 'date'}`, instant: '' }
  }
  if (isoDate === null) return { empty: false, issue: 'date', field: `period-${bound}-date`, instant: '' }
  if (time === null) return { empty: false, issue: 'time', field: `period-${bound}-time`, instant: '' }
  const instant = instantOf(isoDate, time)
  const value = dateOfInstant(instant)
  if (value < FLOOR) return { empty: false, issue: 'floor', field: `period-${bound}-date`, instant: '' }
  if (value > ceiling()) return { empty: false, issue: 'ahead', field: `period-${bound}-date`, instant: '' }
  return { empty: false, issue: '', field: '', instant }
}

/**
 * Everything wrong with the period right now, or `{ message: '' }`.
 *
 * **One function, two callers**, for the reason §7.3a gives about `updateLine`
 * and `destinationRows`: the render path and the keystroke path apply the same
 * rules to the same four strings, and a second copy of them is how the two come
 * to disagree about whether one screen is valid.
 *
 * The order is the order a reader fixes them in — each bound on its own first,
 * then the three rules that need both. The ceiling and the floor are checked per
 * bound because that is where the message can point at a field.
 *
 * **`timeFrame` is a parameter and not a read of `state` (v1.68).** It has to
 * be, because `handlePeriodInput` demotes a preset to `custom` in the same
 * breath as it asks this question, and a default read here would answer about
 * the answer the visitor has just stopped giving. `state.timeFrame` is the
 * default for the two callers — the render path and `calculator.js`'s button —
 * that ask after the state has settled.
 */
export function periodProblem(fields = periodFields(), timeFrame = state.timeFrame) {
  const bounds = { start: readBound(fields, 'start'), end: readBound(fields, 'end') }
  for (const bound of BOUNDS) {
    const read = bounds[bound]
    if (!read.issue) continue
    const message = {
      incomplete: t('Enter both a date and a time for this period.'),
      date: t('Enter the date as dd/mm/yyyy.'),
      time: t('Enter the time as hh:mm on the 24-hour clock.'),
      floor: t('A reporting period cannot begin before 1 January 1970.'),
      ahead: t('A reporting period cannot reach more than 24 hours into the future.'),
    }[read.issue]
    return { message, field: read.field }
  }
  if (bounds.start.empty !== bounds.end.empty) {
    const missing = bounds.start.empty ? 'start' : 'end'
    return { message: t('Enter both a start and an end, or leave the period unstated.'), field: `period-${missing}-date` }
  }
  // §6.2's `period_custom_without_interval`, held here for the reason every
  // other clause in this function is held here: **the form must not be able to
  // build a request the API refuses.** Without it, choosing *Custom period* and
  // typing nothing left Calculate live over a payload of `time_frame: "custom"`
  // with two nulls, which is a 422 the visitor meets after the request rather
  // than a sentence they meet before it. It is reachable the other way round
  // too, and less obviously: pressing *One week* and then emptying all four
  // boxes demotes the answer to `custom` and leaves no interval behind it.
  //
  // Refused rather than normalised to "not stated", which is the same ruling
  // §6.2 takes and for the same reason — dropping the answer on the visitor's
  // behalf discards a selection they made and records a fact they did not
  // state. The message is the one the half-interval case above already uses,
  // word for word: *enter both, or say you are not stating a period* is exactly
  // what is being asked, and a second sentence saying it differently would be a
  // twenty-first catalogue entry that adds nothing.
  if (timeFrame === 'custom' && bounds.start.empty) {
    return { message: t('Enter both a start and an end, or leave the period unstated.'), field: 'period-start-date' }
  }
  if (!bounds.start.empty && dateOfInstant(bounds.end.instant) < dateOfInstant(bounds.start.instant)) {
    // Equal ends are allowed, here and in §6.2: a zero-length period is odd, it
    // enters no calculation, and refusing it buys what refusing a ten-year span
    // would buy, which is nothing. A ten-year span is allowed for the same
    // reason and is not checked anywhere in this file.
    return { message: t('The period cannot end before it starts.'), field: 'period-end-date' }
  }
  return { message: '', field: '' }
}

/** The two values `state.periodStart` / `state.periodEnd` should hold for what
 *  is typed: the wire shape when the whole period is legal, `''` otherwise. A
 *  half-typed date must not leave a stale instant behind it. */
export function periodValues(fields = periodFields()) {
  if (periodProblem(fields).message) return { periodStart: '', periodEnd: '' }
  return {
    periodStart: readBound(fields, 'start').instant,
    periodEnd: readBound(fields, 'end').instant,
  }
}

// ---------------------------------------------------------------------------
// The presets, which are now templates
// ---------------------------------------------------------------------------

/**
 * What pressing one of the four shortcuts fills the interval with: the period
 * **ending now**, back to its own start.
 *
 * Anchored backwards rather than forwards because that is what a visitor
 * pressing *One week* means — the week they are reporting on is the one that
 * just finished — and because the 24-hour ceiling above makes a forward anchor
 * meaningless: a year starting now ends eleven months past the bound.
 */
const PRESET_SPANS = {
  one_week: date => addDays(date, -7),
  one_month: date => addMonths(date, -1),
  one_quarter: date => addMonths(date, -3),
  one_year: date => addMonths(date, -12),
}

export function presetFields(timeFrame) {
  const span = PRESET_SPANS[timeFrame]
  if (!span) return { ...EMPTY_FIELDS }
  const end = now()
  const start = span(end)
  return {
    startDate: DATE_DISPLAY.format(start),
    startTime: `${pad(start.getHours())}:${pad(start.getMinutes())}`,
    endDate: DATE_DISPLAY.format(end),
    endTime: `${pad(end.getHours())}:${pad(end.getMinutes())}`,
  }
}

/**
 * The patch `#time-frame` changing produces.
 *
 * *Not stated* clears the interval, because the two columns mean "no period was
 * given" by absence (§2.3) and a hidden field that still held dates would send
 * one. A preset **overwrites** whatever is there: pressing *One month* is a
 * request to be given that month, and a preset that quietly declined to
 * overwrite would be a button that sometimes did nothing. `custom` keeps what is
 * typed and opens nothing — the visitor may already have filled the fields
 * before naming the answer.
 */
export function timeFrameChanged(value) {
  if (!value) return { timeFrame: value, periodFields: { ...EMPTY_FIELDS }, periodStart: '', periodEnd: '', periodPicker: null, periodClock: null }
  if (value === 'custom') {
    const fields = periodFields()
    return { timeFrame: value, periodFields: fields, ...periodValues(fields), periodPicker: null, periodClock: null }
  }
  const fields = presetFields(value)
  return { timeFrame: value, periodFields: fields, ...periodValues(fields), periodPicker: null, periodClock: null }
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

/**
 * Whether the two date fields are on screen at all.
 *
 * **Any stated answer reveals them, not only `custom`.** From v1.67 a preset is
 * a button that fills the interval, and an interval filled behind a hidden field
 * is a value the visitor is about to send without having seen it. "Not stated"
 * shows nothing new, which is the promise that a visitor who never opens the
 * picker sees what they saw before.
 */
export const periodOffered = () => Boolean(state.timeFrame)

/** An ISO day held inside the allowed range. String comparison is exact for
 *  `YYYY-MM-DD` and is what keeps this free of a second date object. */
const clampDay = iso => {
  const floorDay = isoDay(FLOOR)
  const ceilingDay = isoDay(ceiling())
  if (iso < floorDay) return floorDay
  if (iso > ceilingDay) return ceilingDay
  return iso
}

/** Whether a whole day is offerable. The ceiling is an instant, so the last
 *  allowed day is partly out of range; the day stays enabled and the field-level
 *  check below refuses a time on it that is past the bound. Saying so here
 *  because a grid that disabled the whole day would refuse this morning. */
const dayEnabled = iso => iso >= isoDay(FLOOR) && iso <= isoDay(ceiling())

/**
 * The two grids behind the caption, and the arithmetic they are laid out on.
 *
 * **Five years to a row and three months to a row**, both chosen so that a row
 * is a thing a reader already has a name for: 1970 is a multiple of five, so
 * every row of the year grid is a half-decade — 1970–1974, 1975–1979 — and every
 * row of the month grid is a quarter. A column count that did not divide the
 * range at a place a reader recognises makes `Home` and `End` land somewhere
 * arbitrary, and `Home`/`End` are defined as the ends of the row.
 *
 * The last row of the year grid runs past the ceiling — 2025–2029 for a ceiling
 * in 2026 — and those cells are **drawn and disabled** rather than left out, so
 * that the grid's own shape says where the range stops. Years before 1970 are
 * not drawn at all, which is stronger than disabled: the grid begins at the
 * floor because the floor is a multiple of the row length.
 */
const YEARS_PER_ROW = 5
const MONTHS_PER_ROW = 3
const FLOOR_YEAR = FLOOR.getFullYear()

/** Whole years added, with the day clamped: `addMonths` already does that, and
 *  29 February is the reason it has to be done at all. */
const addYears = (date, count) => addMonths(date, count * 12)

/** A year is offerable when any day of it is. Same rule as `dayEnabled` and for
 *  the same reason: the ceiling is an instant inside a day, so the year holding
 *  it is partly out of range and stays enabled. */
const yearEnabled = year => year >= FLOOR_YEAR && year <= ceiling().getFullYear()

/** A month is offerable when any day of it is — which is what makes January 1970
 *  reachable and December 1969 not, and what disables the months after the
 *  ceiling's own month in the ceiling's own year. `new Date(y, m + 1, 0)` is the
 *  last day of month `m`. */
const monthEnabled = (year, month) =>
  isoDay(new Date(year, month + 1, 0)) >= isoDay(FLOOR) && isoDay(new Date(year, month, 1)) <= isoDay(ceiling())

/** The day a freshly-opened calendar should land on: what the field already
 *  holds, else today, clamped into range either way. */
function openingCursor(bound) {
  const typed = parseDateText(periodFields()[`${bound}Date`])
  return clampDay(typed || isoDay(now()))
}

/** The seven weekday headings, starting on `FIRST_DAY_OF_WEEK`. 2024-01-01 was
 *  a Monday, which is what makes the offsets below readable. */
const MONDAY = new Date(2024, 0, 1)
const weekdayHeadings = () => Array.from({ length: 7 }, (unused, index) => {
  const sample = addDays(MONDAY, (FIRST_DAY_OF_WEEK - 1 + index + 7) % 7)
  return { short: WEEKDAY_SHORT.format(sample), long: WEEKDAY_LONG.format(sample) }
})

/** The first cell of the grid: the `FIRST_DAY_OF_WEEK` on or before the 1st. */
function gridStart(cursorIso) {
  const cursor = dayOf(cursorIso)
  const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1)
  const shift = (first.getDay() - FIRST_DAY_OF_WEEK + 7) % 7
  return addDays(first, -shift)
}

/**
 * One month, as a `role="grid"` a screen reader can read and a keyboard can
 * drive.
 *
 * `<td>` carries the cell and the `tabindex`, rather than a `<button>` inside
 * it, for one reason that decides the whole component: **a disabled `<button>`
 * cannot take focus**, so a roving `tabindex` that landed on one would leave the
 * grid with no tab stop at all. A `<td role="gridcell" aria-disabled="true">`
 * with no `tabindex` is genuinely unreachable — not clickable, not focusable,
 * not selectable — and the arrow keys clamp rather than step onto it, which is
 * exactly right here because the disabled region is the tail past the ceiling
 * and the head before 1970, never a hole in the middle.
 *
 * Each cell's accessible name is the whole date — "Monday, 21 September 2026" —
 * and not the number in it, because "21" read out of a grid of numbers says
 * nothing about which 21st.
 */
function monthGrid(picker) {
  // `aria-describedby` sits on the grid rather than on the dialog because focus
  // moves straight to a cell when the dialog opens: a description hung on the
  // dialog element is never announced, since the dialog itself never receives
  // focus.
  const cursor = picker.cursor
  const cursorDate = dayOf(cursor)
  const selectedIso = parseDateText(periodFields()[`${picker.field}Date`])
  const headings = weekdayHeadings()
  const head = `<tr role="row">${headings.map(heading =>
    `<th role="columnheader" scope="col"><span aria-hidden="true">${escapeHtml(heading.short)}</span><span class="sr-only">${escapeHtml(heading.long)}</span></th>`).join('')}</tr>`
  const start = gridStart(cursor)
  const rows = []
  for (let week = 0; week < 6; week += 1) {
    const cells = []
    for (let index = 0; index < 7; index += 1) {
      const date = addDays(start, week * 7 + index)
      const iso = isoDay(date)
      if (date.getMonth() !== cursorDate.getMonth()) {
        // A blank cell rather than the neighbouring month's number: a grid that
        // showed both would need two rules for what clicking one does, and the
        // arrow keys already cross the boundary by moving the month with them.
        cells.push('<td role="gridcell" class="period-day is-outside"></td>')
        continue
      }
      const name = DAY_NAME.format(date)
      if (!dayEnabled(iso)) {
        cells.push(`<td role="gridcell" class="period-day is-disabled" aria-disabled="true" aria-label="${escapeHtml(name)}">${date.getDate()}</td>`)
        continue
      }
      const isCursor = iso === cursor
      const isSelected = iso === selectedIso
      cells.push(`<td role="gridcell" class="period-day${isSelected ? ' is-selected' : ''}" data-action="period-day" data-day="${iso}"${isCursor ? ' id="period-grid-focus"' : ''} tabindex="${isCursor ? '0' : '-1'}" aria-selected="${isSelected ? 'true' : 'false'}" aria-label="${escapeHtml(name)}">${date.getDate()}</td>`)
    }
    // Six rows is the worst case and most months need five. The empty one is
    // dropped rather than drawn, so the dialog does not change height between
    // months for no reason a visitor can see.
    if (cells.some(cell => !cell.includes('is-outside'))) rows.push(`<tr role="row">${cells.join('')}</tr>`)
  }
  // `aria-label` rather than `aria-labelledby="period-dialog-month"` from the
  // second pass: the caption is two buttons now, and an `aria-labelledby` is
  // resolved from the referenced element's *accessible* text — so the grid would
  // have been named "September, Choose a month 2026, Choose a year". The string
  // is the same one the caption shows, built from the same formatter.
  return `<table class="period-grid" role="grid" aria-label="${escapeHtml(MONTH_CAPTION.format(cursorDate))}" aria-describedby="period-dialog-hint"><thead>${head}</thead><tbody>${rows.join('')}</tbody></table>`
}

/**
 * One cell of the year or the month grid.
 *
 * **A disabled cell is genuinely unreachable**, exactly as the day grid's are
 * and for the reason written over `monthGrid`: no `data-action`, so nothing can
 * click it; no `tabindex`, so nothing can focus it; `aria-disabled="true"`, so
 * nothing reads it as offerable. The colour is the last of the four signals and
 * never the only one.
 */
function choiceCell({ label, text, enabled, cursor, selected, action, value }) {
  if (!enabled) {
    return `<td role="gridcell" class="period-choice is-disabled" aria-disabled="true" aria-label="${escapeHtml(label)}">${escapeHtml(text)}</td>`
  }
  return `<td role="gridcell" class="period-choice${selected ? ' is-selected' : ''}" data-action="${action}" data-value="${escapeHtml(value)}"${cursor ? ' id="period-grid-focus"' : ''} tabindex="${cursor ? '0' : '-1'}" aria-selected="${selected ? 'true' : 'false'}" aria-label="${escapeHtml(label)}">${escapeHtml(text)}</td>`
}

/** The year already in the box, or `null`. What `aria-selected` marks, as
 *  against the cursor, which is what the roving `tabindex` marks. */
function selectedDate(picker) {
  const iso = parseDateText(periodFields()[`${picker.field}Date`])
  return iso ? dayOf(iso) : null
}

/**
 * Fifty-seven years, in a box that scrolls.
 *
 * The scroller is a `<div>` around the table rather than a `max-block-size` on
 * the table itself, because a `<table>` is not a scroll container: `overflow`
 * on one is ignored in every engine. It is scrolled to the cursor by
 * `focusAfterRender`, which is also what focuses it — see the note there about
 * why the scroll is not left to `.focus()`.
 */
function yearGrid(picker) {
  const cursorYear = dayOf(picker.cursor).getFullYear()
  const selected = selectedDate(picker)
  const last = ceiling().getFullYear()
  const rows = []
  for (let start = FLOOR_YEAR; start <= last; start += YEARS_PER_ROW) {
    const cells = []
    for (let index = 0; index < YEARS_PER_ROW; index += 1) {
      const year = start + index
      cells.push(choiceCell({
        label: String(year),
        text: String(year),
        enabled: yearEnabled(year),
        cursor: year === cursorYear,
        selected: Boolean(selected) && selected.getFullYear() === year,
        action: 'period-choose-year',
        value: String(year),
      }))
    }
    rows.push(`<tr role="row">${cells.join('')}</tr>`)
  }
  return `<div class="period-scroller"><table class="period-grid period-choices" role="grid" aria-label="${escapeHtml(t('Choose a year'))}" aria-describedby="period-dialog-hint"><tbody>${rows.join('')}</tbody></table></div>`
}

/**
 * Twelve months of the cursor's year, three to a row.
 *
 * Each cell's accessible name is the month **and the year** — "September 2026" —
 * for the reason a day cell's name is the whole date: "September" read out of a
 * grid in a dialog says nothing about which September.
 */
function monthChoiceGrid(picker) {
  const cursor = dayOf(picker.cursor)
  const year = cursor.getFullYear()
  const selected = selectedDate(picker)
  const rows = []
  for (let row = 0; row < 12 / MONTHS_PER_ROW; row += 1) {
    const cells = []
    for (let index = 0; index < MONTHS_PER_ROW; index += 1) {
      const month = row * MONTHS_PER_ROW + index
      const first = new Date(year, month, 1)
      cells.push(choiceCell({
        label: MONTH_CAPTION.format(first),
        text: MONTH_NAME.format(first),
        enabled: monthEnabled(year, month),
        cursor: month === cursor.getMonth(),
        selected: Boolean(selected) && selected.getFullYear() === year && selected.getMonth() === month,
        action: 'period-choose-month',
        value: String(month),
      }))
    }
    rows.push(`<tr role="row">${cells.join('')}</tr>`)
  }
  return `<table class="period-grid period-choices" role="grid" aria-label="${escapeHtml(t('Choose a month'))}" aria-describedby="period-dialog-hint"><tbody>${rows.join('')}</tbody></table>`
}

/**
 * The dialog itself, and it is a real one: `role="dialog"`, `aria-modal="true"`,
 * a labelled title, focus moved in on open, focus returned to the opener on
 * close, `Esc` to dismiss and `Tab` held inside. `improvement.js`'s expanded
 * chart is this repository's precedent for the markup; the behaviours are in
 * `handlePeriodKeydown` below, because markup alone is not any of them.
 */
function pickerDialog() {
  const picker = state.periodPicker
  if (!picker) return ''
  const view = pickerView(picker)
  const cursor = dayOf(picker.cursor)
  const title = picker.field === 'start' ? t('Choose the start date') : t('Choose the end date')
  // A month is reachable when any day of it is: the last day of the month
  // before, and the first day of the month after. Asking about the cursor's own
  // date one month away would disable January 1970 for a cursor on the 31st and
  // leave it enabled for a cursor on the 1st, which is a button that works or
  // not depending on where the visitor's arrow keys happen to be.
  const previous = dayEnabled(isoDay(new Date(cursor.getFullYear(), cursor.getMonth(), 0)))
  const next = dayEnabled(isoDay(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1)))
  // **A hint per view, because the day grid's names keys the others do not
  // have.** "Page Up and Page Down to move by month" over a grid of years
  // describes two keys that do nothing there, and a hint that is wrong is worse
  // than no hint at all.
  const hint = {
    days: t('Use the arrow keys to move by day, Page Up and Page Down to move by month, then press Enter to choose.'),
    months: t('Use the arrow keys to move between months, then press Enter to choose.'),
    years: t('Use the arrow keys to move between years, then press Enter to choose.'),
  }[view]
  const grid = { days: monthGrid, months: monthChoiceGrid, years: yearGrid }[view](picker)
  // **The `‹ ›` steppers belong to the day grid and are drawn only with it.**
  // They move by one month, which is the thing the other two views exist to stop
  // a visitor having to do; leaving them on a grid of years would be a pair of
  // controls whose effect is invisible on the screen they are drawn on.
  const steppers = view !== 'days' ? ['', ''] : [
    `<button class="period-month-step" type="button" data-action="period-month" data-delta="-1" ${previous ? '' : 'disabled'} aria-label="${escapeHtml(t('Previous month'))}">‹</button>`,
    `<button class="period-month-step" type="button" data-action="period-month" data-delta="1" ${next ? '' : 'disabled'} aria-label="${escapeHtml(t('Next month'))}">›</button>`,
  ]
  return `<div class="period-dialog-backdrop" data-action="period-dismiss">
    <div class="period-dialog" role="dialog" aria-modal="true" aria-labelledby="period-dialog-title" id="period-dialog">
      <div class="period-dialog-head">
        <h3 id="period-dialog-title">${escapeHtml(title)}</h3>
        <button class="period-dialog-close" type="button" data-action="period-close" aria-label="${escapeHtml(t('Close the calendar'))}">×</button>
      </div>
      <p class="period-dialog-hint" id="period-dialog-hint">${escapeHtml(hint)}</p>
      <div class="period-dialog-months">
        ${steppers[0]}
        ${caption(picker, view, cursor)}
        ${steppers[1]}
      </div>
      ${grid}
    </div>
  </div>`
}

/**
 * The caption, which is now two buttons and is the whole of WP2's answer.
 *
 * **Each half toggles its own view**, Material's behaviour: pressing *2026*
 * opens the years and pressing it again comes back to the days, which is the
 * only pointer route back out of a grid that has not been chosen from. A
 * keyboard has `Esc`, which nests for the same reason.
 *
 * `aria-expanded` says which grid is open, and the accessible name carries the
 * **value as well as the invitation** — "September, Choose a month" — because a
 * button named only "Choose a month" tells a screen-reader user what it does and
 * not what is currently true. The value is interpolated around the `t()` call
 * rather than into it: the key stays a plain quoted literal, which is what
 * `tests/web/i18n_keys.py`'s extractor can see.
 *
 * The id stays on the wrapper, not on either button, so that what
 * `#period-dialog-month` reads is still the caption — "September 2026" — in
 * every view.
 */
function caption(picker, view, cursor) {
  const part = (key, text, label, expanded) =>
    `<button class="period-caption" type="button" data-action="period-view" data-view="${key}" aria-expanded="${expanded ? 'true' : 'false'}" aria-label="${escapeHtml(label)}">${escapeHtml(text)}</button>`
  const month = MONTH_NAME.format(cursor)
  const year = String(cursor.getFullYear())
  return `<p class="period-dialog-month" id="period-dialog-month" aria-live="polite">${
    part('months', month, `${month}, ${t('Choose a month')}`, view === 'months')
  } ${
    part('years', year, `${year}, ${t('Choose a year')}`, view === 'years')
  }</p>`
}

/** Which grid is drawn. `'days'` for anything else, including the `undefined` a
 *  picker stored before this existed would carry. */
const pickerView = picker => (picker?.view === 'months' || picker?.view === 'years' ? picker.view : 'days')

/**
 * The calendar button's icon, **drawn rather than typed**.
 *
 * It was `🗓` (U+1F5D3) and it rendered as an empty rectangle on Windows. That
 * is not a missing font: **U+1F5D3 defaults to text presentation**, so a browser
 * draws it monochrome out of a symbol font at text weight. Measured in the
 * page's own stack it is 16.0px wide — the same as a capital M at 15.6px —
 * where an emoji-presentation code point such as `📅` (U+1F4C5) is 22.0px. A
 * variation selector would ask for the emoji form and would still be one font's
 * decision away from a box.
 *
 * §7.6 rule 7 forbids fetching an icon font, and `calculator.js` already carries
 * hand-written inline SVG for the hero pattern, so the icon is written out here.
 * `aria-hidden="true"`: the button's own `aria-label` is the accessible name and
 * a second one would be read twice. `stroke="currentColor"` so it inherits the
 * button's Kale and needs no colour of its own in either theme.
 */
const CALENDAR_ICON = '<svg class="period-open-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3.25" y="5.25" width="17.5" height="15.5" rx="2.5"/><path d="M3.25 10.25h17.5M8 3v4M16 3v4"/></svg>'

/**
 * The clock button's icon, drawn in the same hand as `CALENDAR_ICON` and for the
 * same two reasons: §7.6 rule 7 forbids fetching an icon font, and a clock code
 * point (`🕐` U+1F550, `⏰` U+23F0) is one font's decision away from the empty
 * rectangle U+1F5D3 actually drew.
 *
 * Same viewBox, same 1.8 stroke, same `currentColor`, same `aria-hidden` — the
 * two buttons sit one row apart on the same screen and a second visual weight
 * between them would read as two different kinds of control.
 */
const CLOCK_ICON = '<svg class="period-open-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="8.75"/><path d="M12 6.75V12l3.5 2.25"/></svg>'

const boundLabel = bound => (bound === 'start' ? t('Period start') : t('Period end'))
const boundOpenLabel = bound => (bound === 'start' ? t('Choose the start date') : t('Choose the end date'))
const boundClockLabel = bound => (bound === 'start' ? t('Choose the start time') : t('Choose the end time'))

function boundFields(bound, fields, problem) {
  const invalid = field => (problem.field === field ? ' aria-invalid="true"' : '')
  const dateId = `period-${bound}-date`
  const timeId = `period-${bound}-time`
  return `<fieldset class="period-bound">
    <legend>${escapeHtml(boundLabel(bound))}</legend>
    <div class="period-bound-row">
      <div class="form-field period-date-field">
        <label for="${dateId}">${escapeHtml(t('Date'))}</label>
        <div class="period-date-control">
          <input id="${dateId}" type="text" inputmode="numeric" autocomplete="off" class="period-input" data-period-field="${bound}Date" value="${escapeHtml(fields[`${bound}Date`])}" placeholder="dd/mm/yyyy" aria-describedby="period-format-hint"${invalid(dateId)}>
          <button class="period-open" type="button" id="period-open-${bound}" data-action="period-open" data-bound="${bound}" aria-haspopup="dialog" aria-label="${escapeHtml(boundOpenLabel(bound))}">${CALENDAR_ICON}</button>
        </div>
      </div>
      <div class="form-field period-time-field">
        <label for="${timeId}">${escapeHtml(t('Time'))}</label>
        <div class="period-time-control">
          <input id="${timeId}" type="text" inputmode="numeric" autocomplete="off" class="period-input" data-period-field="${bound}Time" value="${escapeHtml(fields[`${bound}Time`])}" placeholder="hh:mm" aria-describedby="period-format-hint"${invalid(timeId)}>
          <button class="period-open" type="button" id="period-clock-${bound}" data-action="period-clock-open" data-bound="${bound}" aria-haspopup="dialog" aria-label="${escapeHtml(boundClockLabel(bound))}">${CLOCK_ICON}</button>
        </div>
      </div>
    </div>
  </fieldset>`
}

/**
 * The whole field: two bounds, one error line, and the dialog when it is open.
 *
 * `dd/mm/yyyy` and `hh:mm` are placeholders and **not** `t()` keys. They are the
 * format, and the format is pinned to `en-NZ` by the decision at the top of this
 * file; handing a translator "dd/mm/yyyy" invites a translated `jj/mm/aaaa` on a
 * field that would then refuse what it asked for.
 */
export function PeriodField() {
  if (!periodOffered()) return ''
  const fields = periodFields()
  const problem = periodProblem(fields)
  return `<div class="period-field" id="period-field">
    <p class="field-hint" id="period-format-hint">${escapeHtml(t('Dates are dd/mm/yyyy and times are on the 24-hour clock, so a shift is 08:10 to 16:20.'))}</p>
    ${BOUNDS.map(bound => boundFields(bound, fields, problem)).join('')}
    <p class="field-error period-error" id="period-error" role="alert"${problem.message ? '' : ' hidden'}>${escapeHtml(problem.message)}</p>
    ${pickerDialog()}
    ${clockDialog()}
  </div>`
}

// ---------------------------------------------------------------------------
// The clock
// ---------------------------------------------------------------------------

/**
 * The face's geometry, in the 200-unit `viewBox` every number below is written
 * in.
 *
 * **User units, not pixels, and that is what makes the dial resolution-free.**
 * The `<svg>` is sized in CSS and the pointer arithmetic divides by the measured
 * radius, so the same constants describe the face at 240px on a phone and at
 * 280px on a laptop without a second set of numbers to keep in step.
 *
 * `RING_EDGE` is the fraction of the face's radius at which the outer ring stops
 * being the nearer one — **halfway between the two label rings**, derived rather
 * than typed, so that moving a ring moves the boundary with it. A press inside
 * that circle is an inner-ring hour (13–23 and 00); anything further out is
 * 1–12. Very near the centre it still reads as inner, which is right: there is
 * no third thing it could mean.
 */
const FACE_CENTRE = 100
const FACE_RADIUS = 96
const OUTER_RING = 80
const INNER_RING = 52
const KNOB_RADIUS = 17
const RING_EDGE = (OUTER_RING + INNER_RING) / 2 / FACE_RADIUS

/** Which stage a clock is on, defaulting the way `pickerView` does. */
const clockStage = clock => (clock?.stage === 'minutes' ? 'minutes' : 'hours')

/** 0, and 13 to 23: the hours the inner ring carries, so the hand is short for
 *  them. `12` is on the outer ring at the top and `0` is on the inner ring at
 *  the top, which is the one place the two rings disagree about a position. */
const isInnerHour = hours => hours === 0 || hours >= 13

/**
 * Degrees clockwise from twelve o'clock, for a pointer offset from the centre.
 *
 * `atan2(dx, -dy)` rather than the textbook `atan2(dy, dx)`: the arguments are
 * swapped and `dy` is negated so that zero is straight up and the angle grows
 * the way a clock does. Written this way rather than as `90 - atan2(dy, dx)`
 * because the correction term is exactly the sort of thing that survives a
 * refactor with the wrong sign.
 *
 * **`dy` grows downwards**, because that is what both a client rectangle and an
 * SVG `viewBox` do. The negation is the whole of the translation between "the
 * screen" and "a clock", and it is the reason `hourFromPointer(0, -1)` is 12
 * rather than 6.
 *
 * @param {number} dx pointer x minus the face's centre x.
 * @param {number} dy pointer y minus the face's centre y.
 * @returns {number} 0 ≤ degrees < 360.
 */
export function clockAngle(dx, dy) {
  // **The exact centre has no angle, so it is given one here rather than left to
  // IEEE.** `Math.atan2(0, -0)` is π — the negation of a zero `dy` produces
  // negative zero, which `atan2` reads as "the negative x axis" — so a press on
  // the dead centre of the face came back as *six o'clock*, and on the inner
  // ring that is 18:00. Measured, not reasoned about: it is
  // `tests/web/test_period_rules.py`'s centre case, which failed the first time
  // it was run. Twelve is the answer that surprises nobody, and the press is in
  // any case somebody who has not aimed yet.
  if (dx === 0 && dy === 0) return 0
  return ((Math.atan2(dx, -dy) * 180) / Math.PI + 360) % 360
}

/**
 * The hour a press at this offset means, **on whichever of the two rings it
 * landed on**.
 *
 * Twelve positions per ring, so the nearest is the angle rounded to 30°. The top
 * position is `12` on the outer ring and `0` on the inner one, and every other
 * inner position is its outer twin plus twelve — 13 at one o'clock, 23 at
 * eleven — which is Material's layout and the only one that puts 00:00 and 12:00
 * where a reader of a 24-hour clock looks for them.
 *
 * @param {number} dx pointer x minus centre x.
 * @param {number} dy pointer y minus centre y.
 * @param {number} radius the face's measured radius, in the same units as
 *   `dx`/`dy`. Only the *ratio* is used, so pixels and user units both work.
 * @returns {number} 0–23.
 */
export function hourFromPointer(dx, dy, radius) {
  const step = Math.round(clockAngle(dx, dy) / 30) % 12
  const inner = radius > 0 && Math.hypot(dx, dy) / radius < RING_EDGE
  if (!inner) return step === 0 ? 12 : step
  return step === 0 ? 0 : step + 12
}

/**
 * The minute a press at this offset means. **Sixty positions, not twelve.**
 *
 * The labels are drawn every five minutes and the value is not: a roster that
 * says 08:07 is a real shift, and a dial that snapped it to 08:05 or 08:10 would
 * refuse a period somebody actually worked. The ring is 6° per minute, so the
 * rounding is the same one line as the hours over a finer step.
 *
 * The distance from the centre is not read at all — there is one minute ring —
 * which is why this takes no radius and why a drag that wanders inwards while it
 * sweeps does not change the answer.
 *
 * @returns {number} 0–59.
 */
export function minuteFromPointer(dx, dy) {
  return Math.round(clockAngle(dx, dy) / 6) % 60
}

/** Where a label sits, in `viewBox` units. `sin`/`-cos` because zero degrees is
 *  straight up; see `clockAngle`. */
function facePoint(degrees, radius) {
  const radians = (degrees * Math.PI) / 180
  return {
    x: FACE_CENTRE + radius * Math.sin(radians),
    y: FACE_CENTRE - radius * Math.cos(radians),
  }
}

/** The twenty-four hour labels: 1–12 outside, then 00 and 13–23 inside. */
const HOUR_LABELS = [
  ...Array.from({ length: 12 }, (unused, index) => ({
    value: index === 0 ? 12 : index,
    text: String(index === 0 ? 12 : index),
    angle: index * 30,
    radius: OUTER_RING,
  })),
  ...Array.from({ length: 12 }, (unused, index) => ({
    value: index === 0 ? 0 : index + 12,
    text: pad(index === 0 ? 0 : index + 12),
    angle: index * 30,
    radius: INNER_RING,
  })),
]

/** Every fifth minute, and only every fifth minute. See `minuteFromPointer`:
 *  this list is what is *written*, never what can be *chosen*. */
const MINUTE_LABELS = Array.from({ length: 12 }, (unused, index) => ({
  value: index * 5,
  text: pad(index * 5),
  angle: index * 30,
  radius: OUTER_RING,
}))

/**
 * The face's contents, regenerated whole.
 *
 * **Returned as markup rather than patched attribute by attribute** because the
 * pointer path has to redraw this sixty times a second without a `setState` —
 * `render()` replaces `main.innerHTML`, which would destroy the element the
 * pointer is captured on, mid-drag. Assigning `svg.innerHTML` replaces the
 * children and leaves the `<svg>` itself — and therefore the capture — alone.
 *
 * ## The clock does not mirror under `dir="rtl"`, and that is deliberate
 *
 * The calendar one screen away *does* mirror, and its own RTL note says why: a
 * week is text laid out in reading order, so in Arabic Monday belongs on the
 * right and an `ArrowRight` that did not follow it would move the focus ring
 * backwards across the screen. **A clock is not text.** Three o'clock is to the
 * right of twelve in every country that uses this dial, an Arabic reader expects
 * the hand to sweep the same way as everyone else's, and a mirrored face would
 * put 3 where 9 is on the wall behind them.
 *
 * It is enforced by construction rather than by a rule anyone has to remember:
 * every position here is an explicit `x`/`y` in the `viewBox`, and SVG geometry
 * does not mirror with `direction`. The pointer maths is the same story from the
 * other end — `getBoundingClientRect()` is in viewport coordinates, which are
 * absolute. Nothing in `styles.css` positions a number, so there is no
 * `inset-inline-start` to flip and no physical property to smuggle in either.
 *
 * **If you are here to make this consistent with the calendar: don't.** Measure
 * it in Arabic first — `tests/web/test_period_clock_browser.py` already does,
 * and asserts that 3 is drawn to the right of 9 under `?lang=ar`.
 */
function faceContents(clock) {
  const stage = clockStage(clock)
  const value = stage === 'hours' ? clock.hours : clock.minutes
  const labels = stage === 'hours' ? HOUR_LABELS : MINUTE_LABELS
  const angle = stage === 'hours' ? (clock.hours % 12) * 30 : clock.minutes * 6
  const length = stage === 'hours' && isInnerHour(clock.hours) ? INNER_RING : OUTER_RING
  const labelled = labels.some(label => label.value === value)
  const tip = FACE_CENTRE - length
  const marks = labels.map(label => {
    const point = facePoint(label.angle, label.radius)
    return `<text class="period-clock-label${label.value === value ? ' is-current' : ''}" x="${point.x.toFixed(2)}" y="${point.y.toFixed(2)}" text-anchor="middle" dominant-baseline="central">${escapeHtml(label.text)}</text>`
  }).join('')
  // The hand is drawn before the labels so the knob sits *behind* the number it
  // has landed on; the label then inverts to white over it rather than being
  // covered by it. When the value carries no label — every minute that is not a
  // multiple of five — a small mark stands in for one, so the visitor can see
  // the hand has stopped somewhere real rather than between two numbers.
  return `<circle class="period-clock-dial" cx="${FACE_CENTRE}" cy="${FACE_CENTRE}" r="${FACE_RADIUS}"/>
    <g class="period-clock-hand" style="transform: rotate(${angle}deg)">
      <line class="period-clock-stem" x1="${FACE_CENTRE}" y1="${FACE_CENTRE}" x2="${FACE_CENTRE}" y2="${tip}"/>
      <circle class="period-clock-knob" cx="${FACE_CENTRE}" cy="${tip}" r="${KNOB_RADIUS}"/>
      ${labelled ? '' : `<circle class="period-clock-tick" cx="${FACE_CENTRE}" cy="${tip}" r="3.5"/>`}
    </g>
    <circle class="period-clock-hub" cx="${FACE_CENTRE}" cy="${FACE_CENTRE}" r="4"/>
    ${marks}`
}

/** `HH:MM` for what the dial is currently over. Not written anywhere until the
 *  visitor confirms — see `setClockTime`. */
const clockText = clock => `${pad(clock.hours)}:${pad(clock.minutes)}`

/**
 * The readout, which is also the way back to the hours.
 *
 * Two buttons, the same decomposition the calendar's caption went through in
 * WP2 and for the same reason: the stage is a thing the visitor has to be able
 * to change, and the value is the obvious place to press. `aria-pressed` carries
 * which stage is live, and each accessible name carries **the value as well as
 * the invitation** — "8, Choose the hour" — because a button named only "Choose
 * the hour" says what it does and not what is currently true.
 *
 * **The roving `id="period-clock-stage"` is the calendar's trick, reused.**
 * Whichever button is the live stage carries it, so when releasing the hour hand
 * advances to the minutes, `main.js`'s focus restore — which re-focuses
 * `document.activeElement.id` after a same-step render — lands on the minute
 * button rather than on the hour button the visitor has just left.
 */
function clockReadout(clock) {
  const stage = clockStage(clock)
  const part = (key, text, label) =>
    `<button class="period-clock-stage${stage === key ? ' is-current' : ''}" type="button" data-action="period-clock-stage" data-stage="${key}"${stage === key ? ' id="period-clock-stage"' : ''} aria-pressed="${stage === key ? 'true' : 'false'}" aria-label="${escapeHtml(label)}">${escapeHtml(text)}</button>`
  return `<div class="period-clock-readout">${
    part('hours', pad(clock.hours), `${clock.hours}, ${t('Choose the hour')}`)
  }<span class="period-clock-colon" aria-hidden="true">:</span>${
    part('minutes', pad(clock.minutes), `${clock.minutes}, ${t('Choose the minute')}`)
  }</div>`
}

/**
 * The keyboard mode: two number boxes.
 *
 * `type="number"` here and `type="text" inputmode="numeric"` on the four boxes
 * outside, which is not an inconsistency — see the header. These hold one
 * integer each, nothing masks them and nothing reads a caret out of them, so the
 * spinner and the `min`/`max` a number input brings are all wanted. The value is
 * written unpadded (`8`, not `08`): a number input normalises a leading zero
 * away on the first keystroke anyway, and a box that silently rewrote what was
 * typed is the defect the blur path outside exists to avoid.
 */
function clockEntry(clock) {
  const box = (key, id, label, max, value) =>
    `<div class="form-field period-clock-box">
      <label for="${id}">${escapeHtml(label)}</label>
      <input id="${id}" type="number" inputmode="numeric" autocomplete="off" min="0" max="${max}" step="1" data-period-clock-field="${key}" value="${value}">
    </div>`
  return `<div class="period-clock-entry">
    ${box('hours', 'period-clock-hour', t('Hour'), 23, clock.hours)}
    <span class="period-clock-colon" aria-hidden="true">:</span>
    ${box('minutes', 'period-clock-minute', t('Minute'), 59, clock.minutes)}
  </div>`
}

/**
 * The clock dialog: the same four dialog rules as the calendar's, over two modes
 * that are peers rather than a control and its fallback.
 *
 * The face is `aria-hidden="true"` because there is no honest way to expose 1,440
 * positions to a screen reader, and the live region under it is what a
 * screen-reader user hears instead while a sighted pointer user drags. Both are
 * in the markup in both modes' sight but only one is rendered at a time, so
 * there is never a second claimant to `#period-clock-value`.
 */
function clockDialog() {
  const clock = state.periodClock
  if (!clock) return ''
  const keyboard = clock.mode === 'keyboard'
  const title = boundClockLabel(clock.field)
  const hint = keyboard
    ? t('Type the hour and the minute on the 24-hour clock.')
    : t('Drag the hand or press a number to set the hour, then the minute.')
  // **The toggle names the mode it switches *to*, never the one it is in.** A
  // button labelled with the current mode is a label a visitor reads as a
  // statement and presses expecting nothing to change.
  const toggle = keyboard ? t('Choose the time on a clock face') : t('Enter the time on a keyboard')
  const body = keyboard
    ? clockEntry(clock)
    : `${clockReadout(clock)}
      <svg class="period-clock-face" viewBox="0 0 200 200" aria-hidden="true" focusable="false">${faceContents(clock)}</svg>`
  return `<div class="period-dialog-backdrop" data-action="period-clock-dismiss">
    <div class="period-dialog period-clock-dialog" role="dialog" aria-modal="true" aria-labelledby="period-clock-title" id="period-clock-dialog">
      <div class="period-dialog-head">
        <h3 id="period-clock-title">${escapeHtml(title)}</h3>
        <button class="period-dialog-close" type="button" data-action="period-clock-close" aria-label="${escapeHtml(t('Close the clock'))}">×</button>
      </div>
      <p class="period-dialog-hint" id="period-clock-hint">${escapeHtml(hint)}</p>
      ${body}
      <p class="sr-only" id="period-clock-value" aria-live="polite">${escapeHtml(clockText(clock))}</p>
      <div class="period-clock-actions">
        <button class="period-clock-mode" type="button" data-action="period-clock-mode">${escapeHtml(toggle)}</button>
        <span class="period-clock-confirm">
          <button class="period-clock-cancel" type="button" data-action="period-clock-close">${escapeHtml(t('Cancel'))}</button>
          <button class="period-clock-set" type="button" data-action="period-clock-set">${escapeHtml(t('Set the time'))}</button>
        </span>
      </div>
    </div>
  </div>`
}

// ---------------------------------------------------------------------------
// Behaviour
// ---------------------------------------------------------------------------

/**
 * Whichever element should hold focus once the next render has happened.
 *
 * `requestAnimationFrame` rather than a direct call, because `setState` renders
 * synchronously and `main.js` then puts focus back on the id that had it — which
 * for an opening dialog is the button outside it. The frame after is the first
 * moment this module can have the last word. `calculator.js`'s breakdown tabs do
 * the same thing for the same reason.
 */
const focusAfterRender = id => requestAnimationFrame(() => {
  const target = document.getElementById(id)
  if (!target) return
  // **The year grid is twelve rows in a scrolling box and has to open showing
  // the year the visitor is on**, not 1970. `.focus()` would scroll it into view
  // on its own, but only to `nearest` — which puts the cursor hard against an
  // edge with no years visible on one side of it. So the scroll is taken away
  // from `.focus()` and done here, centred, and it moves no focus of its own:
  // the element being scrolled to is the element that has just been focused.
  const scroller = target.closest('.period-scroller')
  target.focus({ preventScroll: Boolean(scroller) })
  if (!scroller) return
  scroller.scrollTop = Math.max(0, target.offsetTop - (scroller.clientHeight - target.offsetHeight) / 2)
})

/**
 * Editing the interval by hand demotes a preset to `custom`.
 *
 * §6.2: `time_frame` records **which shortcut was pressed**, and somebody who
 * pressed *One week* and then moved the start back three days did not press a
 * shortcut for what is now in the fields — they chose those dates, which is what
 * `custom` means. Without this the row would claim a standard week over an
 * interval that is not one, and the first question the client asks of this
 * column ("did they mean a week, or did they pick those dates?") would get the
 * wrong answer.
 *
 * Returned as a patch rather than applied, so the two callers — the typing fast
 * path, which must not `setState`, and the calendar, which must — can each use
 * it in their own way.
 */
const demotion = () => (state.timeFrame && state.timeFrame !== 'custom' ? { timeFrame: 'custom' } : {})

/**
 * Put one problem on screen without re-rendering the step.
 *
 * The typing path's half of §7.3a's documented exception. It writes exactly what
 * `PeriodField` would have written — the same `periodProblem`, the same error
 * text, the same `aria-invalid` flags, the same Calculate button — so the two
 * paths cannot show different answers for the same four strings.
 */
function applyPeriodProblem(problem, otherwiseDisabled) {
  const error = document.getElementById('period-error')
  if (error) {
    error.textContent = problem.message
    error.hidden = !problem.message
  }
  for (const bound of BOUNDS) {
    for (const part of ['date', 'time']) {
      const id = `period-${bound}-${part}`
      const input = document.getElementById(id)
      if (!input) continue
      if (problem.field === id) input.setAttribute('aria-invalid', 'true')
      else input.removeAttribute('aria-invalid')
    }
  }
  // **The whole condition, not this module's half of it.** `otherwiseDisabled`
  // is what `reviewStep` would have computed for every other reason the button
  // can be off — loading, the rate-limit window, a `BLOCKED` factor set — so the
  // two paths write the same boolean. Patching only the period half would let a
  // corrected date re-enable Calculate during a request that is still in flight.
  const calculate = document.querySelector('.step-nav [data-action="calculate"]')
  if (calculate) calculate.disabled = Boolean(problem.message) || Boolean(otherwiseDisabled)
}

/**
 * Everything both hand-patching paths do once one of the four strings has
 * changed: demote, ask, store, keep the select honest, put the answer on screen.
 *
 * **One function, because there are now two of these paths** — a keystroke and a
 * blur — and §7.3a's whole argument for the exception is that the typing path
 * and the render path cannot come to different conclusions about the same four
 * strings. Two hand-patchers written separately would be a third and a fourth
 * conclusion.
 *
 * @returns {boolean} always `true`: the caller owned the event.
 */
function commitPeriodFields(fields, otherwiseDisabled) {
  // **The demotion is decided before the problem is asked, and the answer this
  // keystroke leaves behind is what is asked about** (v1.68). `periodProblem`'s
  // `custom` clause is a question about `time_frame`, and this is the one
  // handler that can change `time_frame` in the same breath as the fields.
  //
  // **Measured, because the obvious claim about this line is not true.**
  // Swapping these two back — ask first, demote after — leaves every test in
  // `tests/web/test_period_submission_browser.py` green, and that is an
  // equivalent mutation rather than a weak test: emptying a preset-filled
  // interval takes four edits and the demotion lands on the *first* of them,
  // so by the edit that empties the last box `state.timeFrame` already reads
  // `custom` and a default read reaches the same answer. The select's own
  // value was read after each of the four to confirm it.
  //
  // It is written this way regardless, because that agreement is an accident
  // of how many boxes there are: a control that cleared the whole interval in
  // one event would demote and empty on the same keystroke, and the version
  // that reads `state` would then answer about `one_week` — which with no
  // interval is perfectly legal — and leave Calculate live over a 422. The
  // clause itself, and the parameter, are asserted directly in
  // `tests/web/test_period_rules.py`.
  const patch = demotion()
  const problem = periodProblem(fields, patch.timeFrame ?? state.timeFrame)
  Object.assign(state, { periodFields: fields, ...periodValues(fields), ...patch })
  // The select is the only part of the step this path may not leave stale: it is
  // on screen beside the fields, and a demoted `time_frame` that still read "One
  // week" would be the form telling the visitor something the state does not say.
  const select = document.getElementById('time-frame')
  if (select && select.value !== state.timeFrame) select.value = state.timeFrame
  applyPeriodProblem(problem, otherwiseDisabled)
  return true
}

/**
 * A keystroke in one of the four text boxes.
 *
 * **No `setState`.** `render()` replaces `main.innerHTML`, so a re-render per
 * keystroke destroys the caret, and `change` on a text input fires while focus
 * is already leaving it, so a re-render there can drag focus back out of the
 * control the visitor just tabbed to. `state` is mutated directly and the three
 * things a render would have changed are patched by hand, which is the exception
 * §7.3a documents for `updateLine` and for the same reason. The separator this
 * handler inserts is written straight onto `event.target.value`, which is
 * precisely what that exception exists to allow.
 *
 * **The mask runs on an insertion and never on a deletion**, which is the first
 * of the three traps. Re-applying it on every `input` event makes the `/`
 * undeletable: the visitor backspaces it away, the next event puts it straight
 * back, and the caret sits still while nothing appears to happen. `inputType`
 * is what tells the two apart — `insertText` and `insertFromPaste` are the two
 * that add characters, and every `delete*` and `history*` is left alone.
 *
 * @returns {boolean} whether this module owned the event.
 */
export function handlePeriodInput(event, otherwiseDisabled = false) {
  // The clock's two number boxes come through the same delegated `input`
  // listener and are the same exception — see `handleClockInput`.
  if (handleClockInput(event)) return true
  const field = event.target.dataset.periodField
  if (!field) return false
  if (INSERTIONS.has(event.inputType)) applyPeriodMask(event, field)
  return commitPeriodFields({ ...periodFields(), [field]: event.target.value }, otherwiseDisabled)
}

/** The two `inputType`s that add characters. See `handlePeriodInput`. */
const INSERTIONS = new Set(['insertText', 'insertFromPaste'])

/**
 * `true` between `compositionstart` and `compositionend` in one of these boxes.
 *
 * **An IME's composition must not be reformatted while it is being composed**,
 * or the composed text is destroyed as it is being typed. `inputmode="numeric"`
 * makes this unlikely rather than impossible: a physical keyboard with an IME
 * active reaches these boxes like any other. The `inputType` branch above
 * already skips `insertCompositionText`; this flag covers the final event a
 * composition commits, which some engines report as an ordinary insertion.
 */
let composing = false

/** `compositionstart` / `compositionend` on one of the four boxes. */
export function handlePeriodComposition(event) {
  if (!event.target?.dataset?.periodField) return false
  composing = event.type === 'compositionstart'
  return true
}

/**
 * `maskPeriodStep`'s answer, written into the box, with the caret kept where the
 * visitor left it.
 *
 * The caret is read **before** the value is rewritten, because assigning to
 * `.value` moves it to the end of the box, and it is read and restored as a
 * count of digits rather than of characters — the second of the three traps.
 *
 * **`data-period-masked` is where "are these separators mine?" is kept**, on the
 * element rather than in `state`. On the element deliberately: it is a fact
 * about a typing session and not about the answer being given, and a render —
 * a preset, the calendar, a step change — replaces the element and so clears it,
 * which is exactly right. A value the calendar wrote is already canonical and is
 * nobody's to re-punctuate.
 */
function applyPeriodMask(event, field) {
  if (composing) return
  const target = event.target
  const typed = target.value
  const step = maskPeriodStep({
    before: String(periodFields()[field] ?? ''),
    typed,
    // `event.data` is the characters this event added. A paste can leave it
    // null, with the content only on `dataTransfer`; that is read as "not
    // digits" rather than guessed at, so an unreadable paste is left alone
    // instead of being reformatted on an assumption.
    inserted: typeof event.data === 'string' ? event.data : null,
    masked: target.dataset.periodMasked === 'true',
  }, field)
  target.dataset.periodMasked = String(step.masked)
  if (step.value === typed) return
  const digits = digitsBefore(typed, target.selectionStart ?? typed.length)
  target.value = step.value
  const caret = caretAfterDigits(step.value, digits)
  // `setSelectionRange` throws on an input whose type does not support a
  // selection. These four are `type="text"` and do, but a throw here would stop
  // the whole handler — including the error line it has not written yet — so the
  // caret is the one part of this that is allowed to fail quietly.
  try { target.setSelectionRange(caret, caret) } catch { /* no selection to place */ }
}

/**
 * The caret has left one of the four boxes: tidy what is in it.
 *
 * `1/1/2026` becomes `01/01/2026`, `8` becomes `08:00` and `8:5` becomes
 * `08:05`. **The values were already correct before this existed** — the parsers
 * accepted every one of those shapes and the right instant reached the wire —
 * so this changes appearance and nothing else, and the test that guards it
 * compares two request bodies byte for byte.
 *
 * **A value that does not parse is left exactly as it was typed.** Somebody who
 * wrote a wrong date needs to see what they wrote in order to fix it, and
 * rewriting a value the visitor did not choose is worse than showing them one
 * that is wrong.
 *
 * The rewrite goes through `commitPeriodFields` rather than `setState` for the
 * reason blur is the worst possible moment for a render: focus is already
 * moving, and `render()` replacing `main.innerHTML` underneath a focus
 * transition drags it back out of the control the visitor tabbed to.
 *
 * @returns {boolean} whether this module owned the event.
 */
export function handlePeriodBlur(event, otherwiseDisabled = false) {
  if (handleClockBlur(event)) return true
  const field = event.target?.dataset?.periodField
  if (!field) return false
  composing = false
  const typed = event.target.value
  const tidy = canonicalPeriodText(field, typed)
  if (tidy === null || tidy === typed) return true
  event.target.value = tidy
  return commitPeriodFields({ ...periodFields(), [field]: tidy }, otherwiseDisabled)
}

/**
 * One box's text in the shape the field itself would have written it, or `null`
 * when the text does not parse.
 *
 * `DATE_DISPLAY` and `parseTimeText`, and not a second formatter: these are the
 * two the presets, the calendar and `formatInstant` already fill these boxes
 * from, so what a blur writes is character-for-character what the rest of the
 * feature writes.
 */
function canonicalPeriodText(field, text) {
  if (field.endsWith('Date')) {
    const iso = parseDateText(text)
    return iso === null ? null : DATE_DISPLAY.format(dayOf(iso))
  }
  return parseTimeText(text)
}

/**
 * Every click this module owns. Returns whether it owned it, so
 * `calculator.js`'s delegated handler can go on to its own actions.
 */
export function handlePeriodClick(action, control, event) {
  // The clock's seven actions, kept in their own function so that this one does
  // not become the place two dialogs are told apart by prefix.
  if (action.startsWith('period-clock-')) return handleClockClick(action, control, event)
  if (action === 'period-open') {
    const bound = control.dataset.bound
    // **Always `'days'`.** A picker that remembered the year grid from last time
    // would open on the wrong question — see the header. The clock closes:
    // two modal dialogs on one screen is two focus traps over one `Tab`.
    setState({ periodClock: null, periodPicker: { field: bound, cursor: openingCursor(bound), openerId: control.id, view: 'days' } })
    focusAfterRender('period-grid-focus')
    return true
  }
  // A click on the backdrop dismisses; a click *inside* the dialog must not.
  // The dialog is a child of the backdrop, so `closest('[data-action]')` from
  // anything inside it finds the backdrop — the identity test is what tells the
  // two apart, and without it clicking the dialog's own hint text closed it.
  if (action === 'period-dismiss' && event?.target !== control) return false
  if (action === 'period-close' || action === 'period-dismiss') {
    closePicker()
    return true
  }
  if (action === 'period-month') {
    moveCursor(addMonths(dayOf(state.periodPicker.cursor), Number(control.dataset.delta)))
    return true
  }
  if (action === 'period-day') {
    chooseDay(control.dataset.day)
    return true
  }
  if (action === 'period-view') {
    // Pressing the button for the view that is already open goes back to the
    // days. See `caption`: it is the pointer's way out of a grid, and it is what
    // `aria-expanded` on those two buttons is describing.
    const wanted = control.dataset.view
    setView(pickerView(state.periodPicker) === wanted ? 'days' : wanted)
    return true
  }
  if (action === 'period-choose-year') {
    chooseYear(Number(control.dataset.value))
    return true
  }
  if (action === 'period-choose-month') {
    chooseMonth(Number(control.dataset.value))
    return true
  }
  return false
}

/** Swap the grid without disturbing the cursor. The roving id moves to whichever
 *  cell is the cursor in the new view, so the focus lands inside the grid that
 *  has just been drawn rather than on the button that drew it. */
function setView(view) {
  const picker = state.periodPicker
  if (!picker) return
  setState({ periodPicker: { ...picker, view } })
  focusAfterRender('period-grid-focus')
}

/**
 * A year or a month chosen: the cursor moves there and the day grid comes back.
 *
 * **Nothing is written into a field and nothing is demoted.** `chooseDay` is
 * still the only function that states a date, which is what keeps `time_frame`
 * a record of which shortcut was pressed rather than of which grid was opened.
 *
 * The day of the month is clamped rather than overflowed, for `addMonths`'s
 * reason: a cursor on 31 March taken to a year's February would otherwise land
 * in March, and the visitor would watch the grid move somewhere they did not ask
 * for.
 */
function moveCursorTo(year, month) {
  const picker = state.periodPicker
  if (!picker) return
  const cursor = dayOf(picker.cursor)
  const lastDay = new Date(year, month + 1, 0).getDate()
  const target = new Date(year, month, Math.min(cursor.getDate(), lastDay))
  setState({ periodPicker: { ...picker, view: 'days', cursor: clampDay(isoDay(target)) } })
  focusAfterRender('period-grid-focus')
}

function chooseYear(year) {
  if (!state.periodPicker || !yearEnabled(year)) return
  moveCursorTo(year, dayOf(state.periodPicker.cursor).getMonth())
}

function chooseMonth(month) {
  const picker = state.periodPicker
  if (!picker) return
  const year = dayOf(picker.cursor).getFullYear()
  if (!monthEnabled(year, month)) return
  moveCursorTo(year, month)
}

function closePicker() {
  const openerId = state.periodPicker?.openerId
  setState({ periodPicker: null })
  // The rule that makes this a dialog rather than a panel: focus goes back to
  // the control that opened it, not to `<main>`, and not to the top of the form.
  if (openerId) focusAfterRender(openerId)
}

/** Move the roving cursor, clamped into the allowed range so it can never land
 *  on a day the grid has disabled. */
function moveCursor(date) {
  const picker = state.periodPicker
  if (!picker) return
  const cursor = clampDay(isoDay(date))
  setState({ periodPicker: { ...picker, cursor } })
  // The cell carrying `id="period-grid-focus"` is whichever one is now the
  // cursor, so this lands on the day that just moved rather than the day that
  // was left. See the header.
  focusAfterRender('period-grid-focus')
}

function chooseDay(iso) {
  const picker = state.periodPicker
  if (!picker || !dayEnabled(iso)) return
  const fields = { ...periodFields(), [`${picker.field}Date`]: DATE_DISPLAY.format(dayOf(iso)) }
  // A date chosen with no time beside it would be half a bound and the form
  // would refuse it. Midnight is the honest default for a day somebody picked
  // off a calendar, and it is visible and editable in the box next door.
  const timeKey = `${picker.field}Time`
  if (!parseTimeText(fields[timeKey])) fields[timeKey] = '00:00'
  const openerId = picker.openerId
  setState({ periodFields: fields, ...periodValues(fields), ...demotion(), periodPicker: null })
  if (openerId) focusAfterRender(openerId)
}

/**
 * The focusable controls inside one open dialog, in document order.
 *
 * Parametrised on the dialog's id from WP3, because there are two of these now
 * and a `Tab` cycle that queried both would let `Tab` walk out of the clock and
 * into a calendar that is not on screen. `input` joined the selector at the same
 * time and changes nothing for the calendar, which has none.
 */
const dialogStops = id => [...document.querySelectorAll(`#${id} button:not([disabled]), #${id} input:not([disabled]), #${id} [tabindex="0"]`)]

/**
 * `Tab` and `Shift`+`Tab` held inside one dialog.
 *
 * `aria-modal` tells a screen reader the rest of the page is inert; it does not
 * stop `Tab` reaching it. This does — and it is shared by the two dialogs
 * because a trap written twice is a trap that is right once.
 *
 * @returns {boolean} whether the key was taken.
 */
function trapTab(event, id) {
  const stops = dialogStops(id)
  if (!stops.length) return false
  const first = stops[0]
  const last = stops[stops.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
    return true
  }
  if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
    return true
  }
  return false
}

/**
 * The keyboard, and it is the whole of what makes this component usable.
 *
 * | Key | In the grid |
 * | --- | --- |
 * | `ArrowLeft` / `ArrowRight` | one day, **in the direction the grid is drawn** |
 * | `ArrowUp` / `ArrowDown` | one week |
 * | `PageUp` / `PageDown` | one month |
 * | `Shift` + `PageUp` / `PageDown` | one year |
 * | `Home` / `End` | the ends of the displayed week |
 * | `Enter` / `Space` | choose the focused day |
 *
 * The month grid and the year grid take the same four arrows, `Home`/`End` and
 * `Enter`/`Space`, over their own step — a month, a year, a row of each. They
 * take no `PageUp`/`PageDown`, and the hint drawn over them does not offer any.
 *
 * `Esc` **nests** — a year or month grid goes back to the days, and only the day
 * grid closes the dialog — and `Tab` cycles within it.
 *
 * **The horizontal arrows are mirrored under `dir="rtl"` and that is not a
 * nicety.** Arabic and Urdu render this page right-to-left and a `<table>`'s
 * columns mirror with the document, so the cell drawn to the right of the cursor
 * in Arabic is the *previous* day. An unmirrored `ArrowRight` would move focus
 * leftwards across the screen — the one thing an arrow key may not do.
 */
export function handlePeriodKeydown(event) {
  if (handleClockKeydown(event)) return true
  const picker = state.periodPicker
  if (!picker) return false
  const dialog = document.getElementById('period-dialog')
  if (!dialog || !dialog.contains(event.target)) return false
  if (event.key === 'Escape') {
    event.preventDefault()
    // **`Esc` nests.** From a year or a month grid it goes back to the days;
    // only from the days does it close the dialog. One `Esc` that closed
    // everything from three levels deep would lose the visitor's place — and the
    // month they had navigated to — for nothing.
    if (pickerView(picker) !== 'days') setView('days')
    else closePicker()
    return true
  }
  if (event.key === 'Tab') return trapTab(event, 'period-dialog')
  const cell = event.target.closest('[data-action="period-day"], [data-action="period-choose-month"], [data-action="period-choose-year"]')
  if (!cell) return false
  const view = pickerView(picker)
  const cursor = dayOf(picker.cursor)
  // **Measured under `dir="rtl"`, not assumed**, in all three grids and for one
  // reason: a `<table>`'s columns mirror with the document, so in Arabic the cell
  // drawn to the right of the cursor is the *previous* one — the previous day,
  // the previous month, the previous year. An unmirrored `ArrowRight` would move
  // the focus ring leftwards across the screen, which is the one thing an arrow
  // key may not do. The three grids are all tables and all mirror together, so
  // the same sign serves all three.
  const rtl = getComputedStyle(cell).direction === 'rtl'
  const horizontal = rtl ? -1 : 1
  const moves = view === 'years' ? yearMoves(cursor, horizontal)
    : view === 'months' ? monthMoves(cursor, horizontal)
      : dayMoves(cursor, horizontal, event.shiftKey)
  if (moves[event.key]) {
    event.preventDefault()
    moveCursor(moves[event.key]())
    return true
  }
  if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
    event.preventDefault()
    if (view === 'years') chooseYear(Number(cell.dataset.value))
    else if (view === 'months') chooseMonth(Number(cell.dataset.value))
    else chooseDay(cell.dataset.day)
    return true
  }
  return false
}

const dayMoves = (cursor, horizontal, shift) => ({
  ArrowLeft: () => addDays(cursor, -horizontal),
  ArrowRight: () => addDays(cursor, horizontal),
  ArrowUp: () => addDays(cursor, -7),
  ArrowDown: () => addDays(cursor, 7),
  PageUp: () => (shift ? addMonths(cursor, -12) : addMonths(cursor, -1)),
  PageDown: () => (shift ? addMonths(cursor, 12) : addMonths(cursor, 1)),
  Home: () => addDays(cursor, -((cursor.getDay() - FIRST_DAY_OF_WEEK + 7) % 7)),
  End: () => addDays(cursor, 6 - ((cursor.getDay() - FIRST_DAY_OF_WEEK + 7) % 7)),
})

/**
 * The month grid's keys: one month sideways, one row — a quarter — vertically,
 * and `Home`/`End` to the ends of that row.
 *
 * **Vertical movement crosses the year**, exactly as the day grid's crosses the
 * month: `ArrowUp` from January is October of the year before, and the grid
 * follows the cursor there. Clamping it inside the displayed year would leave
 * four cells from which an arrow key does nothing, which is how a keyboard user
 * concludes a control is broken.
 *
 * No `PageUp`/`PageDown`. There is nothing for them to mean that an arrow does
 * not already mean better, and the hint over this grid does not claim them.
 */
const monthMoves = (cursor, horizontal) => {
  const column = cursor.getMonth() % MONTHS_PER_ROW
  return {
    ArrowLeft: () => addMonths(cursor, -horizontal),
    ArrowRight: () => addMonths(cursor, horizontal),
    ArrowUp: () => addMonths(cursor, -MONTHS_PER_ROW),
    ArrowDown: () => addMonths(cursor, MONTHS_PER_ROW),
    Home: () => addMonths(cursor, -column),
    End: () => addMonths(cursor, MONTHS_PER_ROW - 1 - column),
  }
}

/**
 * The year grid's keys: one year sideways, one row — five years — vertically.
 *
 * `column` is `year % YEARS_PER_ROW` only because 1970 is a multiple of five and
 * the rows are laid out from it; it is written as the distance from the row's
 * start so that it stays right if either number ever moves.
 */
const yearMoves = (cursor, horizontal) => {
  const column = (cursor.getFullYear() - FLOOR_YEAR) % YEARS_PER_ROW
  return {
    ArrowLeft: () => addYears(cursor, -horizontal),
    ArrowRight: () => addYears(cursor, horizontal),
    ArrowUp: () => addYears(cursor, -YEARS_PER_ROW),
    ArrowDown: () => addYears(cursor, YEARS_PER_ROW),
    Home: () => addYears(cursor, -column),
    End: () => addYears(cursor, YEARS_PER_ROW - 1 - column),
  }
}

// ---------------------------------------------------------------------------
// The clock's behaviour
// ---------------------------------------------------------------------------

/**
 * Which mode a press of the clock button opens, decided by **how it was
 * pressed**.
 *
 * A pointer press opens the dial; `Enter` or `Space` on the focused button opens
 * the two number boxes. This is Android's rule and it is the whole of the
 * control's accessibility story in one line: a keyboard user is never dropped
 * into a face they cannot drive, and a pointer user is never handed two spinners
 * when they reached for a dial.
 *
 * **`event.detail === 0` is what tells the two apart.** `UIEvent.detail` on a
 * click is the click count — 1 for a real press, 2 for the second of a
 * double-click — and a click *synthesised* from a key press on a focused button
 * carries 0, because no button was clicked any number of times. There is no
 * other signal on the event that distinguishes them: `pointerType` is not on a
 * `click`, `event.isTrusted` is `true` for both, and `screenX`/`screenY` are 0
 * for a keyboard press *and* for a pointer press at the screen's origin.
 *
 * Written as a function of its own so that the condition has a name and a test
 * can state it. `tests/web/test_period_clock_browser.py` presses the button both
 * ways and asserts which mode came up; inverting the comparison here fails it
 * twice over, which is what a one-character condition carrying this much needs.
 *
 * @param {Event} [event] the click that opened it.
 * @returns {'dial'|'keyboard'}
 */
export function openClockMode(event) {
  return event && event.detail === 0 ? 'keyboard' : 'dial'
}

/**
 * Where a freshly-opened clock puts its hand: what the box already holds, else
 * the visitor's own now.
 *
 * The same rule as `openingCursor`'s, one field over, and for the same reason —
 * a control that opened at midnight every time would make the common case (a
 * shift today) the longest drag on the face. **Nothing is written by opening**:
 * this is where the hand starts, and `setClockTime` is the only function that
 * puts a value in the box.
 */
function openingTime(bound) {
  const typed = parseTimeText(periodFields()[`${bound}Time`])
  if (typed) {
    const [hours, minutes] = typed.split(':').map(Number)
    return { hours, minutes }
  }
  const at = now()
  return { hours: at.getHours(), minutes: at.getMinutes() }
}

/** Open the clock on one bound. Closes the calendar if it was open: two modal
 *  dialogs on one screen is two focus traps fighting over the same `Tab`. */
function openClock(bound, openerId, mode) {
  setState({
    periodPicker: null,
    periodClock: { field: bound, stage: 'hours', mode, openerId, ...openingTime(bound) },
  })
  // In the dial the face is `aria-hidden` and unfocusable, so the live stage's
  // readout button is where focus goes — it is a real control, it is the way
  // back between the stages, and it keeps `Esc` and the `Tab` cycle reachable.
  // In the keyboard mode the hour box is the obvious first thing to type into.
  focusAfterRender(mode === 'keyboard' ? 'period-clock-hour' : 'period-clock-stage')
}

function closeClock() {
  const openerId = state.periodClock?.openerId
  setState({ periodClock: null })
  // The rule that makes this a dialog rather than a panel, and the same one
  // `closePicker` follows: focus goes back to the control that opened it.
  if (openerId) focusAfterRender(openerId)
}

/**
 * The visitor's answer, written into the field.
 *
 * **Two things this deliberately does not do**, both of them written out at
 * length in the header because both are the kind of helpfulness that invents a
 * value nobody stated:
 *
 *   * it does not check the 24-hour ceiling. `periodProblem` owns that rule; the
 *     time goes into `state.periodFields` and the existing check answers, which
 *     is exactly what `chooseDay` does with a date;
 *   * it does not fill the date beside it. `chooseDay` fills a missing *time*
 *     with `00:00` because a date alone is half a bound and would be refused —
 *     but a date guessed from a time is not the mirror of that, it is a day the
 *     visitor never said.
 *
 * `demotion()` because writing a time **is** an edit: somebody who pressed *One
 * week* and then set the start to 08:10 did not press a shortcut for what is now
 * in the fields.
 */
function setClockTime() {
  const clock = state.periodClock
  if (!clock) return
  const fields = { ...periodFields(), [`${clock.field}Time`]: clockText(clock) }
  const openerId = clock.openerId
  setState({ periodFields: fields, ...periodValues(fields), ...demotion(), periodClock: null })
  if (openerId) focusAfterRender(openerId)
}

/** Swap the dial for the number boxes or back, keeping the value. Focus follows
 *  into whichever control the new mode's visitor is expected to use, for
 *  `openClock`'s reason. */
function setClockMode(mode) {
  const clock = state.periodClock
  if (!clock) return
  setState({ periodClock: { ...clock, mode } })
  focusAfterRender(mode === 'keyboard' ? 'period-clock-hour' : 'period-clock-stage')
}

function setClockStage(stage) {
  const clock = state.periodClock
  if (!clock) return
  setState({ periodClock: { ...clock, stage } })
  focusAfterRender('period-clock-stage')
}

/**
 * The dial redrawn from `state.periodClock` **without a `setState`**.
 *
 * §7.3a's documented exception again, for a second reason the typing path has
 * already established. A `setState` per `pointermove` would re-render the step —
 * `render()` replaces `main.innerHTML` — and the `<svg>` the pointer is
 * *captured* on would be destroyed underneath the drag, which ends the gesture
 * in the middle of it. So the drag mutates `state.periodClock` in place and
 * patches the three things a render would have changed: the face, the readout,
 * and the live region.
 *
 * The face is patched by replacing the `<svg>`'s children rather than the
 * `<svg>`, for exactly that reason — the capture is on the element, and the
 * element survives.
 */
function redrawClock() {
  const clock = state.periodClock
  if (!clock) return
  const face = document.querySelector('.period-clock-face')
  if (face) face.innerHTML = faceContents(clock)
  for (const key of ['hours', 'minutes']) {
    const button = document.querySelector(`[data-action="period-clock-stage"][data-stage="${key}"]`)
    if (!button) continue
    button.textContent = pad(clock[key])
    button.setAttribute('aria-label', `${clock[key]}, ${key === 'hours' ? t('Choose the hour') : t('Choose the minute')}`)
  }
  announceClock()
}

/** The chosen value, said once, politely. This is what a screen-reader user
 *  hears in place of a face that is `aria-hidden` on purpose. */
function announceClock() {
  const live = document.getElementById('period-clock-value')
  if (live && state.periodClock) live.textContent = clockText(state.periodClock)
}

/** The pointer id of the drag in progress, or `null`. One at a time: a second
 *  finger on the face during a drag is the first finger's gesture continuing,
 *  not a second answer. */
let clockPointer = null

/**
 * The whole dial, from `pointerdown` to `pointerup`.
 *
 * **One handler for mouse, touch and pen**, which is the point of pointer events
 * and the reason there is no `mousedown`/`touchstart` pair anywhere in this
 * file. A tap is a `pointerdown` and a `pointerup` with nothing in between, and
 * a drag is the same two with `pointermove`s in the middle, so the two gestures
 * are the same code and cannot come apart.
 *
 * **`setPointerCapture` is what makes a drag off the edge of the face keep
 * working.** Without it, a sweep that leaves the circle stops being delivered
 * and the hand freezes where the pointer crossed the boundary — which is most
 * drags, because a person aiming at "ten past" pushes outwards. With it every
 * later event is retargeted to the `<svg>`, so the angle goes on being read
 * however far out the pointer is, and a release anywhere on the page still lands
 * here.
 *
 * `preventDefault()` on `pointerdown` suppresses the compatibility `mousedown`,
 * which is what would otherwise blur the readout button and drop focus on
 * `<body>` — taking `Esc` and the `Tab` cycle with it. It also stops a touch
 * drag being read as a scroll, together with `touch-action: none` in the
 * stylesheet.
 *
 * @returns {boolean} whether this module owned the event.
 */
export function handlePeriodPointer(event) {
  if (event.type === 'pointerdown') {
    const face = event.target?.closest?.('.period-clock-face')
    if (!face || !state.periodClock) return false
    event.preventDefault()
    clockPointer = event.pointerId
    try { face.setPointerCapture(event.pointerId) } catch { /* capture is an optimisation, not the gesture */ }
    face.classList.add('is-dragging')
    trackClock(face, event)
    return true
  }
  if (clockPointer === null || event.pointerId !== clockPointer) return false
  const face = document.querySelector('.period-clock-face')
  if (!face || !state.periodClock) { clockPointer = null; return false }
  if (event.type === 'pointermove') { trackClock(face, event); return true }
  clockPointer = null
  face.classList.remove('is-dragging')
  // **`pointercancel` is not a choice.** The browser sends it when the gesture
  // was taken over — a scroll, a system gesture — and reading a value out of it
  // would set the hand from a press the visitor did not finish. The hand stays
  // where the last `pointermove` left it and nothing advances.
  if (event.type !== 'pointerup') return true
  trackClock(face, event)
  // The minutes follow the hours automatically, which is the whole of the "two
  // stages" requirement: releasing the hour hand is the visitor saying "that
  // one". Releasing on the minutes advances nothing — the value is complete and
  // *Set the time* is what states it, so a stray drag cannot commit a period.
  if (clockStage(state.periodClock) === 'hours') setClockStage('minutes')
  return true
}

/**
 * One pointer position, read as an hour or a minute.
 *
 * The centre is measured from the element's own client rectangle on every event
 * rather than cached, because the dialog is centred in the viewport and a
 * phone's on-screen keyboard appearing mid-drag moves it. **Client coordinates
 * are absolute**, so this arithmetic is the same under `dir="rtl"` as under
 * `ltr` — which is the pointer half of the deliberate non-mirroring written out
 * over `faceContents`.
 */
function trackClock(face, event) {
  const clock = state.periodClock
  if (!clock) return
  const box = face.getBoundingClientRect()
  const dx = event.clientX - (box.x + box.width / 2)
  const dy = event.clientY - (box.y + box.height / 2)
  if (clockStage(clock) === 'hours') clock.hours = hourFromPointer(dx, dy, box.width / 2)
  else clock.minutes = minuteFromPointer(dx, dy)
  redrawClock()
}

/**
 * A keystroke in one of the keyboard mode's two number boxes.
 *
 * **No `setState`**, for the reason the four text boxes outside have: a
 * re-render per keystroke replaces `main.innerHTML` and destroys the caret. The
 * value is written straight onto `state.periodClock` and the live region is
 * patched by hand.
 *
 * The value is **clamped into range here and rewritten in the box on blur**,
 * rather than refused as it is typed. A box that refused the `9` of a `19` about
 * to be typed is a box that cannot be typed into at all; `max="23"` states the
 * bound to the spinner and to assistive technology, this keeps `state` inside it
 * whatever arrives, and `handlePeriodBlur` makes the two agree once the caret
 * leaves.
 *
 * @returns {boolean} whether this module owned the event.
 */
function handleClockInput(event) {
  const key = event.target?.dataset?.periodClockField
  if (!key || !state.periodClock) return false
  const limit = key === 'hours' ? 23 : 59
  const typed = Number(event.target.value)
  // An empty box is a value being retyped, not a zero. Leaving `state` alone
  // until something parses is what lets somebody select-all and type `16`
  // without the hand jumping to midnight on the way.
  if (event.target.value === '' || !Number.isFinite(typed)) return true
  state.periodClock[key] = Math.max(0, Math.min(limit, Math.trunc(typed)))
  announceClock()
  return true
}

/** The caret has left a number box: show the value `state` actually holds, so a
 *  typed `99` reads back as the `23` that was recorded. See `handleClockInput`. */
function handleClockBlur(event) {
  const key = event.target?.dataset?.periodClockField
  if (!key || !state.periodClock) return false
  event.target.value = String(state.periodClock[key])
  return true
}

/**
 * `Esc` and `Tab` inside the clock.
 *
 * **`Esc` closes, and it does not nest the way the calendar's does.** That is a
 * considered difference rather than an oversight: the calendar's three views are
 * three separate questions and `Esc` from a year grid returns to the month the
 * visitor had navigated to, which would otherwise be lost. The clock's two
 * stages are one question — an hour and a minute of the same time — the readout
 * offers a permanent, visible way back between them, and nothing is written
 * until *Set the time*. So `Esc` here means "not this time", once.
 */
function handleClockKeydown(event) {
  if (!state.periodClock) return false
  const dialog = document.getElementById('period-clock-dialog')
  if (!dialog || !dialog.contains(event.target)) return false
  if (event.key === 'Escape') {
    event.preventDefault()
    closeClock()
    return true
  }
  if (event.key === 'Tab') return trapTab(event, 'period-clock-dialog')
  return false
}

/** Every click the clock owns, answered from `handlePeriodClick`. */
function handleClockClick(action, control, event) {
  if (action === 'period-clock-open') {
    openClock(control.dataset.bound, control.id, openClockMode(event))
    return true
  }
  if (!state.periodClock) return false
  // A click on the backdrop dismisses and a click *inside* the dialog must not.
  // The identity test is `handlePeriodClick`'s own, for the same reason: the
  // dialog is a child of the backdrop, so `closest('[data-action]')` from
  // anything inside it finds the backdrop.
  if (action === 'period-clock-dismiss' && event?.target !== control) return false
  if (action === 'period-clock-close' || action === 'period-clock-dismiss') {
    closeClock()
    return true
  }
  if (action === 'period-clock-stage') {
    setClockStage(control.dataset.stage === 'minutes' ? 'minutes' : 'hours')
    return true
  }
  if (action === 'period-clock-mode') {
    setClockMode(state.periodClock.mode === 'keyboard' ? 'dial' : 'keyboard')
    return true
  }
  if (action === 'period-clock-set') {
    setClockTime()
    return true
  }
  return false
}
