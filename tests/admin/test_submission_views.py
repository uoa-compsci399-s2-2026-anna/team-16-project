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
