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
#: The half of the mock block that changed with the flag itself, and the half
#: a reworded paragraph must not quietly drop: the two directions are not the
#: same size, and clearing no longer means cloning. Two phrases rather than
#: one because the block would still read plausibly with either missing.
MOCK_DIRECTIONS = "are not the same size"
MOCK_NO_CLONE = "do not need to clone a set to clear its flag"
PREVENTION = "as a refusal rather than as a rule"
FORMULA = "it found eight disagreements"
DRY_RUN = "twenty calculations that never happened"

#: Blocks that no ModelView declares, with the template that includes them.
#: `DryRunView` and `GettingStartedView` are `BaseView`s with hand-written
#: templates, so their blocks are included directly rather than through
#: `guidance_blocks`.
HAND_INCLUDED = {
    "dry_run_purpose.html": "brand/dry_run.html",
    #: Split out of `dry_run_purpose.html` so it could be rendered where a
    #: `<details>` cannot swallow it - see `test_the_public_calculator_warning_
    #: is_not_behind_a_disclosure` below for what went wrong when it could.
    "dry_run_warning.html": "brand/dry_run.html",
    # The first-run walkthrough (tests/admin/test_getting_started.py covers
    # what it says and that the index links it; this file's orphan test is
    # what notices if its page stops including it at all).
    "getting-started.html": "brand/getting_started.html",
}


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
async def test_the_factor_set_screen_explains_how_the_flag_moves(admin_client):
    """The half that changed when the flag came off the edit form.

    The block used to end with "untick Is Mock on the draft, then publish",
    and that sequence is now impossible in the panel and wrong as advice: it
    forces a clone, and therefore a new version label, for a change in which
    not one factor value differs. Both phrases are asserted because the block
    would still read as a plausible explanation with either one missing - one
    says the two directions carry different friction, the other says the clone
    is no longer required, and a staff member who is told only the first still
    clones.
    """
    body = _flat(await admin_client.get("/admin/factor-set/list"))

    assert MOCK_DIRECTIONS in body, "the block no longer distinguishes the two directions"
    assert MOCK_NO_CLONE in body, "the block no longer says a clone is unnecessary"


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


def _extends_chain(start: str) -> list[str]:
    """The `{% extends %}` chain from a brand template up to its root.

    Followed rather than matched on one literal line, because the chain has
    more than one link in it since the list-table scrollport landed:

        sqladmin/list.html
          brand/list_table.html      the scrollport rules
            brand/model_list.html    + page-level guidance
              brand/staff_list.html    + its own `model_menu_bar`
              brand/ip_block_list.html + its own `model_menu_bar`

    Asserting `'{% extends "sqladmin/list.html" %}' in model_list.html` was
    the original form of the test below and it failed on that restructure
    while the property it guards - that nothing in the chain REPLACES
    sqladmin's page - was never broken for a moment. Walking the chain keeps
    the guard and drops the coincidence: a link inserted anywhere still has
    to end at sqladmin's own template, and a template that stops extending
    at any depth still fails.
    """
    brand = GUIDANCE_DIR.parent
    chain, seen, current = [], set(), start
    while current.startswith("brand/"):
        assert current not in seen, f"template inheritance loops at {current}"
        seen.add(current)
        text = (brand / Path(current).name).read_text(encoding="utf-8")
        match = re.search(r'{%\s*extends\s*"([^"]+)"\s*%}', text)
        assert match, f"{current} extends nothing; it replaces the page instead"
        current = match.group(1)
        chain.append(current)
    return chain


@pytest.mark.parametrize(
    "template",
    ["brand/model_list.html", "brand/list_table.html",
     "brand/ip_block_list.html", "brand/staff_list.html"],
)
def test_the_list_pages_still_extend_sqladmin_s_own(template):
    """The override adds to the list page; it must not replace it.

    `brand/model_list.html` is every audited view's `list_template`, and
    `brand/list_table.html` - which it now extends - is AuditLogAdmin's. If
    either stopped reaching `sqladmin/list.html`, search, filters, pagination
    and the bulk-action dropdown would vanish from fifteen screens at once,
    and every test above would still pass.
    """
    chain = _extends_chain(template)

    assert chain[-1] == "sqladmin/list.html", (
        f"{template} no longer reaches sqladmin's own list page: {chain}"
    )
    assert ModelView.list_template != AuditedModelView.list_template, (
        "the base no longer overrides sqladmin's list template"
    )


@pytest.mark.asyncio
async def test_the_list_page_still_has_its_table_and_filters(admin_client):
    """The other half of the test above, on a rendered page rather than a
    template file — the assertion that would actually notice."""
    body = _flat(await admin_client.get("/admin/factor-upstream/list"))

    assert PREVENTION in body
    # `<table`, not `table`: every sqladmin page loads `tabler.min.css`, so
    # the bare word is in the markup of a page with no table on it at all.
    assert "<table" in body, "the list page lost its table"
    assert "Filter" in body, "the list page lost sqladmin's filter panel"
    assert "Actions" in body, "the list page lost the bulk-action dropdown"


def test_the_public_calculator_warning_is_not_behind_a_disclosure():
    """**Present in the markup is not the same as readable, and this file had
    been asserting the first while meaning the second.**

    `test_the_dry_run_screen_explains_what_it_is_for` checks that the §8.2
    warning's text appears on the page. A redesign moved the whole guidance
    block inside a closed `<details>`; the substring was still there, the test
    stayed green, and the paragraph the page exists for - *"twenty calculations
    that never happened, sitting inside a figure that may be quoted in public"*
    - went behind a click.

    So this asserts the structure rather than the text: the warning is included
    at a point in `brand/dry_run.html` that no `<details>` has opened.

    Read from the template rather than driven over HTTP on purpose. A rendered
    page would need the disclosure's state inferred from CSS or from a browser,
    and the question here is about where the include sits, which the source
    answers exactly.
    """
    page = (GUIDANCE_DIR.parent / "dry_run.html").read_text(encoding="utf-8")

    #: Jinja comments stripped FIRST. The comment above the include explains
    #: what went wrong by naming `<details>`, and counting raw occurrences read
    #: that as an open disclosure - this assertion failed on a template that was
    #: correct. Sixth unanchored-match defect in this repository; the previous
    #: ones are noted beside `_reported_count` in test_submission_views.py and
    #: `_ADMIN_PATH` in test_operator_guidance.py.
    markup = re.sub(r"\{#.*?#\}", "", page, flags=re.S)

    include = markup.index("guidance/dry_run_warning.html")
    before = markup[:include]
    #: Every `<details>` opened before the include must also have been closed
    #: before it. Counting tags is enough - these templates never nest one
    #: disclosure inside another, and a future one that did would make this
    #: over-strict rather than blind, which is the right way round.
    assert before.count("<details") == before.count("</details>"), (
        "the public-calculator warning is inside a <details> - it has to be "
        "readable without a click, which is the whole of contract 8.2's reason "
        "for this page"
    )

    #: And it is still on the page at all. Without this the assertion above
    #: passes trivially for a page that stopped including it.
    assert "guidance/dry_run_warning.html" in page


# --- Task: admin guidance disclosure ----------------------------------------
#
# The four card-level blocks above are reference material a staff member goes
# looking for (how the lifecycle works, how to write a formula, ...), not a
# warning aimed at someone who is not looking for it - the opposite case from
# `dry_run_warning.html` above - so folding them behind a closed `<details>`
# is the right call for these four and the wrong one for that file. See each
# block's own header comment for the specific reasoning.
#
# **Visibility, not presence.** Every assertion below is on the `<details>`
# element itself - whether it carries an `open` attribute, which is the one
# thing that actually decides whether a browser shows the content - and
# never on whether a phrase merely occurs somewhere in the markup. A test
# that asserted presence here would pass identically whether the block were
# open, closed, or had never been wrapped at all.

#: Which of the two already-pinned phrases above (LIFECYCLE, MOCK, PREVENTION,
#: FORMULA) lives in each folded block, and which page renders it - reused
#: to drive both a template-source check and a real HTTP round-trip.
FOLDED_BLOCKS = {
    "factor_set_lifecycle.html": (LIFECYCLE, "/admin/factor-set/list"),
    "mock_data.html": (MOCK, "/admin/factor-set/list"),
    "prevention_zero.html": (PREVENTION, "/admin/factor-upstream/list"),
    "writing_a_formula.html": (FORMULA, "/admin/formula/list"),
}


def _enclosing_details_tag(markup: str, phrase: str) -> str:
    """The opening `<details ...>` tag that `phrase` sits inside, verbatim.

    `markup` is whitespace-collapsed first, the same reason `_flat` above
    exists: these blocks are wrapped prose, so a pinned phrase such as
    "clone, edit, publish" is split across source lines about as often as
    not. Walks every `<details`/`</details>` before `phrase` and keeps the
    last unmatched open, the same depth-blind counting
    `test_the_public_calculator_warning_is_not_behind_a_disclosure` uses
    above - correct here for the same reason: none of these blocks nest one
    disclosure inside another.
    """
    markup = re.sub(r"\s+", " ", markup)
    index = markup.index(phrase)
    opens = [m.start() for m in re.finditer(r"<details\b[^>]*>", markup[:index])]
    closes = [m.start() for m in re.finditer(r"</details>", markup[:index])]
    assert len(opens) > len(closes), (
        f"{phrase!r} is not inside any <details> at all - it has not been folded"
    )
    start = opens[-1]
    end = markup.index(">", start) + 1
    return markup[start:end]


@pytest.mark.parametrize("template_name,pair", sorted(FOLDED_BLOCKS.items()))
def test_a_card_guidance_block_is_folded_and_closed_in_its_template(template_name, pair):
    """The mutation this guards against: someone reopens the block, or wraps
    it in a `<details open>` "to be safe" and quietly undoes Change 1.

    Read from the template source, not over HTTP, for the same reason the
    dry-run check above is: the question is where the include sits and what
    attributes the tag carries, and the source answers that directly.
    """
    phrase, _route = pair
    markup = (GUIDANCE_DIR / template_name).read_text(encoding="utf-8")
    markup = re.sub(r"\{#.*?#\}", "", markup, flags=re.S)  # strip comments first - see above
    tag = _enclosing_details_tag(markup, phrase)
    assert "guidance-disclosure" in tag, f"{template_name}'s <details> lost its class: {tag!r}"
    assert not re.search(r"\bopen\b", tag), (
        f"{template_name}'s guidance is inside an OPEN <details> - a visitor "
        f"sees exactly what they would if it had never been folded: {tag!r}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("template_name,pair", sorted(FOLDED_BLOCKS.items()))
async def test_a_card_guidance_block_renders_closed_on_its_real_page(admin_client, template_name, pair):
    """The template-source check above cannot see template composition bugs -
    `_guidance.html`'s loop, or a `model_view` that stopped declaring the
    block - so this drives the same real route `test_guidance.py`'s other
    HTTP tests use and checks the same thing on the response that actually
    reaches a browser.
    """
    phrase, route = pair
    body = (await admin_client.get(route)).text
    tag = _enclosing_details_tag(body, phrase)
    assert "guidance-disclosure" in tag
    assert not re.search(r"\bopen\b", tag), (
        f"{route} renders {template_name}'s guidance already open: {tag!r}"
    )


def test_getting_started_is_not_folded():
    """The one block this task deliberately leaves alone.

    `getting-started.html` is the *entire* content of its own page
    (`brand/getting_started.html`) - there are no controls on that page to
    scroll past, so Change 1's reason for folding the other four does not
    apply here, and folding it would hide the walkthrough a reader navigated
    to this page specifically to read. This pins that decision rather than
    leaving it to be silently undone by a future "fold everything in
    guidance/" pass.
    """
    page = (GUIDANCE_DIR / "getting-started.html").read_text(encoding="utf-8")
    markup = re.sub(r"\{#.*?#\}", "", page, flags=re.S)
    assert "<details" not in markup, (
        "getting-started.html has been folded behind a <details> - it is the "
        "whole of its own page, and there is nothing left to read if it starts "
        "closed"
    )
