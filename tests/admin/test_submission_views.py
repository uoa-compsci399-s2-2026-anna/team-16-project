"""`/admin/submissions` — contract §8.2's moderation screen.

**The two things this file exists to prove, in order of how badly they would
hurt.**

1. **No token reaches a page.** `submission.token` is the one field that ever
   tied a stored row to a browser session, and contract v1.5 put it in
   `REDACTED_FIELDS` *ahead of this screen* precisely because a submission view
   built on `AuditedModelView` would copy it into `audit_log`. This file drives
   the list, the details page and an exclusion, and asserts a real token is on
   none of them — including in the audit row the exclusion writes. Asserting
   "the token is absent" against a page that never had one would pass for a
   view that does not exist, so every one of those checks is paired with a
   positive one: the page must be showing this submission at all.
2. **The derived columns are right.** The sector summary and the recorded mass
   are computed in Python across two child tables. A wrong number here is not a
   crash; it is a staff member excluding the wrong row from public statistics.

Both are driven through real HTTP as a signed-in administrator, because the
question is what renders, and only a request can answer it.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from db.models import (
    AuditLog,
    Destination,
    DestinationGroup,
    FactorSet,
    FactorSetStatus,
    FoodCategory,
    Scenario,
    Sector,
    Submission,
    SubmissionEntry,
    SubmissionLine,
    utcnow,
)

pytestmark = [pytest.mark.db]

#: A real UUID4 in canonical form, so the "no token on the page" assertions are
#: looking for the thing that would actually be leaked rather than a sentinel
#: nothing would ever render.
_TOKEN = "3f2a91c4-77b5-4d1e-9c08-6b5e2a7d4419"


def _taxonomy(session):
    """The rows a submission needs to point at. Codes are prefixed so the
    fixture cannot collide with the seeded taxonomy or with another test's."""
    factor_set = FactorSet(
        version_label="SUBVIEW-TEST", status=FactorSetStatus.draft, is_mock=True
    )
    session.add(factor_set)
    sector = Sector(code="subview_processing", name="Processing", sort_order=1)
    retail = Sector(code="subview_retail", name="Retail", sort_order=2)
    hosp = Sector(code="subview_hosp", name="Hospitality", sort_order=3)
    bakery = FoodCategory(code="subview_bakery", name="Bread and bakery", sort_order=1)
    group = DestinationGroup(code="subview_waste", name="Waste", is_waste=True, sort_order=1)
    session.add_all([sector, retail, hosp, bakery, group])
    session.flush()
    landfill = Destination(
        group_id=group.id, code="subview_landfill", name="Landfill", sort_order=1
    )
    compost = Destination(
        group_id=group.id, code="subview_compost", name="Composting", sort_order=2
    )
    prevention = Destination(
        group_id=group.id,
        code="subview_prevention",
        name="Prevented",
        is_prevention=True,
        sort_order=3,
    )
    session.add_all([landfill, compost, prevention])
    session.flush()
    return {
        "factor_set": factor_set,
        "sectors": [sector, retail, hosp],
        "food_category": bakery,
        "landfill": landfill,
        "compost": compost,
        "prevention": prevention,
    }


def _submission(session, taxonomy, *, sectors, current, alternative=None, token=_TOKEN):
    """One submission, one entry per sector named, `current`/`alternative`
    being lists of `(destination, kg)`."""
    now = utcnow()
    row = Submission(
        token=token,
        token_expires_at=now + timedelta(hours=1),
        created_at=now,
        updated_at=now,
        factor_set_id=taxonomy["factor_set"].id,
        gwp_horizon=100,
    )
    session.add(row)
    session.flush()
    for index, sector in enumerate(sectors):
        entry = SubmissionEntry(
            submission_id=row.id,
            sector_id=sector.id,
            food_category_id=taxonomy["food_category"].id if index == 0 else None,
            sort_order=index,
        )
        session.add(entry)
        session.flush()
        for destination, qty in current:
            session.add(
                SubmissionLine(
                    submission_entry_id=entry.id,
                    scenario=Scenario.current,
                    destination_id=destination.id,
                    qty_kg=Decimal(qty),
                )
            )
        for destination, qty in alternative or []:
            session.add(
                SubmissionLine(
                    submission_entry_id=entry.id,
                    scenario=Scenario.alternative,
                    destination_id=destination.id,
                    qty_kg=Decimal(qty),
                )
            )
    session.flush()
    session.commit()
    return row


@pytest.fixture
def seeded(admin_app):
    """One submission with two entries, committed, and torn down afterwards.

    Committed rather than left in a transaction because the assertions below
    arrive over HTTP, through the application's own session — a row that exists
    only inside this test's transaction is invisible to the request.
    """
    with admin_app.state.session_factory() as session:
        taxonomy = _taxonomy(session)
        session.commit()
        row = _submission(
            session,
            taxonomy,
            sectors=taxonomy["sectors"][:2],
            current=[(taxonomy["landfill"], "1200.500"), (taxonomy["compost"], "300.250")],
            alternative=[(taxonomy["prevention"], "1500.750")],
        )
        submission_id = row.id
        factor_set_id = taxonomy["factor_set"].id
    yield {"id": submission_id, "factor_set_id": factor_set_id}
    with admin_app.state.session_factory() as session:
        session.execute(
            AuditLog.__table__.delete().where(AuditLog.table_name == "submission")
        )
        session.execute(
            Submission.__table__.delete().where(Submission.id == submission_id)
        )
        session.execute(FactorSet.__table__.delete().where(FactorSet.id == factor_set_id))
        for model, prefix in (
            (Destination, "subview_"),
            (DestinationGroup, "subview_"),
            (Sector, "subview_"),
            (FoodCategory, "subview_"),
        ):
            session.execute(model.__table__.delete().where(model.code.like(f"{prefix}%")))
        session.commit()


# --- the list ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_list_shows_the_three_columns_it_promises(admin_client, seeded):
    """Time, stage, mass — and the mass is the *current* scenario only.

    1200.500 + 300.250 = 1500.750 per entry, across two entries = 3001.500. The
    alternative adds another 1500.750 per entry and must not appear in this
    figure: both scenarios move the same mass by construction (§4.1), so
    counting both would double every row on this page.
    """
    response = await admin_client.get("/admin/submissions/list")

    assert response.status_code == 200
    body = response.text
    assert "Processing, Retail" in body, "the sector summary is not rendering"
    assert "3,001.500 kg" in body, "the recorded mass is wrong or absent"
    assert "4,502.250" not in body, "the alternative scenario is being counted too"


@pytest.mark.asyncio
async def test_the_list_never_renders_the_session_token(admin_client, seeded):
    """§2.3's linkage, and the reason `token` was redacted before this screen
    was written.

    The positive half matters as much as the negative one: a page that failed
    to render the submission at all would satisfy `token not in body` perfectly.
    """
    response = await admin_client.get("/admin/submissions/list")

    assert response.status_code == 200
    assert "3,001.500 kg" in response.text, "the row is not on the page to begin with"
    assert _TOKEN not in response.text


@pytest.mark.asyncio
async def test_more_sectors_than_fit_are_summarised_rather_than_listed(
    admin_client, admin_app, seeded
):
    """Three entries print as two names and a count, so the column has a fixed
    maximum width however many stages a business reports."""
    with admin_app.state.session_factory() as session:
        taxonomy = {
            "factor_set": session.get(FactorSet, seeded["factor_set_id"]),
            "food_category": session.scalar(
                select(FoodCategory).where(FoodCategory.code == "subview_bakery")
            ),
            "sectors": session.scalars(
                select(Sector).where(Sector.code.like("subview_%")).order_by(Sector.sort_order)
            ).all(),
            "landfill": session.scalar(
                select(Destination).where(Destination.code == "subview_landfill")
            ),
        }
        row = _submission(
            session,
            taxonomy,
            sectors=taxonomy["sectors"],
            current=[(taxonomy["landfill"], "10.000")],
            token=None,
        )
        extra_id = row.id

    try:
        response = await admin_client.get("/admin/submissions/list")
        assert response.status_code == 200
        assert "Processing, Retail +1" in response.text
    finally:
        with admin_app.state.session_factory() as session:
            session.execute(Submission.__table__.delete().where(Submission.id == extra_id))
            session.commit()


# --- the drill-down ---------------------------------------------------------


@pytest.mark.asyncio
async def test_the_details_page_names_every_destination_and_both_scenarios(
    admin_client, seeded
):
    """The whole reason this view has its own template: sqladmin renders a
    relationship as a list of `__str__` values, which would print the entries
    and drop every line."""
    response = await admin_client.get(f"/admin/submissions/details/{seeded['id']}")

    assert response.status_code == 200
    body = response.text
    assert "Landfill" in body and "Composting" in body
    assert "1,200.500 kg" in body and "300.250 kg" in body
    assert "Prevented" in body, "the alternative scenario's destination is missing"
    assert "1,500.750 kg" in body
    assert "Bread and bakery" in body, "the entry's food category is missing"
    assert "Standard mix" in body, "the entry with no food category is not labelled"


@pytest.mark.asyncio
async def test_the_details_page_never_renders_the_session_token(admin_client, seeded):
    """`column_details_list` defaults to every mapped column, which is how the
    staff details page once rendered a bcrypt hash (`admin/modelviews.py`,
    point 2). Same defect, same door, different table."""
    response = await admin_client.get(f"/admin/submissions/details/{seeded['id']}")

    assert response.status_code == 200
    assert "Landfill" in response.text, "the page is not showing the submission"
    assert _TOKEN not in response.text


# --- moderation -------------------------------------------------------------


@pytest.mark.asyncio
async def test_excluding_records_the_reason_in_the_audit_log(
    admin_client, admin_app, seeded
):
    """§8.2: "allows setting `excluded_from_public` with a reason". The reason
    is the only part of this that is worth anything six months later."""
    response = await admin_client.post(
        f"/admin/submissions/moderate?pks={seeded['id']}&exclude=1",
        data={"reason": "Test data — 3 tonnes from a two-person bakery"},
        follow_redirects=False,
    )

    assert response.status_code == 302

    with admin_app.state.session_factory() as session:
        row = session.get(Submission, seeded["id"])
        assert row.excluded_from_public is True

        entry = session.scalar(
            select(AuditLog)
            .where(AuditLog.table_name == "submission", AuditLog.row_id == seeded["id"])
            .order_by(AuditLog.id.desc())
        )
        assert entry is not None, "no audit entry was written"
        assert entry.action == "exclude_submission"
        assert entry.after_json["reason"].startswith("Test data")
        assert entry.after_json["excluded_from_public"] is True
        assert entry.before_json == {"excluded_from_public": False}
        #: The audit payload is hand-built from the one field that changed, so
        #: the token is not in it — rather than being in it and redacted.
        assert _TOKEN not in str(entry.after_json) + str(entry.before_json)


@pytest.mark.asyncio
async def test_a_blank_reason_is_refused_and_changes_nothing(
    admin_client, admin_app, seeded
):
    """Refused rather than defaulted to "no reason given". An audit row that
    answers nobody's question later is worse than the moderation not happening,
    because the submission is still there to try again."""
    response = await admin_client.post(
        f"/admin/submissions/moderate?pks={seeded['id']}&exclude=1",
        data={"reason": "   "},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Give a reason" in response.text

    with admin_app.state.session_factory() as session:
        assert session.get(Submission, seeded["id"]).excluded_from_public is False
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.table_name == "submission")
            )
            == 0
        )


@pytest.mark.asyncio
async def test_a_row_already_in_the_requested_state_writes_no_audit_entry(
    admin_client, admin_app, seeded
):
    """Excluding an excluded row is not an error — it is what happens when
    somebody selects a whole page — but it must not fill the trail with
    entries in which nothing changed."""
    first = await admin_client.post(
        f"/admin/submissions/moderate?pks={seeded['id']}&exclude=1",
        data={"reason": "First pass"},
        follow_redirects=False,
    )
    assert first.status_code == 302

    second = await admin_client.post(
        f"/admin/submissions/moderate?pks={seeded['id']}&exclude=1",
        data={"reason": "Second pass, nothing to do"},
        follow_redirects=False,
    )
    assert second.status_code == 302

    with admin_app.state.session_factory() as session:
        count = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.table_name == "submission")
        )
    assert count == 1, "the second, no-op exclusion wrote an audit entry"


@pytest.mark.asyncio
async def test_returning_a_submission_to_the_statistics_is_audited_too(
    admin_client, admin_app, seeded
):
    """The reverse needs a reason for the same reason the exclusion does:
    otherwise the trail shows one person excluding a row with a stated reason
    and another undoing it with none."""
    await admin_client.post(
        f"/admin/submissions/moderate?pks={seeded['id']}&exclude=1",
        data={"reason": "Excluded first"},
        follow_redirects=False,
    )
    response = await admin_client.post(
        f"/admin/submissions/moderate?pks={seeded['id']}&exclude=0",
        data={"reason": "Checked with the client — it is a real figure"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    with admin_app.state.session_factory() as session:
        assert session.get(Submission, seeded["id"]).excluded_from_public is False
        entry = session.scalar(
            select(AuditLog)
            .where(AuditLog.table_name == "submission")
            .order_by(AuditLog.id.desc())
        )
        assert entry.action == "include_submission"
        assert "real figure" in entry.after_json["reason"]


@pytest.mark.asyncio
async def test_the_moderation_page_lists_what_is_about_to_change(admin_client, seeded):
    """Reached from a bulk selection, so it names the rows rather than a count:
    a page that said "1 submission selected" would ask somebody to act on a
    number."""
    response = await admin_client.get(
        f"/admin/submissions/moderate?pks={seeded['id']}&exclude=1"
    )

    assert response.status_code == 200
    assert "Processing, Retail" in response.text
    assert "3,001.500 kg" in response.text
    assert _TOKEN not in response.text


# --- what the screen refuses to be ------------------------------------------


@pytest.mark.asyncio
async def test_a_submission_can_be_neither_created_nor_edited_nor_deleted(
    admin_client, admin_app, seeded
):
    """A submission is a record of something a member of the public did.
    Inventing one corrupts the statistics with a number nobody entered;
    editing one rewrites what a visitor actually submitted; deleting one
    destroys the evidence that the calculation was run. Moderation is the only
    write §8.2 asks for, and it is the only one this view offers.

    Driven as requests rather than asserted on the class flags — sqladmin only
    honours `can_create`/`can_edit`/`can_delete` if it reads them, and a flag
    nobody reads is a comment.
    """
    for path in (
        "/admin/submissions/create",
        f"/admin/submissions/edit/{seeded['id']}",
    ):
        response = await admin_client.get(path, follow_redirects=False)
        assert response.status_code in (403, 404), f"{path} answered {response.status_code}"

    #: DELETE, which is the method sqladmin's own route accepts. A POST here
    #: answers 405 whether or not deletion is disabled, so a test written that
    #: way would pass against a view that deletes happily.
    response = await admin_client.request(
        "DELETE", f"/admin/submissions/delete?pks={seeded['id']}", follow_redirects=False
    )
    assert response.status_code in (403, 404), (
        f"delete answered {response.status_code}"
    )

    #: And the row is still there, which is the assertion that does not depend
    #: on reading a status code correctly.
    with admin_app.state.session_factory() as session:
        assert session.get(Submission, seeded["id"]) is not None


# --- local time -------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_timestamp_carries_an_explicit_utc_offset(admin_client, seeded):
    """**The Z is the whole assertion, and its absence is silent.**

    `created_at` is stored naive (contract 1.3, `admin/models.py::utcnow`), so
    `isoformat()` alone yields `2026-08-16T05:28:18` - and ECMA-262 parses a
    date-time form with no offset as *local* time. `admin/static/localtime.js`
    would then read a UTC instant as if it were already local and render it
    thirteen hours out in Auckland, eight in Shanghai, in a direction that looks
    entirely plausible on a page nobody cross-checks.

    Nothing about the page breaks if the Z goes. That is exactly why it is
    tested rather than trusted.
    """
    for path in ("/admin/submissions/list", f"/admin/submissions/details/{seeded['id']}"):
        response = await admin_client.get(path)
        assert response.status_code == 200
        stamps = re.findall(r'<time datetime="([^"]+)"', response.text)
        assert stamps, f"{path} rendered no <time> element"
        for value in stamps:
            assert value.endswith("Z"), (
                f"{path} emitted {value!r} - a browser reads that as local time"
            )
            assert "T" in value, f"{path} emitted {value!r}, which is not a date-time"


@pytest.mark.asyncio
async def test_the_visible_text_is_still_utc_and_says_so(admin_client, seeded):
    """The script is an upgrade over a page that is already correct. With it
    absent, blocked or broken - which is what a test client is - the reader sees
    the UTC instant and the word that tells them which zone it is in."""
    response = await admin_client.get("/admin/submissions/list")

    assert response.status_code == 200
    assert re.search(r'<time datetime="[^"]+">[^<]*UTC</time>', response.text), (
        "the fallback text no longer names its zone"
    )


@pytest.mark.asyncio
async def test_both_pages_load_the_conversion_script(admin_client, seeded):
    """Asserted on both, because they inherit from two different bases - the
    list from `brand/model_list.html`, the details page from sqladmin's own -
    and only one of them being wired up is the failure that presents as "it
    works on the list"."""
    for path in ("/admin/submissions/list", f"/admin/submissions/details/{seeded['id']}"):
        response = await admin_client.get(path)
        assert "/admin/static/localtime.js" in response.text, f"{path} has no script"


@pytest.mark.asyncio
async def test_the_conversion_script_is_actually_served(admin_client):
    """A `<script src>` pointing at a 404 leaves a page that silently keeps
    showing UTC, and the test above would still pass."""
    response = await admin_client.get("/admin/static/localtime.js")

    assert response.status_code == 200
    assert "Intl.DateTimeFormat" in response.text


# --- filtering --------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_stage_filter_narrows_to_that_stage(admin_client, seeded):
    """Both directions. A one-sided filter test passes for a filter that
    returns nothing at all."""
    shown = await admin_client.get("/admin/submissions/list?stage=subview_processing")
    assert shown.status_code == 200
    assert "3,001.500 kg" in shown.text, "the matching submission was filtered out"

    hidden = await admin_client.get("/admin/submissions/list?stage=subview_hosp")
    assert hidden.status_code == 200
    assert "3,001.500 kg" not in hidden.text, "a non-matching submission was returned"


@pytest.fixture
def two_entries_one_stage(admin_app, seeded):
    """A submission with two entries at the SAME stage, which is what
    `uq_submission_entry` permits: it is UNIQUE on (submission, sector,
    food_category), so one business reporting bakery waste and dairy waste at
    Processing is two rows carrying one sector."""
    with admin_app.state.session_factory() as session:
        processing = session.scalar(
            select(Sector).where(Sector.code == "subview_processing")
        )
        landfill = session.scalar(
            select(Destination).where(Destination.code == "subview_landfill")
        )
        bakery = session.scalar(
            select(FoodCategory).where(FoodCategory.code == "subview_bakery")
        )
        dairy = FoodCategory(code="subview_dairy", name="Dairy", sort_order=2)
        session.add(dairy)
        session.flush()

        now = utcnow()
        row = Submission(
            token=None,
            created_at=now,
            updated_at=now,
            factor_set_id=seeded["factor_set_id"],
            gwp_horizon=100,
        )
        session.add(row)
        session.flush()
        for order, category in enumerate((bakery, dairy)):
            entry = SubmissionEntry(
                submission_id=row.id,
                sector_id=processing.id,
                food_category_id=category.id,
                sort_order=order,
            )
            session.add(entry)
            session.flush()
            session.add(
                SubmissionLine(
                    submission_entry_id=entry.id,
                    scenario=Scenario.current,
                    destination_id=landfill.id,
                    qty_kg=Decimal("4321.000"),
                )
            )
        session.commit()
        row_id = row.id

    yield row_id

    with admin_app.state.session_factory() as session:
        session.execute(Submission.__table__.delete().where(Submission.id == row_id))
        session.execute(
            FoodCategory.__table__.delete().where(FoodCategory.code == "subview_dairy")
        )
        session.commit()


def _reported_count(body: str) -> int:
    r"""The `N` from sqladmin's "Showing 1 to 2 of N items".

    **Anchored to the element, not to the words.** An unanchored
    `of\s+(\d+)\s+items` also matches a comment inside
    `brand/list_table.html`, which quotes that exact sentence as an example -
    and returns 776, a number out of a docstring, asserted against as though it
    had come from the database. That is how the first version of this helper
    behaved. This project has now shipped an unanchored-pattern defect four
    times; see the note on `_ADMIN_PATH` in
    `tests/admin/test_operator_guidance.py` for the last one.
    """
    match = re.search(
        r'<p class="m-0 text-muted">\s*Showing\s+\d+\s+to\s+\d+\s+of\s+(\d+)\s+items',
        body,
    )
    assert match, "the list page did not report a count at all"
    return int(match.group(1))


@pytest.mark.asyncio
async def test_a_stage_filter_reports_the_number_of_rows_it_renders(
    admin_client, seeded, two_entries_one_stage
):
    """**EXISTS, not a join - and the symptom of getting it wrong is the count,
    not the rows.**

    Two earlier versions of this test proved nothing, and both times a mutation
    is what said so:

    1. The first filtered the seeded submission, whose two entries are at two
       *different* stages. Filtering on one stage matches one of them, so a
       plain join returns one row as well.
    2. The second used a submission with two entries at one stage and counted
       the rendered figure. Still green under a join, because
       `ModelView._run_query` calls `.scalars().unique()` - SQLAlchemy
       deduplicates the entities before sqladmin ever renders them.

    What a join actually breaks is the count. sqladmin builds it as
    `select(count()).select_from(stmt.subquery())` over the *unfiltered-by-
    unique* statement, so the joined duplicate is counted and the page reports
    more rows than it draws. A moderator told there are five submissions and
    shown four has no way to know which number is lying, and no reason to
    suspect either.

    So this asserts the two agree.
    """
    response = await admin_client.get("/admin/submissions/list?stage=subview_processing")
    assert response.status_code == 200

    body = response.text
    rendered = body.count("8,642.000 kg")
    assert rendered == 1, f"the two-entry submission was drawn {rendered} times"

    #: Both submissions match this stage - the seeded one and the two-entry one
    #: - so the count is two. Under a join it is three.
    assert _reported_count(body) == 2, (
        "the page reports a different number of submissions than it rendered"
    )


@pytest.mark.asyncio
async def test_a_row_lands_in_exactly_one_band(admin_client, seeded):
    """3,001.500 kg belongs to `1k-10k` and to none of the other three."""
    hits = []
    for band in ("lt100", "100-1k", "1k-10k", "gte10k"):
        response = await admin_client.get(f"/admin/submissions/list?mass={band}")
        assert response.status_code == 200
        if "3,001.500 kg" in response.text:
            hits.append(band)

    assert hits == ["1k-10k"], f"the row appeared in {hits}"


@pytest.mark.asyncio
async def test_the_bands_tile_at_the_boundary_itself(admin_client, admin_app, seeded):
    """**Exactly 1,000.000 kg, because that is the only value that can tell
    the two spellings apart.**

    A first version of this test used the seeded 3,001.500 kg row and asserted
    it appeared in one band. It does - under `total < upper` and under
    `total <= upper` alike, because 3,001.5 is not a boundary. The mutation
    changing one to the other left the test green, which is how this second
    test came to exist.

    A row sitting exactly on a boundary is in the upper band and not the lower
    one. With `<=` it is in both, the four counts sum to more than the table
    holds, and every figure a moderator reads off this filter is inflated by
    however many rows happen to be round numbers - which, in a tool where
    people type 1000, is a lot of them."""
    with admin_app.state.session_factory() as session:
        processing = session.scalar(
            select(Sector).where(Sector.code == "subview_processing")
        )
        landfill = session.scalar(
            select(Destination).where(Destination.code == "subview_landfill")
        )
        now = utcnow()
        row = Submission(
            token=None,
            created_at=now,
            updated_at=now,
            factor_set_id=seeded["factor_set_id"],
            gwp_horizon=100,
        )
        session.add(row)
        session.flush()
        entry = SubmissionEntry(
            submission_id=row.id,
            sector_id=processing.id,
            food_category_id=None,
            sort_order=0,
        )
        session.add(entry)
        session.flush()
        session.add(
            SubmissionLine(
                submission_entry_id=entry.id,
                scenario=Scenario.current,
                destination_id=landfill.id,
                qty_kg=Decimal("1000.000"),
            )
        )
        session.commit()
        boundary_id = row.id

    try:
        upper = await admin_client.get("/admin/submissions/list?mass=1k-10k")
        assert upper.status_code == 200
        assert "1,000.000 kg" in upper.text, (
            "a row exactly on the boundary is missing from the band above it"
        )

        lower = await admin_client.get("/admin/submissions/list?mass=100-1k")
        assert lower.status_code == 200
        assert "1,000.000 kg" not in lower.text, (
            "the bands overlap: 1,000.000 kg is in two of them at once"
        )
    finally:
        with admin_app.state.session_factory() as session:
            session.execute(
                Submission.__table__.delete().where(Submission.id == boundary_id)
            )
            session.commit()


@pytest.mark.asyncio
async def test_the_window_filter_keeps_a_fresh_row_and_drops_an_old_one(
    admin_client, admin_app, seeded
):
    """The seeded row is minutes old; a second is backdated past every window,
    so the filter is shown doing both halves of its job. The unfiltered request
    at the end is what stops a filter that returns nothing from passing."""
    with admin_app.state.session_factory() as session:
        taxonomy = {
            "factor_set": session.get(FactorSet, seeded["factor_set_id"]),
            "food_category": session.scalar(
                select(FoodCategory).where(FoodCategory.code == "subview_bakery")
            ),
            "sectors": session.scalars(
                select(Sector).where(Sector.code == "subview_processing")
            ).all(),
            "landfill": session.scalar(
                select(Destination).where(Destination.code == "subview_landfill")
            ),
        }
        old = _submission(
            session,
            taxonomy,
            sectors=taxonomy["sectors"],
            current=[(taxonomy["landfill"], "77.000")],
            token=None,
        )
        old.created_at = utcnow() - timedelta(days=400)
        session.commit()
        old_id = old.id

    try:
        recent = await admin_client.get("/admin/submissions/list?window=24h")
        assert recent.status_code == 200
        assert "3,001.500 kg" in recent.text, "the fresh row was dropped"
        assert "77.000 kg" not in recent.text, "the 400-day-old row was kept"

        everything = await admin_client.get("/admin/submissions/list")
        assert "77.000 kg" in everything.text, "unfiltered, the old row should return"
    finally:
        with admin_app.state.session_factory() as session:
            session.execute(Submission.__table__.delete().where(Submission.id == old_id))
            session.commit()


@pytest.mark.asyncio
async def test_a_filtered_page_renders_the_rows_its_filter_selected(admin_client, seeded):
    """**The failure the scalar subquery was chosen to avoid.** sqladmin builds
    its count from `select(count()).select_from(stmt.subquery())`, so a filter
    that changed the statement's shape - a join with GROUP BY, say - could
    report a count that disagrees with what is rendered. A moderator told there
    are 40 rows and shown 12 has no way to know which number is the lie.

    Both ends of one band are driven: the band that holds the row shows it, and
    the band above it shows nothing."""
    inside = await admin_client.get("/admin/submissions/list?mass=1k-10k")
    assert inside.status_code == 200
    assert "3,001.500 kg" in inside.text

    above = await admin_client.get("/admin/submissions/list?mass=gte10k")
    assert above.status_code == 200
    assert "3,001.500 kg" not in above.text


# --- the filter bar ---------------------------------------------------------


def _filter_form(body: str) -> str:
    """The bar's own markup, and nothing else on the page.

    Sliced out before anything is asserted about it. The list page carries
    sqladmin's own `<select>`s (the page-size menu) and its own hidden inputs
    (the delete modal's), so a `<select` count or a `name="sortBy"` search over
    the whole document answers a question about the page rather than about this
    form."""
    match = re.search(r'<form method="get" class="kc-filters".*?</form>', body, re.S)
    assert match, "the filter bar did not render"
    return match.group(0)


@pytest.mark.asyncio
async def test_the_filters_are_one_row_of_selects_above_the_table(admin_client, seeded):
    """The owner's ask: a bar across the top, not sqladmin's right-hand sidebar
    of link lists.

    Asserted as "a form with one select per filter", plus the rule that hides
    the sidebar — leaving that visible would give the page two filter UIs
    disagreeing with each other, which is worse than either alone."""
    response = await admin_client.get("/admin/submissions/list")
    assert response.status_code == 200
    body = response.text

    form = _filter_form(body)
    for name in ("window", "stage", "mass", "excluded_from_public", "gwp_horizon"):
        assert f'name="{name}"' in form, f"the {name} filter is not in the bar"

    assert ".filter-sidebar-col { display: none" in body, (
        "sqladmin's own filter sidebar is still on the page beside this bar"
    )

    #: The bar is above the table, not below it. Position in the document is
    #: the only thing that makes it a bar "across the top" rather than a form
    #: somebody has to scroll past the results to find.
    assert body.index(form) < body.index("<table"), "the bar renders below the table"


@pytest.mark.asyncio
async def test_the_bar_shows_which_filter_is_active(admin_client, seeded):
    """A filtered table whose controls all read "Any" is a page that tells the
    reader they are seeing everything while showing them a subset."""
    response = await admin_client.get(
        "/admin/submissions/list?stage=subview_retail&mass=1k-10k"
    )
    assert response.status_code == 200

    form = _filter_form(response.text)
    selected = re.findall(r'<option value="([^"]+)" selected', form)
    assert sorted(selected) == ["1k-10k", "subview_retail"], (
        f"the bar does not reflect the active filters: {selected}"
    )


@pytest.mark.asyncio
async def test_the_bar_carries_the_sort_but_not_the_page(admin_client, seeded):
    """**Two opposite decisions, and each is a defect if made the other way.**

    `sortBy` has to survive: a form that submitted only its own selects would
    silently discard the sort order the staff member just chose, and the table
    would reorder itself for no visible reason.

    `page` must not: a new filter is a new result set, and page 4 of a set with
    two pages renders an empty table that reads as "no matches".
    """
    response = await admin_client.get(
        "/admin/submissions/list?sortBy=created_at&sort=desc&page=1"
    )
    assert response.status_code == 200

    form = _filter_form(response.text)
    hidden = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form))
    assert hidden.get("sortBy") == "created_at", f"sortBy was dropped: {hidden}"
    assert hidden.get("sort") == "desc", f"sort direction was dropped: {hidden}"
    assert "page" not in hidden, "the page number would survive a filter change"


@pytest.mark.asyncio
async def test_the_clear_link_appears_only_when_something_is_filtered(
    admin_client, seeded
):
    """A permanently visible Clear on an unfiltered table invites a click that
    does nothing, and teaches the reader that the bar is inert."""
    unfiltered = _filter_form((await admin_client.get("/admin/submissions/list")).text)
    assert ">\n        Clear\n      </a>" not in unfiltered and "Clear" not in unfiltered

    filtered = _filter_form(
        (await admin_client.get("/admin/submissions/list?mass=1k-10k")).text
    )
    assert "Clear" in filtered, "no way back to the unfiltered table"


@pytest.mark.asyncio
async def test_the_bar_needs_no_javascript(admin_client, seeded):
    """A `<form method="get">` with a submit button, so the bar works with
    scripting off — the same standard `brand/block_ip.html` and the public
    site's drawer hold to. The one script on this page converts timestamps and
    is an enhancement over a page that already reads correctly."""
    form = _filter_form((await admin_client.get("/admin/submissions/list")).text)

    assert 'method="get"' in form
    assert 'type="submit"' in form
    assert "onclick" not in form and "addEventListener" not in form
