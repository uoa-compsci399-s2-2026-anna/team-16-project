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

from datetime import timedelta
from decimal import Decimal

from markupsafe import Markup, escape
from sqladmin import action, expose
from sqladmin.filters import BooleanFilter, StaticValuesFilter
from sqlalchemy import func, select
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


class RecentWindowFilter:
    """"Calculated in the last ..." — rolling windows, not calendar days.

    **Calendar days would be wrong and would look right.** "Today" is a
    question about the reader's own time zone, and this filter runs on a server
    that stores naive UTC. A staff member in Auckland opening the panel at 9am
    on the 21st is at 21:00 UTC on the *20th*; a UTC "today" would show them an
    empty screen and they would conclude the calculator had had no visitors.

    A rolling window has no such ambiguity — "the last 24 hours" is the same
    24 hours everywhere — which is why the options below are durations rather
    than dates. It is also the question a moderator actually has: not "what
    happened on Tuesday" but "what has arrived since I last looked".
    """

    has_operator = False
    template = "sqladmin/filters/lookup_filter.html"

    #: value -> (label, timedelta). Ordered shortest first, because the short
    #: windows are the ones used repeatedly.
    WINDOWS = {
        "24h": ("Last 24 hours", timedelta(hours=24)),
        "7d": ("Last 7 days", timedelta(days=7)),
        "30d": ("Last 30 days", timedelta(days=30)),
        "12mo": ("Last 12 months", timedelta(days=365)),
    }

    def __init__(self, title="Calculated in the", parameter_name="window"):
        self.title = title
        self.parameter_name = parameter_name

    async def lookups(self, request, model, run_query):
        return [("__all", "Any time")] + [
            (value, label) for value, (label, _) in self.WINDOWS.items()
        ]

    async def get_filtered_query(self, query, value, model):
        window = self.WINDOWS.get(value)
        if window is None:
            return query
        #: `utcnow()` is naive UTC and so is `created_at` (contract §1.3), so
        #: these compare directly. Mixing an aware value in here would raise
        #: at query build time rather than quietly comparing wrong, which is
        #: the one mercy of naive datetimes.
        return query.where(Submission.created_at >= utcnow() - window[1])


class SupplyChainStageFilter:
    """Submissions with **any** entry at the named stage.

    `EXISTS`, not a join. A submission has one entry per supply-chain stage,
    so a business reporting waste at three stages joins to three rows — and a
    join would return that submission three times, which sqladmin would then
    render as three identical lines and count as three. `EXISTS` asks the
    question the filter is actually asking: does this calculation touch that
    stage at all.

    The options come from the `sector` table rather than from a literal list,
    because §2.1's taxonomy is data: a sector staff add through the panel has
    to appear here without anyone editing this file.
    """

    has_operator = False
    template = "sqladmin/filters/lookup_filter.html"

    def __init__(self, title="Supply-chain stage", parameter_name="stage"):
        self.title = title
        self.parameter_name = parameter_name

    async def lookups(self, request, model, run_query):
        rows = await run_query(
            select(Sector.code, Sector.name).order_by(Sector.sort_order, Sector.name)
        )
        return [("__all", "Any stage")] + [(row[0], row[1]) for row in rows]

    async def get_filtered_query(self, query, value, model):
        if value in ("", "__all", None):
            return query
        return query.where(
            select(1)
            .select_from(SubmissionEntry)
            .join(Sector, SubmissionEntry.sector_id == Sector.id)
            .where(
                SubmissionEntry.submission_id == Submission.id,
                Sector.code == value,
            )
            .correlate(Submission)
            .exists()
        )


class RecordedMassFilter:
    """Bands of recorded food waste, over the same total the column shows.

    **Bands rather than a free numeric range**, and the reason is that this
    filter exists to find implausible rows. The question is "show me the very
    large ones" rather than "show me between 1,240 and 1,260 kg", and a pair of
    free number boxes would need validating, would need a unit stated beside
    each, and would let a mistyped bound return an empty screen that looks like
    an empty database.
    """

    has_operator = False
    template = "sqladmin/filters/lookup_filter.html"

    #: value -> (label, lower inclusive, upper exclusive). `None` is unbounded.
    #: The boundaries are decimal orders of magnitude and the top band starts
    #: at ten tonnes, which on a real business is a year rather than a week —
    #: it is the band a moderator opens first.
    BANDS = {
        "lt100": ("Under 100 kg", None, Decimal("100")),
        "100-1k": ("100 kg – 1 tonne", Decimal("100"), Decimal("1000")),
        "1k-10k": ("1 – 10 tonnes", Decimal("1000"), Decimal("10000")),
        "gte10k": ("10 tonnes and over", Decimal("10000"), None),
    }

    def __init__(self, title="Food waste recorded", parameter_name="mass"):
        self.title = title
        self.parameter_name = parameter_name

    async def lookups(self, request, model, run_query):
        return [("__all", "Any amount")] + [
            (value, label) for value, (label, _, _) in self.BANDS.items()
        ]

    async def get_filtered_query(self, query, value, model):
        band = self.BANDS.get(value)
        if band is None:
            return query
        _, lower, upper = band
        total = _recorded_mass_subquery()
        if lower is not None:
            query = query.where(total >= lower)
        if upper is not None:
            #: Exclusive upper bound, so the bands tile without overlapping.
            #: `<=` on both sides would put exactly 1,000 kg in two bands and
            #: make the four counts sum to more than the table holds.
            query = query.where(total < upper)
        return query


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

    #: §8.2's "search and filtering". There is no free text on this model — no
    #: name, no note, nothing a person typed — so search would have nothing to
    #: match and is left off rather than shipped as an empty box that finds
    #: nothing. Filtering is what this screen can honestly offer, and the two
    #: filters are the two questions a moderator actually has: what have I
    #: already dealt with, and which methane horizon was this run at.
    column_filters = [
        RecentWindowFilter(),
        SupplyChainStageFilter(),
        RecordedMassFilter(),
        BooleanFilter(Submission.excluded_from_public, title="Excluded"),
        StaticValuesFilter(
            Submission.gwp_horizon,
            [("20", "20 years"), ("100", "100 years")],
            title="Methane horizon",
        ),
    ]

    column_default_sort = ("created_at", True)

    list_template = "brand/submission_list.html"
    details_template = "brand/submission_details.html"

    def list_query(self, request):
        """Eager-load what the two derived columns walk.

        Without this the list page is N+1 twice over: once for `entries` and
        again for each entry's `lines` and `sector`. Fifty rows is 151 queries,
        and the page still renders — slowly, and only on a database small
        enough not to notice.
        """
        return super().list_query(request).options(
            selectinload(Submission.entries).selectinload(SubmissionEntry.lines),
            selectinload(Submission.entries).selectinload(SubmissionEntry.sector),
        )

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
