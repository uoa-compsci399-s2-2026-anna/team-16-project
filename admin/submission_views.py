"""`/admin/submissions` — contract §8.2's record-level moderation screen.

**Three columns, and two of them are not columns.** §8.2 asks for a list "with
search and filtering" that "allows setting ``excluded_from_public`` with a
reason". What a staff member needs in order to decide *whether* to exclude a row
is what the calculation was about, and `submission` records almost none of it
directly: the sectors live on `submission_entry` and the quantities on
`submission_line`. So the sector summary and the recorded mass below are derived
per row, and `list_query` eager-loads the two collections they walk — without
that, one page of fifty rows is fifty-one queries and then another hundred for
the sectors.

## What this screen cannot show, and why that is not a gap to be closed

There is **no IP address, no user agent and no browser fingerprint** on any of
these tables, because none was ever collected (§2.3, and `Submission`'s own
docstring). A usage list keyed on "who" is not buildable here and is not
supposed to be. `submission.token` is the only field that ever tied a row to a
browser session, it is nulled an hour later by `expire_tokens`, and it appears
on no page this module serves.

**That last point is load-bearing rather than tidy.** Contract v1.5 added
`token` to `REDACTED_FIELDS` explicitly *ahead of* this screen, on the reasoning
that the moment a submission view is built on `AuditedModelView`, `write_audit`
snapshots the row and copies a live session token into `audit_log` — where it
never expires and `expire_tokens` cannot reach it. This module is that screen
arriving. The redaction is why building it is safe; `column_details_list` below
narrows the drill-down for the same reason, in the second place.

## Read-mostly, and the one write it allows

`can_create` and `can_delete` are both off. A submission is a record of
something a member of the public did: inventing one corrupts the statistics with
a number nobody entered, and deleting one destroys evidence of a calculation
that was genuinely run. Moderation is what §8.2 asks for, and moderation is
`excluded_from_public` — the row stays, the aggregate stops counting it.

`can_edit` is off too, and the exclusion goes through its own route instead. A
generic edit form over this model would offer `gwp_horizon`, `factor_set_id` and
`created_at` as editable fields, which would let a staff member silently rewrite
what a visitor actually submitted; and it has nowhere to put the *reason* §8.2
requires. The two actions below take a reason, apply exactly one field, and
write their own audit entry.

**Auditing is explicit here, as it is in `admin/blocklist_views.py` and
`admin/accounts_view.py`.** `AuditedModelView`'s before-commit listener only
fires for writes that went through `insert_model`/`update_model`/`delete_model`
(see `admin/modelviews.py`'s class docstring, point 1), and neither route below
is any of those three. Each calls `write_audit` itself, and the payloads are
hand-built from the one field that changed plus the reason — not
`row_to_dict`, which would serialise `token` and rely on the redaction rather
than on never having read it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from markupsafe import Markup, escape
from sqladmin import action, expose
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload
from starlette.responses import RedirectResponse
from starlette.responses import Response as StarletteResponse

from admin.audit import write_audit
from admin.auth import SESSION_KEY
from admin.modelviews import AuditedModelView
from db.models import Scenario, Sector, Submission, SubmissionEntry, SubmissionLine, utcnow

#: The panel's own grouping. Submissions are neither taxonomy nor factors: they
#: are the record of what the public did with both.
_CATEGORY = "Submissions"

#: How many sectors a row names before it stops listing them. Four short sector
#: names already run past the column on a 938px laptop, which is the width this
#: panel is developed against.
_SECTOR_LIMIT = 2


def _sector_summary(submission: Submission) -> str:
    """The sectors this calculation covered, in entry order.

    A submission is one or more `(sector, food_category)` entries, because a
    food business has waste at more than one point in the supply chain (§2.3).
    One sector is the common case and prints as itself; more print as the first
    two and a count, so the column has a fixed maximum width.

    Order is `submission_entry.sort_order` — the order the visitor entered
    them, which `Submission.entries` already applies — rather than alphabetical,
    so the summary reads the way the calculation was built.
    """
    names: list[str] = []
    for entry in submission.entries:
        name = entry.sector.name if entry.sector is not None else str(entry.sector_id)
        if name not in names:
            names.append(name)
    if not names:
        return "—"
    if len(names) <= _SECTOR_LIMIT:
        return ", ".join(names)
    return f"{', '.join(names[:_SECTOR_LIMIT])} +{len(names) - _SECTOR_LIMIT}"


def _current_total_kg(submission: Submission) -> Decimal:
    """Mass recorded in the **current** scenario, across every entry.

    Current, not both, and not the alternative. Every calculation is a pair
    (§4.1) and the two scenarios move the same mass by construction, so adding
    them would double every figure on this page; and the alternative is a
    hypothetical the visitor was asked to imagine, not something they reported.
    The current scenario is the answer to "how much food waste does this
    business actually have", which is the number a staff member is judging when
    they decide whether a row is plausible.

    `Decimal`, never float — §1.2, and these are `DECIMAL(16,3)` columns.
    """
    total = Decimal("0")
    for entry in submission.entries:
        for line in entry.lines:
            if line.scenario is Scenario.current:
                total += line.qty_kg
    return total


#: The scalar subquery behind both the "food waste recorded" column's filter
#: and nothing else. Written once, here, because a second copy of a correlated
#: subquery is a second chance to forget `scenario == current` — which would
#: silently double every figure it filters on, in the same way adding the two
#: scenarios would (see `_current_total_kg`).
#:
#: A scalar subquery rather than a join with GROUP BY/HAVING, deliberately: a
#: grouped statement changes the shape of what sqladmin then wraps in
#: `select(count()).select_from(stmt.subquery())` for the pagination count, and
#: a filter that quietly disagreed with its own row count is the failure this
#: screen can least afford — a moderator would be told there are 40 rows and
#: shown 12.
def _recorded_mass_subquery():
    return (
        select(func.coalesce(func.sum(SubmissionLine.qty_kg), 0))
        .select_from(SubmissionLine)
        .join(SubmissionEntry, SubmissionLine.submission_entry_id == SubmissionEntry.id)
        .where(
            SubmissionEntry.submission_id == Submission.id,
            SubmissionLine.scenario == Scenario.current,
        )
        .correlate(Submission)
        .scalar_subquery()
    )


#: The largest real UTC offset is +14:00 (Kiritimati) and the smallest -12:00,
#: so anything outside this is either a typo or someone editing the query
#: string. Clamped rather than refused: the cost of a silly offset is a window
#: shifted by hours, and the cost of refusing is a staff member staring at an
#: error they cannot act on.
_MAX_TZ_OFFSET_MINUTES = 14 * 60


def _parse_iso_date(value: str | None) -> date | None:
    """`YYYY-MM-DD` from an `<input type="date">`, or None.

    Anything unparseable is None rather than an error. The input element only
    ever submits that form, so a bad value means a hand-edited query string,
    and the useful response to that is the unfiltered table rather than a
    stack trace.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _tz_offset_minutes(value: str | None) -> int:
    """`Date.prototype.getTimezoneOffset()` as the browser reports it.

    **Positive west of UTC.** JavaScript returns UTC-minus-local in minutes, so
    Auckland in NZST is `-720` and Shanghai is `-480`. That sign convention is
    the opposite of the one people say out loud ("UTC+12"), which is exactly
    why it is converted in one place with the rule written down rather than
    inline at the two call sites.

    Absent or unparseable means zero, which makes the typed dates UTC dates —
    the honest fallback when the page has no script running to tell us
    otherwise, and what the form's own hint says will happen.
    """
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return 0
    return max(-_MAX_TZ_OFFSET_MINUTES, min(_MAX_TZ_OFFSET_MINUTES, minutes))


def _local_day_to_utc(day: date, offset_minutes: int) -> datetime:
    """Midnight at the start of `day`, in the reader's zone, as naive UTC.

    local = UTC - offset  =>  UTC = local + offset. Auckland's `-720` turns
    local 2026-08-21 00:00 into 2026-08-20 12:00 UTC, which is right: New
    Zealand midnight is noon UTC the day before.

    Naive on the way out, because `submission.created_at` is naive UTC
    (contract §1.3) and SQLAlchemy will not compare an aware value against it.
    """
    return datetime(day.year, day.month, day.day) + timedelta(minutes=offset_minutes)


#: **The bar is built here rather than through sqladmin's filter protocol, and
#: multi-select is why.** That protocol hands `get_filtered_query` a single
#: value read with `query_params.get(name)`; it cannot see a second one. Two of
#: these filters take several values at once, so all of them moved here rather
#: than leaving the screen with two filtering mechanisms that render
#: differently and fail differently.
#:
#: What that also bought: `column_filters` is now empty, so sqladmin renders no
#: sidebar at all, and the CSS that used to hide one is gone. A hack removed by
#: making it unnecessary is better than a hack with a comment.

#: Rolling windows, not calendar days. "Today" is a question about the reader's
#: zone against a server that stores naive UTC: a staff member in Auckland
#: opening the panel at 9am is at 21:00 UTC *yesterday*, so a UTC "today" shows
#: them an empty screen, and the reasonable conclusion from an empty screen is
#: that nobody used the calculator. A rolling window is the same everywhere,
#: and it is the question a moderator actually has - not "what happened on
#: Tuesday" but "what has arrived since I last looked".
#:
#: The explicit from/to range below answers the calendar question, and pays the
#: time-zone cost with `tzoffset`.
WINDOWS: dict[str, tuple[str, timedelta]] = {
    "24h": ("Last 24 hours", timedelta(hours=24)),
    "7d": ("Last 7 days", timedelta(days=7)),
    "30d": ("Last 30 days", timedelta(days=30)),
    "12mo": ("Last 12 months", timedelta(days=365)),
}

#: value -> (label, lower inclusive, upper exclusive); `None` is unbounded.
#:
#: **Half-open, and that is what makes ticking two of them mean what it looks
#: like it means.** Written `<=` on both sides, a row of exactly 1,000 kg is in
#: two bands at once - so the counts sum to more than the table holds, and
#: ticking "100 kg - 1 tonne" and "1 - 10 tonnes" returns that row twice over
#: in the arithmetic even though it is drawn once. In a tool where people type
#: `1000`, that is a lot of rows.
BANDS: dict[str, tuple[str, Decimal | None, Decimal | None]] = {
    "lt100": ("Under 100 kg", None, Decimal("100")),
    "100-1k": ("100 kg \u2013 1 tonne", Decimal("100"), Decimal("1000")),
    "1k-10k": ("1 \u2013 10 tonnes", Decimal("1000"), Decimal("10000")),
    "gte10k": ("10 tonnes and over", Decimal("10000"), None),
}

EXCLUSION_CHOICES: list[tuple[str, str]] = [
    ("true", "Excluded only"),
    ("false", "Included only"),
]

HORIZON_CHOICES: list[tuple[str, str]] = [("20", "20 years"), ("100", "100 years")]


def _stage_clause(codes: list[str]):
    """Submissions with an entry at **any** of the named stages.

    `EXISTS`, not a join, and one `EXISTS` rather than one per code. A join on
    `submission_entry` returns a submission once per matching entry -
    `uq_submission_entry` is UNIQUE on (submission, sector, food_category), so
    one business reporting bakery and dairy waste at Processing is two rows
    with one sector. sqladmin calls `.scalars().unique()` and would draw that
    submission once, but the pagination count is built from
    `select(count()).select_from(stmt.subquery())` and counts the duplicate:
    the page then reports three submissions and renders two, with nothing on
    screen to say which number is wrong.

    `IN` inside the one `EXISTS` gives OR across the ticked stages while still
    asking a yes/no question per submission.
    """
    return (
        select(1)
        .select_from(SubmissionEntry)
        .join(Sector, SubmissionEntry.sector_id == Sector.id)
        .where(
            SubmissionEntry.submission_id == Submission.id,
            Sector.code.in_(codes),
        )
        .correlate(Submission)
        .exists()
    )


def _mass_clause(band_values: list[str]):
    """The recorded total falling in **any** of the ticked bands.

    Returns None when nothing recognisable was ticked, so the caller can tell
    "no filter" from "a filter that matches nothing" - the difference between
    showing every row and showing none, on a hand-edited query string.
    """
    total = _recorded_mass_subquery()
    clauses = []
    for value in band_values:
        band = BANDS.get(value)
        if band is None:
            continue
        _, lower, upper = band
        bounds = []
        if lower is not None:
            bounds.append(total >= lower)
        if upper is not None:
            bounds.append(total < upper)
        clauses.append(and_(*bounds) if len(bounds) > 1 else bounds[0])
    if not clauses:
        return None
    return or_(*clauses) if len(clauses) > 1 else clauses[0]


def _utc_time_element(moment, pattern: str) -> Markup:
    """A `<time>` whose text is UTC and whose `datetime` says so.

    **The `Z` is the whole point of this function.** `created_at` is stored
    naive (`admin/models.py::utcnow` strips the tzinfo, contract §1.3), so
    `isoformat()` yields `2026-08-16T05:28:18` with no offset — and ECMA-262
    parses a date-time form without one as LOCAL time. `new Date()` in Auckland
    would then read a UTC instant as if it were already local and display it
    thirteen hours out, in a direction that looks entirely plausible. Appending
    `Z` here, once, is what makes `admin/static/localtime.js` correct rather
    than confidently wrong.

    The visible text stays UTC and says so, because that is what the reader
    sees if the script does not run — and it is what `audit_log` will quote
    back at them.
    """
    return Markup('<time datetime="{}">{} UTC</time>').format(
        moment.isoformat(timespec="seconds") + "Z", moment.strftime(pattern)
    )


def _list_created_at(submission: Submission, _name) -> str:
    """The timestamp, carrying the excluded state with it.

    **A marker in this cell rather than a fourth column.** A staff member
    scanning the list has to be able to tell which rows have already been dealt
    with, and a boolean column to say so costs width on a table that is three
    columns by design. Module level, not a method: sqladmin calls a formatter as
    `formatter(model, name)`, so a method here would bind `self` to the row and
    the row to the column name.
    """
    if submission.created_at is None:
        return "—"
    marker = " · excluded" if submission.excluded_from_public else ""
    return Markup("{}{}").format(_utc_time_element(submission.created_at, "%Y-%m-%d %H:%M"), marker)


class SubmissionAdmin(AuditedModelView, model=Submission):
    name = "Submission"
    name_plural = "Submissions"
    category = _CATEGORY
    icon = "fa-solid fa-list-check"

    #: See the module docstring. A submission is a record of something a member
    #: of the public did; it is not staff-authored content.
    can_create = False
    can_delete = False
    can_edit = False
    can_view_details = True

    #: Three columns, and the two derived ones are rendered by the formatters
    #: below. `excluded_from_public` is deliberately **not** a fourth: the state
    #: is shown on the row itself (see `_format_created_at`), which keeps the
    #: table narrow enough to read on a laptop while still making it impossible
    #: to miss that a row has been excluded.
    column_list = ["created_at", "sector_summary", "current_total_kg"]

    #: **Narrowed on purpose, and `token` is why.** `column_details_list`
    #: defaults to every mapped column (`admin/modelviews.py`, point 2), which
    #: here would print the live de-duplication token on a page one click from
    #: every row — the exact leak contract v1.5 redacted `token` in the audit
    #: trail to prevent, arriving by the other door. `token_expires_at` goes
    #: with it: it is metadata about a value this page does not show.
    column_details_list = [
        "created_at",
        "sector_summary",
        "current_total_kg",
        "gwp_horizon",
        "factor_set",
        "excluded_from_public",
    ]

    column_labels = {
        "created_at": "Calculated at",
        "sector_summary": "Supply-chain stage",
        "current_total_kg": "Food waste recorded",
        "gwp_horizon": "Methane time horizon",
        "factor_set": "Factor set",
        "excluded_from_public": "Excluded from public statistics",
    }

    #: **Only real columns.** sqladmin sorts in SQL, so naming a derived
    #: attribute here produces a header that looks sortable and then fails on
    #: the click. `sector_summary` and `current_total_kg` are computed in Python
    #: from two child tables and are not sortable at any price worth paying.
    column_sortable_list = ["created_at", "gwp_horizon", "excluded_from_public"]

    #: **Empty, and deliberately.** Every control on this screen is built by
    #: `filter_controls` and applied in `list_query`, because two of them take
    #: several values at once and sqladmin's protocol reads one
    #: (`query_params.get`). Leaving one or two here would give the page two
    #: filtering mechanisms with different markup and different failure modes,
    #: and would put sqladmin's sidebar back on the right of the table.
    #:
    #: §8.2 also asks for "search". There is no free text on this model - no
    #: name, no note, nothing a person typed - so a search box would have
    #: nothing to match, and is left off rather than shipped as an input that
    #: silently finds nothing.
    column_filters = []

    column_default_sort = ("created_at", True)

    list_template = "brand/submission_list.html"
    details_template = "brand/submission_details.html"

    async def filter_controls(self, request):
        """What the bar renders, in the order it renders it.

        One structure, read by the template and matched by `list_query` below,
        so a control cannot appear on the page without a clause behind it or a
        clause exist with no way to reach it.

        The stage options come from the `sector` table rather than a literal
        list, because §2.1's taxonomy is data: a sector staff add through the
        panel has to appear here without anyone editing this file. Inactive
        sectors are included on purpose - a submission recorded against one is
        still in the table, and a filter that could not name it would leave
        those rows unreachable.
        """
        with self.session_maker() as session:
            sectors = session.execute(
                select(Sector.code, Sector.name).order_by(Sector.sort_order, Sector.name)
            ).all()

        selected = lambda name: request.query_params.getlist(name)  # noqa: E731
        return [
            {
                "name": "window",
                "title": "Calculated in the",
                "multiple": False,
                "empty_label": "Any time",
                "options": [(value, label) for value, (label, _) in WINDOWS.items()],
                "selected": selected("window"),
            },
            {
                "name": "stage",
                "title": "Supply-chain stage",
                "multiple": True,
                "empty_label": "Any stage",
                "options": [(code, name) for code, name in sectors],
                "selected": selected("stage"),
            },
            {
                "name": "mass",
                "title": "Food waste recorded",
                "multiple": True,
                "empty_label": "Any amount",
                "options": [(value, label) for value, (label, _, _) in BANDS.items()],
                "selected": selected("mass"),
            },
            {
                "name": "excluded",
                "title": "Excluded",
                "multiple": False,
                "empty_label": "Included and excluded",
                "options": EXCLUSION_CHOICES,
                "selected": selected("excluded"),
            },
            {
                "name": "horizon",
                "title": "Methane horizon",
                "multiple": False,
                "empty_label": "Either horizon",
                "options": HORIZON_CHOICES,
                "selected": selected("horizon"),
            },
        ]

    def list_query(self, request):
        """Eager-load what the derived columns walk, then apply the bar.

        **The eager loading.** Without it the list page is N+1 twice over: once
        for `entries` and again for each entry's `lines` and `sector`. Fifty
        rows is 151 queries, and the page still renders - slowly, and only on a
        database small enough not to notice.

        **Every filter is applied here, and that is what keeps the count
        honest.** sqladmin builds its total from
        `select(count()).select_from(stmt.subquery())` over whatever this
        returns, so a clause added here is counted exactly as it is rendered.
        The alternative - a join with GROUP BY - changes the statement's shape
        and can report a number the page does not draw.

        Filters intersect: several controls narrow together, and several values
        inside one control widen it. That is what a filter bar means everywhere
        else, and the form's hint says so rather than leaving it to be
        discovered.
        """
        params = request.query_params
        query = super().list_query(request).options(
            selectinload(Submission.entries).selectinload(SubmissionEntry.lines),
            selectinload(Submission.entries).selectinload(SubmissionEntry.sector),
        )

        window = WINDOWS.get(params.get("window", ""))
        if window is not None:
            #: `utcnow()` is naive UTC and so is `created_at` (§1.3), so these
            #: compare directly. An aware value here would raise at query build
            #: time rather than compare wrong, which is the one mercy of naive
            #: datetimes.
            query = query.where(Submission.created_at >= utcnow() - window[1])

        offset = _tz_offset_minutes(params.get("tzoffset"))
        start = _parse_iso_date(params.get("from"))
        end = _parse_iso_date(params.get("to"))
        if start is not None:
            query = query.where(Submission.created_at >= _local_day_to_utc(start, offset))
        if end is not None:
            #: The *end* of the chosen day. A staff member picking 21 August to
            #: 21 August means that whole day; taking the date at face value
            #: makes `from == to` return nothing at all, which reads as "there
            #: were no calculations" rather than as a bad query.
            query = query.where(
                Submission.created_at < _local_day_to_utc(end + timedelta(days=1), offset)
            )

        stages = [code for code in params.getlist("stage") if code]
        if stages:
            query = query.where(_stage_clause(stages))

        mass = _mass_clause([value for value in params.getlist("mass") if value])
        if mass is not None:
            query = query.where(mass)

        excluded = params.get("excluded")
        if excluded in ("true", "false"):
            query = query.where(Submission.excluded_from_public.is_(excluded == "true"))

        horizon = params.get("horizon")
        if horizon in ("20", "100"):
            query = query.where(Submission.gwp_horizon == int(horizon))

        return query

    def details_query(self, request):
        """The drill-down walks one level deeper than the list: every line's
        destination and every entry's food category are rendered by name."""
        return super().details_query(request).options(
            selectinload(Submission.entries).selectinload(SubmissionEntry.sector),
            selectinload(Submission.entries).selectinload(SubmissionEntry.food_category),
            selectinload(Submission.entries)
            .selectinload(SubmissionEntry.lines)
            .selectinload(SubmissionLine.destination),
        )

    column_formatters = {
        "created_at": _list_created_at,
        "sector_summary": lambda model, _name: _sector_summary(model),
        "current_total_kg": lambda model, _name: f"{_current_total_kg(model):,.3f} kg",
    }
    column_formatters_detail = {
        "created_at": lambda model, _name: (
            _utc_time_element(model.created_at, "%Y-%m-%d %H:%M:%S")
            if model.created_at
            else "—"
        ),
        "sector_summary": lambda model, _name: _sector_summary(model),
        "current_total_kg": lambda model, _name: f"{_current_total_kg(model):,.3f} kg",
    }

    # ---------------------------------------------------------------- routes

    def _list_url(self, request):
        return request.url_for("admin:list", identity=self.identity)

    def _selected_ids(self, request) -> list[int]:
        """Every id named in `pks`, dropping anything that does not parse.

        The same reasoning as `admin/blocklist_views.py`'s copy: only a
        hand-typed query string can put a non-integer here, and refusing the
        whole selection over one bad entry would cost a staff member every row
        they meant to act on.
        """
        ids: list[int] = []
        for pk in request.query_params.get("pks", "").split(","):
            pk = pk.strip()
            if not pk:
                continue
            try:
                ids.append(int(pk))
            except ValueError:
                continue
        return ids

    @action(
        name="exclude",
        label="Exclude from public statistics",
        add_in_detail=True,
        add_in_list=True,
    )
    async def exclude_action(self, request) -> RedirectResponse:
        return RedirectResponse(
            request.url_for("admin:view-submissions-moderate").include_query_params(
                pks=request.query_params.get("pks", ""), exclude="1"
            ),
            status_code=302,
        )

    @action(
        name="include",
        label="Return to public statistics",
        add_in_detail=True,
        add_in_list=True,
    )
    async def include_action(self, request) -> RedirectResponse:
        return RedirectResponse(
            request.url_for("admin:view-submissions-moderate").include_query_params(
                pks=request.query_params.get("pks", ""), exclude="0"
            ),
            status_code=302,
        )

    @expose("/moderate", methods=["GET", "POST"])
    async def moderate(self, request) -> StarletteResponse:
        """§8.2's "with a reason", which is why this is a form and not an
        `@action` confirmation dialog.

        sqladmin's `confirmation_message` is a yes/no prompt with nowhere to
        type, so an action alone could set the flag but could not record why —
        and the reason is the whole value of the audit entry. Six months on,
        "excluded_from_public went true" tells the next person nothing;
        "obvious test data, 5,000,000 kg from a household" tells them
        everything.

        No CSRF token, matching every other state-changing route on this panel;
        see `admin/blocklist_views.py`'s `block_form` for the note on that being
        a pre-existing sqladmin pattern here rather than something this route
        introduces.
        """
        excluding = request.query_params.get("exclude", "1") != "0"
        ids = self._selected_ids(request)
        context = {
            "excluding": excluding,
            "error": None,
            "list_url": self._list_url(request),
            "pks": ",".join(str(i) for i in ids),
        }

        with self.session_maker() as session:
            rows = (
                session.scalars(
                    select(Submission)
                    .where(Submission.id.in_(ids))
                    .options(
                        selectinload(Submission.entries).selectinload(
                            SubmissionEntry.lines
                        ),
                        selectinload(Submission.entries).selectinload(
                            SubmissionEntry.sector
                        ),
                    )
                    .order_by(Submission.created_at.desc())
                ).all()
                if ids
                else []
            )
            context["rows"] = [
                {
                    "id": row.id,
                    "created_at": (
                        row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "—"
                    ),
                    "sectors": _sector_summary(row),
                    "total": f"{_current_total_kg(row):,.3f} kg",
                    "already": row.excluded_from_public,
                }
                for row in rows
            ]

            if request.method == "GET":
                return await self.templates.TemplateResponse(
                    request, "brand/submission_moderate.html", context
                )

            form = await request.form()
            reason = str(form.get("reason", "")).strip()
            if not rows:
                context["error"] = "No submissions were selected."
                return await self.templates.TemplateResponse(
                    request, "brand/submission_moderate.html", context, status_code=400
                )
            if not reason:
                #: Refused rather than defaulted. A blank reason produces an
                #: audit row that satisfies §8.2's letter and answers nobody's
                #: question later, which is worse than the moderation not
                #: happening — the row is still there to try again.
                context["error"] = "Give a reason. It is what the audit entry is for."
                return await self.templates.TemplateResponse(
                    request, "brand/submission_moderate.html", context, status_code=400
                )

            actor = request.session.get(SESSION_KEY, "unknown")
            changed = 0
            for row in rows:
                if row.excluded_from_public == excluding:
                    #: Already in the requested state. Skipped rather than
                    #: re-written, so the audit trail does not fill with
                    #: entries in which nothing changed.
                    continue
                before = {"excluded_from_public": row.excluded_from_public}
                row.excluded_from_public = excluding
                write_audit(
                    session,
                    actor,
                    "exclude_submission" if excluding else "include_submission",
                    "submission",
                    row.id,
                    before,
                    {"excluded_from_public": excluding, "reason": reason},
                )
                changed += 1
            session.commit()

        return RedirectResponse(self._list_url(request), status_code=302)


#: **Set here, after the class, because the metaclass overwrites it.**
#: `ModelViewMeta.__new__` assigns `cls.identity = slugify_class_name(...)`
#: unconditionally, so an `identity` in the class body above is computed and
#: then thrown away — silently, which is what makes it worth a note. Contract
#: §8.2 names this screen `/admin/submissions`, and every other URL in that
#: table happens to match its slugified model name (`AuditLog` -> `audit-log`);
#: this is the one that does not, because `Submission` slugifies to the
#: singular. Assigning after class creation is what makes the contract's URL
#: the real one: `add_view` reads this attribute when it registers the routes,
#: which happens later still.
SubmissionAdmin.identity = "submissions"
