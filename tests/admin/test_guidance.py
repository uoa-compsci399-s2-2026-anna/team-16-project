"""The five page-level explanations, and the guarantee that they render.

`tests/admin/test_field_help.py` covers the other half of this panel's
documentation: every editable field says what it is. A field description
cannot say what *order* to do things in, and every one of the five blocks
this file guards is a sequence whose wrong order produces wrong numbers
rather than an error message — a published factor set edited in place, a
general upstream row with no `prevention` counterpart, the mock warning
switched off before the real factors are in, a formula naming a methane
horizon, a hundred test calculations run through the public calculator and
counted in the statistics the client publishes.

**Why a test for prose.** The team hands over at the end of semester and
nobody is left to ask. A block deleted in a template refactor takes with it
the only written account of why the panel refuses something, and nothing
else in the tree fails when it goes. That is exactly what happened to
`ip_block`'s expiry warning, which Task E-8 wrote and Task 4 found pinned by
nothing.

Asserted on a distinguishing phrase from each block rather than on whole
paragraphs, so rewording the copy does not fail this while deleting it does.
Every assertion runs against whitespace-collapsed markup (`_flat` below):
these blocks are wrapped prose, so a phrase in the source is split across
lines about as often as not, and a test that only passed when a sentence
happened to sit on one line would fail on the next reflow.
"""

import re
from pathlib import Path

import pytest
from sqladmin import ModelView

import admin.factor_views  # noqa: F401  (defines the declaring views)
from admin.modelviews import AuditedModelView

pytestmark = pytest.mark.db

GUIDANCE_DIR = Path(__file__).resolve().parents[2] / "admin" / "templates" / "brand" / "guidance"

#: One phrase per block, chosen to be the sentence the block exists for
#: rather than an incidental one.
LIFECYCLE = "clone, edit, publish"
MOCK = "the only thing holding them up"
PREVENTION = "as a refusal rather than as a rule"
FORMULA = "it found eight disagreements"
DRY_RUN = "twenty calculations that never happened"

#: Blocks that no ModelView declares, with the template that includes them.
#: `DryRunView` is a `BaseView` with a hand-written template, so its block is
#: included directly rather than through `guidance_blocks`.
HAND_INCLUDED = {"dry_run_purpose.html": "brand/dry_run.html"}


@pytest.fixture
def session(_committed_session):
    """See tests/admin/conftest.py's `_committed_session` docstring for why
    every file that drives `admin_client` re-exposes it locally."""
    return _committed_session


def _flat(response) -> str:
    """One response's markup with every run of whitespace collapsed."""
    return re.sub(r"\s+", " ", response.text)


def _declaring_views() -> dict[str, list[str]]:
    """Every AuditedModelView subclass that declares guidance, by class name.

    Read off the classes rather than a hand-kept list, so a fifth block
    added to a sixth view is covered by the two whole-set tests below
    without anybody remembering to add it here.
    """
    found: dict[str, list[str]] = {}
    stack = list(AuditedModelView.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        if getattr(cls, "guidance_blocks", None):
            found[cls.__name__] = list(cls.guidance_blocks)
    return found


@pytest.mark.asyncio
async def test_the_factor_set_screen_explains_the_lifecycle(admin_client):
    """Clone, edit, publish — and why a published set is never edited.

    On the list page because that is where Clone, Publish, Roll back and
    Archive are: `status` is not on the edit form (contract §8.2), so those
    four actions are the whole of this screen's behaviour and the order to
    use them in is not written anywhere a staff member would find it.
    """
    body = _flat(await admin_client.get("/admin/factor-set/list"))

    assert LIFECYCLE in body, "the factor-set screen no longer explains the lifecycle"
    assert "Roll back" in body and "Archive" in body


@pytest.mark.asyncio
async def test_the_factor_set_screen_explains_the_mock_flag(admin_client):
    """What `is_mock` does, and the sequence for turning it off.

    Open item O-1 is unresolved, so this is the procedure the panel's owners
    will need first: the client supplies real factors and somebody has to
    know that unticking one box is what removes the warning from every
    public page and every export at once.
    """
    body = _flat(await admin_client.get("/admin/factor-set/list"))

    assert MOCK in body, "the factor-set screen no longer explains the mock flag"


@pytest.mark.asyncio
async def test_the_upstream_screen_explains_the_prevention_row(admin_client):
    """O-7, as the refusal a staff member meets rather than a rule to recall.

    `publish_factor_set` (admin/factor_lifecycle.py) refuses a set whose
    general rows lack a `prevention` row at zero, naming the combinations.
    Nothing on this screen said what that message is asking for.
    """
    body = _flat(await admin_client.get("/admin/factor-upstream/list"))

    assert PREVENTION in body, "the upstream screen no longer explains prevention"


@pytest.mark.asyncio
async def test_the_formula_screen_explains_how_to_write_one(admin_client):
    """Where each value comes from, and that the panel and the engine agree.

    On the create page as well as the list, because that is where a formula
    is actually typed — the assertion below is on `create` for that reason,
    and `test_a_block_reaches_the_form_pages_too` holds the mechanism to all
    three routes.
    """
    body = _flat(await admin_client.get("/admin/formula/create"))

    assert FORMULA in body, "the formula screen no longer explains the agreement"
    assert "const_GWP_CH4" in body


@pytest.mark.asyncio
async def test_the_dry_run_screen_explains_what_it_is_for(admin_client):
    """Contract §8.2: persisting these runs would pollute public statistics.

    The page always said nothing is saved. It never said why that matters,
    or what testing on the public calculator instead would cost — which is
    the part that changes what somebody does.
    """
    body = _flat(await admin_client.get("/admin/try"))

    assert DRY_RUN in body, "the dry-run screen no longer explains what it is for"


@pytest.mark.asyncio
async def test_a_block_reaches_the_form_pages_too(admin_client, one_draft, session):
    """List, create and edit — not whichever one somebody happened to check.

    sqladmin renders those three routes from three separate templates, and
    the guidance include lives in a brand override of each. Two of the three
    keeping it is a likelier failure than none of them, and the person who
    needs the factor-set lifecycle explained is most often the one looking at
    a factor set's own edit form.
    """
    assert FORMULA in _flat(await admin_client.get("/admin/formula/list"))

    # `one_draft` only flushes (tests/admin/conftest.py's `_make_set`), and
    # `admin_client` reaches the app on a different connection.
    session.commit()
    edit = await admin_client.get(f"/admin/factor-set/edit/{one_draft.id}")

    assert edit.status_code == 200
    flat = _flat(edit)
    assert LIFECYCLE in flat, "the edit page lost its guidance"
    assert MOCK in flat, "the edit page lost its guidance"


@pytest.mark.asyncio
async def test_a_screen_that_declares_no_guidance_shows_none(admin_client):
    """Guards every assertion above against passing for the wrong reason.

    If the blocks were pasted into a shared layout, or the include were
    unconditional, all five tests above would pass while the mechanism they
    describe did not exist — and the next view would inherit four
    explanations that have nothing to do with it. `SectorAdmin` declares
    none, so its list page must carry none.
    """
    body = _flat(await admin_client.get("/admin/sector/list"))

    for phrase in (LIFECYCLE, MOCK, PREVENTION, FORMULA, DRY_RUN):
        assert phrase not in body, (
            f"a screen that declares no guidance is showing some: {phrase!r}"
        )


def test_every_declared_block_exists():
    """A path typed wrongly on a view is a 500 on that whole screen.

    Jinja raises `TemplateNotFound` at render time, which the HTTP tests
    above would catch for today's four blocks — but only for the pages they
    name. This covers the fifth view somebody adds without a test of its own.
    """
    declaring = _declaring_views()
    assert declaring, "no view declares guidance at all - the mechanism is unused"

    missing = [
        f"{view}: {path}"
        for view, paths in declaring.items()
        for path in paths
        if not (GUIDANCE_DIR.parent / path.removeprefix("brand/")).is_file()
    ]

    assert not missing, f"declared guidance templates that do not exist: {missing}"


def test_every_guidance_template_is_shown_somewhere():
    """A block written and never wired up is worse than no block.

    It reads as done in the diff and in the report, and nobody sees it. The
    same shape as test_field_help.py's registration test: coverage over what
    is wired up cannot notice what is not.
    """
    declared = {
        Path(path).name
        for paths in _declaring_views().values()
        for path in paths
    }
    orphans = []
    for template in sorted(GUIDANCE_DIR.glob("*.html")):
        if template.name in declared:
            continue
        includer = HAND_INCLUDED.get(template.name)
        if includer is None:
            orphans.append(template.name)
            continue
        host = GUIDANCE_DIR.parent / Path(includer).name
        if f"guidance/{template.name}" not in host.read_text(encoding="utf-8"):
            orphans.append(f"{template.name} (its host {includer} no longer includes it)")

    assert not orphans, (
        "these guidance blocks are not rendered on any screen: " + ", ".join(orphans)
    )


def test_the_list_pages_still_extend_sqladmin_s_own():
    """The override adds to the list page; it must not replace it.

    `brand/model_list.html` is now every audited view's `list_template`. If
    it ever stopped extending `sqladmin/list.html`, search, filters,
    pagination and the bulk-action dropdown would vanish from fourteen
    screens at once, and every test above would still pass.
    """
    brand = GUIDANCE_DIR.parent
    assert '{% extends "sqladmin/list.html" %}' in (
        brand / "model_list.html").read_text(encoding="utf-8")
    # IpBlockAdmin's own override has to reach the mechanism through it.
    assert '{% extends "brand/model_list.html" %}' in (
        brand / "ip_block_list.html").read_text(encoding="utf-8")

    views = [v for v in AuditedModelView.__subclasses__()]
    assert ModelView.list_template != AuditedModelView.list_template, (
        "the base no longer overrides sqladmin's list template"
    )
    assert views, "no view inherits AuditedModelView"


@pytest.mark.asyncio
async def test_the_list_page_still_has_its_table_and_filters(admin_client):
    """The other half of the test above, on a rendered page rather than a
    template file — the assertion that would actually notice."""
    body = _flat(await admin_client.get("/admin/factor-upstream/list"))

    assert PREVENTION in body
    assert "table" in body and "Filter" in body, (
        "the list page lost sqladmin's own furniture"
    )
