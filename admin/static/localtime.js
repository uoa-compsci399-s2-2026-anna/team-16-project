/* Timestamps in the reader's own time zone, on /admin/submissions.
 *
 * WHY THE BROWSER AND NOT THE SERVER. This panel is server-rendered, and the
 * server cannot know what zone the reader is in. It could be told one — a
 * `KAICALC_DISPLAY_TIMEZONE` setting — but that is a single value for a panel
 * whose users are not in a single place: the client is in New Zealand and this
 * team is not. The browser already knows, exactly, for each reader.
 *
 * SO THE SERVER STILL SENDS UTC, and this is an upgrade over a page that is
 * already correct. Every timestamp is rendered as
 *
 *     <time datetime="2026-08-16T05:28:18Z">2026-08-16 05:28 UTC</time>
 *
 * With this file absent, blocked, or broken, the reader sees UTC and the
 * suffix that says so. Nothing here is load-bearing; it replaces a true
 * sentence with a more useful one.
 *
 * THE TRAP, and it is a silent one. `submission.created_at` is stored NAIVE
 * (admin/models.py::utcnow strips the tzinfo, contract §1.3), so the value
 * Python hands a template has no zone on it. Written into `datetime=` as
 * `2026-08-16T05:28:18`, `new Date()` parses it as LOCAL time by the
 * ECMA-262 rule for date-time forms without an offset — and the result is
 * wrong by exactly the reader's own offset, in the direction that looks
 * plausible. Thirteen hours out in Auckland; eight in Shanghai. The `Z` is
 * appended server-side, in the formatter that builds this attribute, and
 * `tests/admin/test_submission_views.py` asserts it is there.
 *
 * NO LIBRARY. `Intl.DateTimeFormat` has done this in every browser this panel
 * supports for a decade, and §7.6 rule 7 forbids a runtime asset from a
 * third-party host anyway.
 */

const LOCALE_ATTRIBUTE = "data-locale";

/* `dateStyle`/`timeStyle` rather than a hand-built pattern: the order of the
 * parts, the separator and the 12/24-hour choice are all locale decisions, and
 * a template string here would impose one locale's answer on every reader. */
function formatterFor(locale) {
  try {
    return new Intl.DateTimeFormat(locale || undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    /* An unusable locale tag is not worth failing over — the page's own
     * language negotiation may have produced something Intl does not know.
     * Falling back to the browser's default still beats UTC. */
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  }
}

function localise(root) {
  const locale = document.documentElement.getAttribute(LOCALE_ATTRIBUTE)
    || document.documentElement.lang
    || undefined;
  const format = formatterFor(locale);

  for (const element of root.querySelectorAll("time[datetime]")) {
    const parsed = new Date(element.getAttribute("datetime"));
    /* An unparseable value leaves the server's own text alone. A cell reading
     * "Invalid Date" is strictly worse than one reading the UTC instant. */
    if (Number.isNaN(parsed.getTime())) continue;

    /* The UTC form moves to the tooltip rather than being discarded: it is
     * what the database holds and what an audit entry will say, so a staff
     * member comparing the two needs it to still be reachable. */
    if (!element.title) element.title = element.textContent.trim();
    element.textContent = format.format(parsed);
  }
}

localise(document);

/* The other half: tell the server which zone the reader typed their dates in.
 *
 * `/admin/submissions` has a from/to date range, and a calendar date is a
 * question about a zone. Left to itself the server reads those dates as UTC
 * dates, which for a New Zealand reader shifts the window by twelve hours in
 * the direction that quietly drops the most recent half-day — the half they
 * were most likely looking for.
 *
 * `getTimezoneOffset()` is UTC-minus-local in minutes and so is POSITIVE west
 * of UTC: Auckland in NZST is -720. `_tz_offset_minutes` in
 * admin/submission_views.py carries the same note, because the sign is the
 * opposite of the one people say out loud and each side has to get it right
 * independently.
 *
 * The field is filled on load rather than on submit: it has to be right for
 * the bookmarked URL a staff member shares as much as for the click that made
 * it. With this file absent the field stays empty and the server reads UTC,
 * which the form's own hint states.
 */
const offsetField = document.getElementById("kc-tzoffset");
if (offsetField && !offsetField.value) {
  offsetField.value = String(new Date().getTimezoneOffset());
}
