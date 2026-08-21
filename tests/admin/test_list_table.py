"""The list table scrolls in its own right, on every list page there is.

WHAT THIS FILE CAN AND CANNOT PROVE, said first because the distinction is
the whole reason the defect it guards survived to be reported by the owner.

The defect was never that markup was missing. sqladmin has always wrapped its
list table in ``<div class="table-responsive">`` and Tabler has always given
that ``overflow-x: auto``, so a test asserting "the wrapper is there and it
scrolls" would have passed on every day the panel has existed - including the
day a reader could not reach the right-hand half of /admin/factor-upstream.
What the wrapper never had was a HEIGHT, so the horizontal scrollbar the
browser drew for it was drawn at the bottom of a 4656px box, roughly 4500px
below the fold. Content that is reachable only by a gesture nobody can see is
not reachable.

So the reachability itself was measured in a browser, not here. Chromium via
Playwright against the running stack on :18080 with the ReFED comparison set
loaded (764 upstream rows, 1728 downstream), at 1920x1080, 1440x900,
1280x900, 1024x768, 768x800 and 390x700, on all eighteen list pages:

* before, /admin/factor-upstream at 1280x900 - scrollport 682x4656, the last
  column (`data_quality`) laid out at x=2314..2452 against a port ending at
  x=947, and the port's own bottom edge, with its scrollbar, at y~5439
  against a 900px viewport;
* after - scrollport 682x630, and scrolling it to its right-hand end brings
  that column's right edge to exactly the port's right edge (947 == 947) on
  every page that overflows and at every one of those six sizes; scrolling it
  to its bottom moves 4026px of rows past a header row that does not move
  (y=783 before and after), with `document.elementFromPoint` over the header
  returning the `TH` rather than the row sliding under it.

What is left for this file is delivery: that those rules reach every list
page, and that each of the four survives as the rule it was measured as. That
is not a formality - the coverage test below is what caught the one screen the
first version of the fix missed. ``AuditLogAdmin`` is a plain ``ModelView``
rather than an ``AuditedModelView`` (a view onto the audit trail has nothing
to audit), so it inherited none of the brand list templates, and
/admin/audit-log - fifty rows a page of timestamps, actors and table names -
would have been the one table still clipped.
"""

import re

import pytest
from sqladmin import ModelView

pytestmark = pytest.mark.db

#: Every rule brand/_list_table_css.html adds, as the pattern that has to
#: survive for the browser measurement above to still describe the panel.
#:
#: ANCHORED TO THE SELECTOR, NOT ONLY TO THE DECLARATION. `position: sticky`
#: appears in this project's other stylesheet too, and a pattern that only
#: looked for a declaration would pass on a page that happened to carry one
#: anywhere - which is how three mutants survived on this branch inside a
#: week. Each pattern below names the selector, then walks to the declaration
#: within the same rule body (`[^}]*`), so moving the declaration to another
#: rule fails it.
SCROLLPORT = re.compile(
    r"\.table-responsive\s*\{[^}]*max-height:\s*max\(\s*70vh\s*,\s*16rem\s*\)\s*;"
    r"[^}]*overflow:\s*auto\s*;",
    re.S,
)
#: The viewport unit specifically. A pixel clamp would scroll just as well and
#: would lose the property the measurement turned on: that the port, and
#: therefore its horizontal scrollbar, fits on the screen at once.
VIEWPORT_UNIT = re.compile(r"max-height:\s*max\(\s*70vh\s*,")
STICKY_HEADER = re.compile(
    r"\.table-responsive\s*>\s*table\s*>\s*thead\s*>\s*tr\s*>\s*th\s*\{"
    r"[^}]*position:\s*sticky\s*;[^}]*top:\s*0\s*;",
    re.S,
)
#: An opaque ground and a z-index, in the same rule. Without either, the
#: pinned row is a row you can see the table through.
STICKY_HEADER_OPAQUE = re.compile(
    r"\.table-responsive\s*>\s*table\s*>\s*thead\s*>\s*tr\s*>\s*th\s*\{"
    r"[^}]*z-index:\s*1\s*;[^}]*background:\s*#f9fafb\s*;",
    re.S,
)
#: The four background layers, and - the part that makes them an affordance
#: rather than a decoration - two attached `local` and two attached `scroll`.
#: The covers must travel with the content and the shadows must not; make all
#: four the same and the shadow is painted at both edges permanently, which
#: says "there is more this way" in the one place there is not.
SHADOW_COVERS = re.compile(
    r"linear-gradient\(to right,\s*#fff[^;]*?no-repeat local,"
    r"\s*linear-gradient\(to left,\s*#fff[^;]*?no-repeat local,",
    re.S,
)
SHADOW_SHADOWS = re.compile(
    r"radial-gradient\(farthest-side at 0 50%[^;]*?no-repeat scroll,"
    r"\s*radial-gradient\(farthest-side at 100% 50%[^;]*?no-repeat scroll\s*;",
    re.S,
)
#: The filter sidebar, which sqladmin's own main.css pins at
#: `width: 300px; flex-shrink: 0` in a flex row that never wraps. Measured
#: before this rule: the scrollport was 194px wide at a 768px viewport and
#: 56px at 390px. A 56px scrollport is not a component anybody can scroll.
SIDEBAR_WRAPS = re.compile(
    r"@media\s*\(max-width:\s*991\.98px\)\s*\{[^@]*"
    r"\.container-fluid\s+\.d-flex\.min-w-0\s*\{[^}]*flex-wrap:\s*wrap\s*;",
    re.S,
)

RULES = {
    "the scrollport clamp": SCROLLPORT,
    "the viewport unit in the clamp": VIEWPORT_UNIT,
    "the pinned header row": STICKY_HEADER,
    "the pinned header's opaque ground": STICKY_HEADER_OPAQUE,
    "the scroll-shadow covers": SHADOW_COVERS,
    "the scroll-shadow shadows": SHADOW_SHADOWS,
    "the filter sidebar wrapping below md": SIDEBAR_WRAPS,
}


@pytest.fixture()
def list_identities(admin_app) -> list[str]:
    """Every ``/admin/{identity}/list`` the running panel serves.

    Read off the live ``Admin`` object rather than from a hand-written list,
    which is the point: a screen added next semester is covered the day it is
    registered, and the one screen that was missing the fix was missing it
    precisely because it was not the kind of view anybody would have thought
    to add to a list by hand.
    """
    from admin.modelviews import AuditLogAdmin

    return sorted(
        view.identity
        for view in AuditLogAdmin._admin_ref.views
        if isinstance(view, ModelView)
    )


def test_the_sweep_covers_every_list_screen(list_identities):
    """Guards the coverage test below against passing for the wrong reason.

    ``test_every_list_page_carries_the_scrollport_rules`` asserts that a list
    of failures is empty, and the cheapest way for that to be true is for the
    fixture to have found no screens at all. Eighteen is the same count
    ``tests/admin/test_role_matrix.py`` holds the panel to, and this asserts
    it independently so that a view lost from the registry fails both.
    """
    assert len(list_identities) == 18, (
        f"expected eighteen list screens, found {len(list_identities)}: "
        f"{list_identities}"
    )
    assert "audit-log" in list_identities, (
        "the one screen that is not an AuditedModelView is the one this file "
        "exists to keep in the sweep"
    )


@pytest.mark.asyncio
async def test_every_list_page_carries_the_scrollport_rules(
    admin_client, list_identities
):
    """All eighteen, not the three that were reported.

    The report named upstream, downstream and constants because those are the
    three the owner happened to open. The clipping is a property of the one
    table markup sqladmin renders for every ``ModelView``, so a fix applied to
    three screens would be a fix that leaves fourteen broken and looks done.
    """
    missing: list[str] = []
    for identity in list_identities:
        response = await admin_client.get(f"/admin/{identity}/list")
        assert response.status_code == 200, (
            f"/admin/{identity}/list returned {response.status_code}"
        )
        for name, pattern in RULES.items():
            if not pattern.search(response.text):
                missing.append(f"/admin/{identity}/list is missing {name}")
    assert not missing, "\n".join(missing)


@pytest.mark.asyncio
async def test_the_rules_are_not_trapping_the_page_scroll(admin_client):
    """`overscroll-behavior` on the X axis only, and this is deliberate.

    A horizontal flick that runs off the end of a table this wide is otherwise
    handed to the browser as a back-gesture, which loses the page. Containing
    the Y axis as well would be the worse bug in the other direction: a wheel
    that reached the last row would stop, and the reader would be inside a box
    that had swallowed the page's own scrolling. A table that traps the page
    is worse than the clipping this whole change exists to fix.
    """
    body = (await admin_client.get("/admin/factor-upstream/list")).text

    assert re.search(r"overscroll-behavior-x:\s*contain\s*;", body), (
        "the horizontal end-stop is gone; a flick past the last column now "
        "navigates back"
    )
    assert not re.search(r"overscroll-behavior(-y)?:\s*(contain|none)\s*;", body), (
        "vertical overscroll is contained: the table has taken the page's "
        "scroll"
    )


@pytest.mark.asyncio
async def test_the_audit_log_takes_the_rules_without_the_guidance_mechanism(
    admin_client,
):
    """Why ``brand/list_table.html`` exists as a separate template.

    ``AuditLogAdmin`` points at the base of the chain rather than at
    ``brand/model_list.html``. Pointed at the latter it would render
    ``brand/_guidance.html``, which iterates ``model_view.guidance_blocks`` -
    an attribute only ``AuditedModelView`` declares - and a plain ``ModelView``
    would raise ``UndefinedError`` and return 500. The split is what lets a
    view take the scrollport without taking a mechanism it has no use for.
    """
    response = await admin_client.get("/admin/audit-log/list")

    assert response.status_code == 200
    assert SCROLLPORT.search(response.text), "the audit log lost the scrollport"
