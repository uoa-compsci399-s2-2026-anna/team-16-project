"""The first-run walkthrough: that it renders, and that it is findable.

`tests/admin/test_guidance.py` guards the five explanations that sit on the
screens they describe; this file guards the one page that exists to be read
before any of them, by somebody who has just been handed the system and has
nobody to ask.

**What a test of prose can and cannot do**, restated here because this file
is the third to run into it. It can hold the page to rendering, to naming its
four steps *in order*, to still linking the three screens it sends people to,
and to being reachable from the index by somebody who does not know it
exists. It cannot check that any sentence is true. Three of the claims in
this page are true because code enforces them today - the panel refuses an
account resetting its own authenticator, the panel refuses every screen until
the issued password is changed and one authenticator is enrolled, and the
seeded factor set is marked as mock - and if any of those is relaxed this
page becomes wrong with every test here still green. See the task report.

Asserted against whitespace-collapsed markup for the reason
test_guidance.py's own docstring gives: this is wrapped prose, and a phrase
in the source sits on one line about half the time.
"""

import re

import pytest

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

#: One phrase per step, in the order the page has to put them. The ordering
#: assertion below is the point of this list: the brief's whole content is
#: that nothing else is safe before the second factor exists, and a page that
#: rendered these four in another order would pass every "is it present"
#: check ever written.
STEPS = [
    "Change the password you were issued",
    "Enrol an authenticator, and keep a way back in",
    "Find the numbers the calculator is using",
    "Run one calculation, to see the system answer",
]


def _flat(response) -> str:
    return re.sub(r"\s+", " ", response.text)


def _below_the_menu(response, marker: str) -> str:
    """The page from `marker` onward - which is everything after the menu.

    **Two assertions in this file passed for the wrong reason without this,
    and mutation testing is the only reason that is known.** sqladmin's
    layout renders the whole navigation before the content block, and the
    navigation contains a link to every screen - including
    `/admin/getting-started` and `/admin/security`. So `"/admin/security" in
    body` is true of every page in this panel, and replacing this page's own
    links with `#` left both tests green. It is the same shape as Task 5's
    `"table" in body`, which was true of every sqladmin page because they all
    load `tabler.min.css`.

    Slicing at a phrase that only the card carries is what makes the
    assertions be about the card. The marker itself is asserted, so a
    rewrite that removes it fails here rather than silently widening the
    slice to the whole page.
    """
    body = _flat(response)
    assert marker in body, f"the page no longer carries {marker!r}"
    return body[body.index(marker):]


async def test_the_walkthrough_renders(admin_client):
    response = await admin_client.get("/admin/getting-started")

    assert response.status_code == 200
    body = _flat(response)
    for step in STEPS:
        assert step in body, f"the walkthrough no longer has its step: {step!r}"


async def test_the_four_steps_are_in_order(admin_client):
    """The order is the content.

    Nothing else on the list is safe to do before an authenticator exists,
    which is why enrolment is step 2 and not an appendix. A page that lists
    the same four things in a different order is a different document.
    """
    body = _flat(await admin_client.get("/admin/getting-started"))

    positions = [body.index(step) for step in STEPS]

    assert positions == sorted(positions), (
        f"the walkthrough's steps are out of order: {positions}"
    )


async def test_it_sends_the_reader_to_the_three_screens_it_names(admin_client):
    """Every step ends somewhere else, which is the whole design of the page.

    A link that stops resolving fails at render (Jinja's `url_for` raises
    rather than emitting an empty href), so the assertion that matters is
    that the *hrefs are still there* - a rewrite that drops one leaves a step
    telling somebody to go to a screen without saying where it is.

    Asserted below the menu, which carries a link to all three of these on
    every page in the panel - see `_below_the_menu`.
    """
    card = _below_the_menu(
        await admin_client.get("/admin/getting-started"), "Four things, in this order"
    )

    assert "/admin/security" in card, "step 2 no longer links Your security"
    assert "/admin/factor-set/list" in card, "step 3 no longer links Factor sets"
    assert "/admin/try" in card, "step 4 no longer links Try a scenario"


async def test_the_panel_index_links_it(admin_client):
    """Findable without being told it is there.

    sqladmin's own index is an empty page with a menu; the first thing a new
    staff member sees after saving their recovery codes. The override that
    puts this link on it is one file in a directory of template overrides,
    and the thing most likely to happen to it is a refactor that restores
    sqladmin's own.

    Asserted on the card and not on the page: the menu links this from every
    screen, so `"/admin/getting-started" in body` is true of an index that
    has had its card removed entirely. That mutation survived until this
    test was written this way.
    """
    response = await admin_client.get("/admin/")

    assert response.status_code == 200
    card = _below_the_menu(response, "New here?")
    assert "/admin/getting-started" in card, "the panel index no longer links it"


async def test_it_is_in_the_navigation_of_every_page(admin_client):
    """The second way in, and the one that works from wherever somebody is.

    Checked on a screen that is not the index, because the menu is rendered
    by sqladmin's layout and the index test above would pass on its own copy.
    """
    body = _flat(await admin_client.get("/admin/sector/list"))

    assert "/admin/getting-started" in body, "the walkthrough left the menu"


async def test_the_walkthrough_is_not_pasted_onto_other_screens(admin_client):
    """Guards the tests above against passing for the wrong reason.

    If this page's block were included by a shared layout - the mistake
    Task 5's mutation I describes - every assertion in this file would pass
    while the page itself carried nothing of its own, and fourteen screens
    would each open with a first-run walkthrough. The menu link is expected
    on `sector/list`; the four steps are not.
    """
    body = _flat(await admin_client.get("/admin/sector/list"))

    for step in STEPS:
        assert step not in body, (
            f"a screen that is not the walkthrough is showing it: {step!r}"
        )


async def test_a_plain_staff_member_can_read_it(staff_client):
    """It is not an administrator's page.

    Everything it sends the reader to is reachable by `staff` - their own
    security screen, the factor sets, the dry run - and the person most
    likely to be handed this system without an explanation is the one with
    the fewest permissions.
    """
    response = await staff_client.get("/admin/getting-started")

    assert response.status_code == 200
    assert STEPS[1] in _flat(response)
