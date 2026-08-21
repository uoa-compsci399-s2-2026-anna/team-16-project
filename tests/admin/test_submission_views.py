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
from datetime import datetime, timedelta
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


def _clear_fixture_rows(admin_app):
    """Every row this file creates, in foreign-key order.

    Submissions first (their entries and lines cascade), then the audit entries
    they attracted, then the factor set they pointed at, then the taxonomy.
    Reversing that order fails on a constraint rather than on anything to do
    with the test.
    """
    with admin_app.state.session_factory() as session:
        stale = session.scalars(
            select(FactorSet.id).where(FactorSet.version_label == "SUBVIEW-TEST")
        ).all()
        if stale:
            session.execute(
                Submission.__table__.delete().where(Submission.factor_set_id.in_(stale))
            )
        session.execute(
            AuditLog.__table__.delete().where(AuditLog.table_name == "submission")
        )
        session.execute(
            FactorSet.__table__.delete().where(FactorSet.version_label == "SUBVIEW-TEST")
        )
        for model in (Destination, DestinationGroup, Sector, FoodCategory):
            session.execute(model.__table__.delete().where(model.code.like("subview_%")))
        session.commit()


@pytest.fixture
def seeded(admin_app):
    """One submission with two entries, committed, and torn down afterwards.

    Committed rather than left in a transaction because the assertions below
    arrive over HTTP, through the application's own session — a row that exists
    only inside this test's transaction is invisible to the request.
    """
    #: Cleared BEFORE the fixture builds anything, not only after. A run
    #: interrupted mid-test - a failing assertion under `-x`, a killed process -
    #: leaves `SUBVIEW-TEST` behind, and the next run then dies in setup on a
    #: duplicate `version_label` with an error about the database rather than
    #: about the test. `tests/admin/conftest.py` gives the same reason for
    #: running its staff teardown in both directions.
    _clear_fixture_rows(admin_app)
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
    _clear_fixture_rows(admin_app)


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
async def test_every_control_is_in_one_bar_above_the_table(admin_client, seeded):
    """The owner's ask: a bar across the top, not sqladmin's right-hand
    sidebar of link lists.

    **And sqladmin now renders no sidebar at all**, because `column_filters` is
    empty - so this asserts its absence rather than a CSS rule hiding it. A
    rule that hides an element is one stylesheet edit away from stopping;
    an element that was never rendered cannot come back without this failing.
    """
    response = await admin_client.get("/admin/submissions/list")
    assert response.status_code == 200
    body = response.text

    form = _filter_form(body)
    for name in ("window", "stage", "mass", "excluded", "horizon", "from", "to"):
        assert f'name="{name}"' in form, f"the {name} control is not in the bar"

    #: The ELEMENT, not the string. `.filter-sidebar-col` is a rule in
    #: sqladmin's own stylesheet and is on every page whether or not a sidebar
    #: renders - asserting on the bare name matches the CSS and passes for a
    #: page that does render one. Fifth unanchored-match defect in this
    #: repository; the previous four are noted beside `_reported_count` and
    #: `_ADMIN_PATH`.
    assert 'id="filter-sidebar"' not in body, (
        "sqladmin is still rendering its own filter sidebar beside this bar"
    )

    #: Position in the document is the only thing that makes it a bar "across
    #: the top" rather than a form somebody scrolls past the results to find.
    assert body.index(form) < body.index("<table"), "the bar renders below the table"


@pytest.mark.asyncio
async def test_the_bar_shows_which_single_choice_filters_are_active(admin_client, seeded):
    """A filtered table whose controls all read "Any" tells the reader they are
    seeing everything while showing them a subset."""
    response = await admin_client.get(
        "/admin/submissions/list?window=7d&horizon=100&excluded=false"
    )
    assert response.status_code == 200

    form = _filter_form(response.text)
    selected = re.findall(r'<option value="([^"]+)" selected', form)
    assert sorted(selected) == ["100", "7d", "false"], (
        f"the bar does not reflect the active filters: {selected}"
    )


@pytest.mark.asyncio
async def test_the_bar_shows_which_boxes_are_ticked(admin_client, seeded):
    """The multi-select half of the same claim. Checkboxes, not options, so a
    separate assertion - and the two that are ticked have to be exactly the two
    in the query string."""
    response = await admin_client.get(
        "/admin/submissions/list?stage=subview_retail&stage=subview_hosp&mass=1k-10k"
    )
    assert response.status_code == 200

    form = _filter_form(response.text)
    ticked = set(
        re.findall(
            r'<input type="checkbox" name="\w+" value="([^"]+)"\s*\n?\s*checked', form
        )
    )
    assert ticked == {"subview_retail", "subview_hosp", "1k-10k"}, (
        f"the ticked boxes do not match the query string: {ticked}"
    )


@pytest.mark.asyncio
async def test_a_multi_select_opens_when_something_is_ticked(admin_client, seeded):
    """`<details open>` when a filter is active. A closed box whose summary
    reads "2 selected" is honest, but a reader arriving at a filtered table -
    from a bookmark, or a link a colleague sent - should be able to see WHICH
    two without a click."""
    filtered = _filter_form(
        (await admin_client.get("/admin/submissions/list?stage=subview_retail")).text
    )
    assert "<details class=\"kc-multi\" open>" in filtered

    unfiltered = _filter_form((await admin_client.get("/admin/submissions/list")).text)
    assert "<details class=\"kc-multi\" open>" not in unfiltered, (
        "an unfiltered bar opens its dropdowns for no reason"
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

    #: The multi-selects are `<details>`, which opens and closes natively -
    #: the same primitive the public site's drawer uses. A dropdown built from
    #: a button and a hidden div would need script to open at all, and this bar
    #: would stop working with scripting off.
    assert "<details" in form, "the multi-selects need JavaScript to open"


# --- the explicit date range ------------------------------------------------


@pytest.fixture
def dated_rows(admin_app, seeded):
    """Three submissions stamped at known UTC instants, and their masses are
    what identifies them on the page.

    The instants are chosen around a UTC midnight on purpose: 2026-03-10 at
    23:30 UTC is already the 11th in Auckland (UTC+13 in March), and 2026-03-11
    at 00:30 UTC is the 11th in both. A range filter that ignored the reader's
    zone would put those two in different days; one that got the sign backwards
    would put them in the wrong days.
    """
    stamps = {
        "1111.000": datetime(2026, 3, 10, 12, 0),
        "2222.000": datetime(2026, 3, 10, 23, 30),
        "3333.000": datetime(2026, 3, 11, 0, 30),
    }
    made = []
    with admin_app.state.session_factory() as session:
        processing = session.scalar(
            select(Sector).where(Sector.code == "subview_processing")
        )
        landfill = session.scalar(
            select(Destination).where(Destination.code == "subview_landfill")
        )
        for mass, moment in stamps.items():
            row = Submission(
                token=None,
                created_at=moment,
                updated_at=moment,
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
                    qty_kg=Decimal(mass),
                )
            )
            made.append(row.id)
        session.commit()

    yield {"ids": made}

    with admin_app.state.session_factory() as session:
        session.execute(Submission.__table__.delete().where(Submission.id.in_(made)))
        session.commit()


def _shown(body: str) -> set[str]:
    """Which of the dated rows the page drew, by their masses."""
    return {
        mass for mass in ("1,111.000 kg", "2,222.000 kg", "3,333.000 kg")
        if mass in body
    }


@pytest.mark.asyncio
async def test_a_single_day_range_includes_the_whole_of_that_day(
    admin_client, dated_rows
):
    """`from == to` is the commonest range anybody types, and taking the end
    date at face value would make it return nothing at all - which reads as
    "there were no calculations that day" rather than as a bad query."""
    response = await admin_client.get(
        "/admin/submissions/list?from=2026-03-10&to=2026-03-10&tzoffset=0"
    )

    assert response.status_code == 200
    assert _shown(response.text) == {"1,111.000 kg", "2,222.000 kg"}, (
        "a same-day range did not cover that whole day in UTC"
    )


@pytest.mark.asyncio
async def test_the_range_is_read_in_the_readers_zone_not_in_utc(
    admin_client, dated_rows
):
    """**The sign of `getTimezoneOffset()` is the thing being tested here.**

    It is UTC-minus-local, so Auckland is `-720`, which is the opposite of the
    "+12" people say out loud. Get it backwards and every window shifts by
    twice the offset - a day out in New Zealand - in a direction that still
    returns *some* rows, so the screen looks like it is working.

    2026-03-10 23:30 UTC is 2026-03-11 12:30 in Auckland. Asking for the 11th
    in local terms must therefore return it, and must not return 2026-03-10
    12:00 UTC, which is still the 11th at 01:00 local... and so is also in
    range. So the discriminating row is the first one at 12:00 UTC on the 10th,
    which in Auckland is 01:00 on the 11th.
    """
    utc = await admin_client.get(
        "/admin/submissions/list?from=2026-03-11&to=2026-03-11&tzoffset=0"
    )
    assert _shown(utc.text) == {"3,333.000 kg"}, (
        "with no offset the range should be plain UTC days"
    )

    #: Auckland, UTC+13 in March (daylight saving). `getTimezoneOffset()`
    #: reports -780.
    auckland = await admin_client.get(
        "/admin/submissions/list?from=2026-03-11&to=2026-03-11&tzoffset=-780"
    )
    assert _shown(auckland.text) == {"1,111.000 kg", "2,222.000 kg", "3,333.000 kg"}, (
        "the local-day window is not where the offset puts it"
    )

    #: And the day before, in Auckland, holds none of them - which is what
    #: fails if the sign is inverted.
    day_before = await admin_client.get(
        "/admin/submissions/list?from=2026-03-10&to=2026-03-10&tzoffset=-780"
    )
    assert _shown(day_before.text) == set(), (
        "the sign of the offset is inverted: the window landed a day early"
    )


@pytest.mark.asyncio
async def test_an_open_ended_range_bounds_only_the_end_it_names(
    admin_client, dated_rows
):
    """One box filled and the other empty is a normal thing to type."""
    from_only = await admin_client.get(
        "/admin/submissions/list?from=2026-03-11&tzoffset=0"
    )
    assert _shown(from_only.text) == {"3,333.000 kg"}

    to_only = await admin_client.get("/admin/submissions/list?to=2026-03-10&tzoffset=0")
    assert _shown(to_only.text) == {"1,111.000 kg", "2,222.000 kg"}


@pytest.mark.asyncio
async def test_a_nonsense_date_leaves_the_table_unfiltered(admin_client, dated_rows):
    """Only a hand-edited query string can put this here - `<input type="date">`
    submits `YYYY-MM-DD` or nothing. The useful answer is the unfiltered table,
    not a stack trace at somebody who cannot act on it."""
    response = await admin_client.get(
        "/admin/submissions/list?from=not-a-date&to=13/14/2026&tzoffset=banana"
    )

    assert response.status_code == 200
    assert _shown(response.text) == {"1,111.000 kg", "2,222.000 kg", "3,333.000 kg"}


@pytest.mark.asyncio
async def test_the_range_and_a_preset_narrow_together(admin_client, dated_rows):
    """They compose rather than one overriding the other, which is what
    "filter" means everywhere else on the page - and the form's hint says so.
    The dated rows are from March, so any recent-window preset excludes them
    however wide the range."""
    response = await admin_client.get(
        "/admin/submissions/list?from=2026-03-01&to=2026-03-31&window=24h&tzoffset=0"
    )

    assert response.status_code == 200
    assert _shown(response.text) == set()


@pytest.mark.asyncio
async def test_the_range_is_counted_as_well_as_rendered(admin_client, dated_rows):
    """The range is applied in `list_query` rather than through sqladmin's
    filter protocol, so this is the assertion that it is still counted:
    sqladmin builds its total from `select(count()).select_from(
    stmt.subquery())` over whatever `list_query` returns."""
    response = await admin_client.get(
        "/admin/submissions/list?from=2026-03-10&to=2026-03-10&tzoffset=0"
    )

    assert response.status_code == 200
    assert _reported_count(response.text) == 2


@pytest.mark.asyncio
async def test_the_bar_offers_the_range_and_keeps_what_was_typed(
    admin_client, seeded
):
    """Two `type="date"` inputs and a hidden offset field, and a submitted
    range comes back filled in - a form that cleared itself on submit would
    make a second, narrower query mean retyping both dates."""
    response = await admin_client.get(
        "/admin/submissions/list?from=2026-03-10&to=2026-03-11"
    )
    assert response.status_code == 200

    form = _filter_form(response.text)
    assert 'name="from"' in form and 'name="to"' in form
    assert 'type="date"' in form
    assert 'value="2026-03-10"' in form and 'value="2026-03-11"' in form
    assert 'name="tzoffset"' in form, "nothing tells the server which zone was meant"


# --- the bar in Chinese -----------------------------------------------------


@pytest.mark.asyncio
async def test_the_filter_bar_renders_in_chinese(admin_client, seeded):
    """**`tests/admin/test_i18n.py` cannot see these strings.**

    It walks view classes for `name`, `name_plural`, `category` and every
    `form_args` description. Filter titles and option labels are attributes of
    filter *objects*, and this screen has no form, so nothing in that file
    touches either - a catalogue miss here would be invisible until somebody
    opened the page in Chinese and found an English bar over a Chinese table.
    """
    response = await admin_client.get("/admin/submissions/list?lang=zh")
    assert response.status_code == 200

    form = _filter_form(response.text)
    for expected in (
        "计算时间",        # the window filter's title
        "最近 24 小时",    # one of its options
        "供应链环节",      # the stage filter
        "全部环节",
        "记录的食物浪费量",  # the mass filter
        "10 吨及以上",
        "起始日期",        # the range
        "查询",            # the submit button
    ):
        assert expected in form, f"{expected!r} is not translated on the bar"


@pytest.mark.asyncio
async def test_the_english_bar_is_unchanged(admin_client, seeded):
    """The affirmative half. Every assertion above is satisfied by a bar that
    renders Chinese unconditionally, which would be a different defect."""
    form = _filter_form((await admin_client.get("/admin/submissions/list")).text)

    assert "Last 24 hours" in form and "Any stage" in form
    assert "计算时间" not in form


# --- several values inside one filter ---------------------------------------


@pytest.fixture
def three_stages(admin_app, seeded):
    """One submission per stage, each with a distinct mass, so which rows came
    back can be read off the page."""
    masses = {
        "subview_processing": "5001.000",
        "subview_retail": "5002.000",
        "subview_hosp": "5003.000",
    }
    made = []
    with admin_app.state.session_factory() as session:
        landfill = session.scalar(
            select(Destination).where(Destination.code == "subview_landfill")
        )
        for code, mass in masses.items():
            sector = session.scalar(select(Sector).where(Sector.code == code))
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
                sector_id=sector.id,
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
                    qty_kg=Decimal(mass),
                )
            )
            made.append(row.id)
        session.commit()

    yield {f"{float(m):,.3f} kg": c for c, m in masses.items()}

    with admin_app.state.session_factory() as session:
        session.execute(Submission.__table__.delete().where(Submission.id.in_(made)))
        session.commit()


def _stage_rows(body: str) -> set[str]:
    return {
        mass for mass in ("5,001.000 kg", "5,002.000 kg", "5,003.000 kg")
        if mass in body
    }


@pytest.mark.asyncio
async def test_ticking_two_stages_returns_both(admin_client, three_stages):
    """OR inside the control. Both halves are asserted: the two ticked stages
    come back, and the third does not - a filter that ignored its values
    entirely would satisfy the first half on its own."""
    response = await admin_client.get(
        "/admin/submissions/list?stage=subview_processing&stage=subview_hosp"
    )

    assert response.status_code == 200
    assert _stage_rows(response.text) == {"5,001.000 kg", "5,003.000 kg"}


@pytest.mark.asyncio
async def test_ticking_two_stages_counts_each_submission_once(
    admin_client, three_stages, two_entries_one_stage
):
    """**The `IN`-inside-one-`EXISTS` shape, tested through the count.**

    `two_entries_one_stage` has two entries at Processing. With one `EXISTS`
    per ticked code, or with a join, it matches twice and the count says one
    more than the page draws - and sqladmin's `.scalars().unique()` means the
    duplicate never shows on screen to give it away.
    """
    response = await admin_client.get(
        "/admin/submissions/list?stage=subview_processing&stage=subview_retail"
    )
    assert response.status_code == 200
    body = response.text

    assert body.count("8,642.000 kg") == 1
    #: The seeded two-entry submission (processing + retail), the two-entries-
    #: at-one-stage one, and the single-stage processing and retail rows.
    assert _reported_count(body) == 4, (
        "the count disagrees with the rows for a multi-stage submission"
    )


@pytest.mark.asyncio
async def test_ticking_two_mass_bands_is_their_union(admin_client, admin_app, seeded):
    """Adjacent bands tile, so ticking both is one continuous range - and a row
    on the shared boundary belongs to exactly one of them, so it appears once
    rather than being counted twice."""
    with admin_app.state.session_factory() as session:
        processing = session.scalar(
            select(Sector).where(Sector.code == "subview_processing")
        )
        landfill = session.scalar(
            select(Destination).where(Destination.code == "subview_landfill")
        )
        made = []
        for mass in ("50.000", "1000.000", "20000.000"):
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
                    qty_kg=Decimal(mass),
                )
            )
            made.append(row.id)
        session.commit()

    try:
        response = await admin_client.get(
            "/admin/submissions/list?mass=100-1k&mass=1k-10k"
        )
        assert response.status_code == 200
        body = response.text

        #: 1,000.000 kg is on the boundary and belongs to the upper band; the
        #: seeded 3,001.500 kg is in the upper band too.
        assert "1,000.000 kg" in body and body.count("1,000.000 kg") == 1
        assert "3,001.500 kg" in body
        #: Outside both.
        assert "50.000 kg" not in body and "20,000.000 kg" not in body
    finally:
        with admin_app.state.session_factory() as session:
            session.execute(Submission.__table__.delete().where(Submission.id.in_(made)))
            session.commit()


@pytest.mark.asyncio
async def test_two_different_filters_narrow_together(admin_client, three_stages):
    """AND across controls, OR within one. Two stages ticked and a mass band
    that only one of them falls in returns that one."""
    response = await admin_client.get(
        "/admin/submissions/list?stage=subview_processing&stage=subview_hosp&mass=1k-10k"
    )

    assert response.status_code == 200
    #: All three stage rows are in the 1k-10k band, so the band alone does not
    #: narrow this - the stages do. Asserted against the third row, which the
    #: stage filter excludes.
    assert _stage_rows(response.text) == {"5,001.000 kg", "5,003.000 kg"}


@pytest.mark.asyncio
async def test_an_unknown_value_is_ignored_rather_than_matching_nothing(
    admin_client, three_stages
):
    """A hand-edited query string, or a sector deleted since the link was
    bookmarked. Ignoring the unknown code and honouring the known one is what
    keeps a stale bookmark useful; matching nothing would show an empty table
    that reads as "there are no submissions"."""
    response = await admin_client.get(
        "/admin/submissions/list?stage=subview_processing&stage=no-such-sector"
    )

    assert response.status_code == 200
    assert _stage_rows(response.text) == {"5,001.000 kg"}


# --- the All box ------------------------------------------------------------


def _all_box_checked(form: str, name: str) -> bool:
    """Whether the All row of `name`'s dropdown is ticked.

    Matched on `value=""` - the same marker the server keys on and the script
    finds by - rather than on the `kc-all` class, which is presentational and
    could be renamed without changing a thing about the behaviour."""
    match = re.search(
        rf'<input type="checkbox" name="{name}" value=""\s*\n?\s*(checked)?>', form
    )
    assert match, f"the {name} dropdown has no All row"
    return match.group(1) is not None


@pytest.mark.asyncio
async def test_every_multi_select_offers_an_all_row_at_the_top(admin_client, seeded):
    """**A panel of entirely unticked boxes reads as "nothing selected, so
    nothing will show" - which is the opposite of what it means.**

    The table is unfiltered in that state, and the summary does say "Any
    stage", but the summary is behind the click that opened the panel. The All
    row makes the everything-state something a reader can see rather than
    infer.

    It comes first: a reader scanning down the list has to meet it before the
    specific values, or it is just another value near the top.
    """
    response = await admin_client.get("/admin/submissions/list")
    assert response.status_code == 200
    form = _filter_form(response.text)

    for name in ("stage", "mass"):
        assert _all_box_checked(form, name), (
            f"{name} has an All row but it is not ticked on an unfiltered table"
        )
        panel = re.search(
            rf'<input type="checkbox" name="{name}".*?</div>', form, re.S
        ).group(0)
        first = re.search(r'value="([^"]*)"', panel).group(1)
        assert first == "", f"{name}'s All row is not the first thing in the panel"


@pytest.mark.asyncio
async def test_the_all_row_unticks_once_something_specific_is_chosen(
    admin_client, seeded
):
    """The affirmative half. An All box that were always ticked would satisfy
    the test above while telling the reader nothing."""
    form = _filter_form(
        (await admin_client.get("/admin/submissions/list?stage=subview_retail")).text
    )

    assert not _all_box_checked(form, "stage")
    #: And the other dropdown, untouched, still says All - so this is about the
    #: control the reader used rather than about the page.
    assert _all_box_checked(form, "mass")


@pytest.mark.asyncio
async def test_ticking_all_shows_everything_even_beside_a_specific_value(
    admin_client, three_stages
):
    """**The state scripting-off can produce, and the reason All wins.**

    `filter-bar.js` unticks the specific boxes when All is ticked, but nothing
    enforces that without it - a reader can submit both, and a bookmarked URL
    can carry both. Letting All win makes that request mean what the closed
    control says it means. Filtering the empty string out and honouring Retail
    instead would make the same click give two different answers depending on
    whether a script happened to load.
    """
    response = await admin_client.get(
        "/admin/submissions/list?stage=&stage=subview_retail"
    )

    assert response.status_code == 200
    assert _stage_rows(response.text) == {
        "5,001.000 kg",
        "5,002.000 kg",
        "5,003.000 kg",
    }, "All did not win over the specific value ticked beside it"


@pytest.mark.asyncio
async def test_the_bar_still_reads_as_unfiltered_when_all_wins(
    admin_client, three_stages
):
    """The controls have to agree with the table they sit above. If All wins in
    the query it has to win on the page: drawing Retail as ticked over a table
    showing every stage is the contradiction this pair of assertions exists to
    prevent."""
    form = _filter_form(
        (
            await admin_client.get("/admin/submissions/list?stage=&stage=subview_retail")
        ).text
    )

    assert _all_box_checked(form, "stage")
    ticked = re.findall(
        r'<input type="checkbox" name="stage" value="([^"]+)"\s*\n?\s*checked', form
    )
    assert ticked == [], f"a specific stage is still drawn as ticked: {ticked}"


@pytest.mark.asyncio
async def test_the_filter_script_is_served(admin_client):
    """A `<script src>` pointing at a 404 leaves the boxes contradicting each
    other after a click, which is the thing it exists to prevent."""
    response = await admin_client.get("/admin/static/filter-bar.js")

    assert response.status_code == 200
    assert 'value=""' in response.text, (
        "the script no longer finds the All box the way the server marks it"
    )


@pytest.mark.asyncio
async def test_the_all_row_is_translated(admin_client, seeded):
    """It reuses each control's empty label - "Any stage", "Any amount" - so
    the row and the closed summary say the same words. Those keys are already
    in the catalogue; this fails if the All row is given prose of its own and
    that prose is not."""
    form = _filter_form((await admin_client.get("/admin/submissions/list?lang=zh")).text)

    assert form.count("全部环节") >= 2, "the All row and the summary disagree in Chinese"
    assert form.count("不限数量") >= 2


@pytest.mark.parametrize("language", ["zh"])
def test_every_string_the_bar_can_render_has_a_translation(language):
    """**Neither i18n coverage test can see these strings, and five shipped
    untranslated before this one existed.**

    `test_i18n.py::test_our_own_templates_are_translated` scans templates for
    `_("literal")`; these arrive as `_(control.title)` and as
    `_("%(count)s selected", count=...)`, and its regex matches neither - the
    first has no literal, the second has a second argument after the closing
    quote. `test_every_view_name_and_menu_category_is_translated` reads `name`,
    `name_plural`, `category` and `form_args`, none of which a filter spec has.

    So the check is derived from `CONTROL_SPECS` rather than from a hand-kept
    list: a filter added later is covered without anyone remembering to add it
    here, which is the property the two tests above lack. The stage options are
    excluded because they are taxonomy rows - staff-authored names out of the
    database, not interface copy, and translating them is what §8's
    "never translate data" rule forbids.
    """
    from admin import i18n
    from admin.submission_views import CONTROL_SPECS

    strings = i18n.catalogue(language).strings
    expected = {"From", "To", "Apply", "Clear", "%(count)s selected"}
    for spec in CONTROL_SPECS:
        expected.add(spec["title"])
        expected.add(spec["empty_label"])
        for _value, label in spec["options"] or []:
            expected.add(label)

    missing = sorted(key for key in expected if key not in strings)
    assert not missing, (
        f"{len(missing)} filter-bar string(s) have no {language} translation, and "
        f"neither i18n coverage test can see them: {missing}"
    )
