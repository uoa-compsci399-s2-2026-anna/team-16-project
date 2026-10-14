"""The two hand-drawn icon paths are symmetric about their own box.

**This file exists because a mutation survived the whole repository.** The browser
cases added with the icons measure the rendered *element's box* against the text beside
it -- which is the right question for "is the chevron centred on the word" and is what
the five reported defects were about. But every one of them is blind to where the ink is
drawn *inside* that box: moving the chevron's path from ``M 5 9 L 12 15 L 19 9`` to
``M 5 12 L 12 18 L 19 12`` shifts the arrowhead 2.2px down the page and leaves
``test_the_details_chevron_sits_on_the_centre_of_its_label``,
``test_the_header_chevron_sits_on_the_centre_of_the_card_name`` and
``test_the_tips_dismiss_icon_sits_in_the_centre_of_its_circle`` all green. Measured, not
predicted: that mutation was run against a rebuilt image and killed nothing.

That is the same class of defect the icons replaced. The old ``⌄`` sat below its label
because the *font* drew the ink off-centre inside a box the layout had placed correctly;
a path drawn off-centre inside a viewBox the layout places correctly is that bug again,
authored by hand instead of inherited from Kumbh Sans. The whole argument for paths over
characters is that the geometry becomes something this repository can hold itself to --
so it is held to it here.

**Source rather than pixels, deliberately.** Catching it on the rendered page means
reading painted pixels, which nothing in ``tests/web`` does and which would need a
screenshot comparison and a tolerance for antialiasing. The claim is about the path data,
the path data is a constant in ``view.js``, and a constant can be read and measured
exactly. So this is a stack-free module -- no browser, no image, no rate limit -- and it
is registered in ``conftest.py``'s ``NOT_A_BROWSER_SUITE`` for that reason.

**What it does not cover, stated rather than implied.** It measures the path's declared
extent: the coordinates in ``d``. The painted ink is wider than that by half the stroke
and by the round caps, which push a cap-ended endpoint about 0.65 units along the stroke
-- for the chevron that puts the painted ink centre at 12.17 rather than 12.00, which at
``.step-card__chevron``'s 1.1rem is 0.11px and inside the 1px rule the browser cases
assert. A path whose *declared* extent is centred and whose painted extent is not would
have to be asymmetric in its caps, which neither of these two shapes can be: both are
stroked with a uniform width and a single cap style.
"""

from __future__ import annotations

import pathlib
import re

import pytest

# The `browser` marker is applied by `conftest.py` by location; this module is named in
# its `NOT_A_BROWSER_SUITE`, so nothing here is marked and nothing here needs a stack.

VIEW = pathlib.Path(__file__).resolve().parents[2] / "web" / "js" / "view.js"

#: `M`/`L` only. Both shapes are polylines by construction -- a curve would need a
#: different reader and is a change this test should be made to notice rather than
#: silently accommodate, so an unexpected command fails below instead of being skipped.
_COMMAND = re.compile(r"[A-Za-z]")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _constant(name: str) -> str:
    """The string literal assigned to `export const <name>` in `view.js`."""
    source = VIEW.read_text(encoding="utf-8")
    found = re.search(rf"(?m)^export const {name} = '(.*)'$", source)
    assert found, f"{name} is not a single-quoted one-line constant in view.js any more"
    return found[1]


def _box(icon_box: str) -> tuple[float, float, float, float]:
    parts = [float(value) for value in icon_box.split()]
    assert len(parts) == 4, icon_box
    return parts[0], parts[1], parts[2], parts[3]


def _points(path_markup: str) -> list[tuple[float, float]]:
    """Every coordinate pair in the `d` attribute of a one-path markup string."""
    found = re.search(r'\bd="([^"]+)"', path_markup)
    assert found, f"no d attribute in {path_markup!r}"
    data = found[1]
    unexpected = {c for c in _COMMAND.findall(data)} - {"M", "L"}
    assert not unexpected, (
        f"the path uses {sorted(unexpected)}, which this reader does not understand -- "
        "the extent below would be wrong rather than absent, so the reader has to grow "
        "with the shape"
    )
    numbers = [float(value) for value in _NUMBER.findall(data)]
    assert numbers and len(numbers) % 2 == 0, data
    return list(zip(numbers[::2], numbers[1::2]))


@pytest.mark.parametrize("name", ["CHEVRON_PATH", "CROSS_PATH"])
def test_the_icon_is_drawn_on_the_centre_of_its_own_box(name):
    """The declared extent is symmetric about the centre of `ICON_BOX`, both axes.

    Asserted as `min + max == 2 * centre` rather than as a coordinate, so it says the
    shape is *centred* rather than that it is at some remembered place: a shape made
    bigger or smaller about the same centre still passes, and a shape slid off it fails
    however large it is.

    The centre comes out of `ICON_BOX` rather than being written as 12. The box and the
    coordinates are one decision -- that is why they live beside each other in `view.js`
    -- and a test that hard-coded the centre would keep passing on the day somebody
    changed the box and left the paths where they were, which is the drift the shared
    constant exists to prevent.
    """
    left, top, width, height = _box(_constant("ICON_BOX"))
    points = _points(_constant(name))
    xs = [x for x, _ in points]
    ys = [y for _, y in points]

    assert min(xs) >= left and max(xs) <= left + width, (name, points)
    assert min(ys) >= top and max(ys) <= top + height, (name, points)

    assert min(xs) + max(xs) == pytest.approx(2 * (left + width / 2)), (
        f"{name} is drawn off the horizontal centre of ICON_BOX: x runs "
        f"{min(xs)}..{max(xs)} in a box centred on {left + width / 2}. Its element box "
        "would still be placed correctly and every browser case would still pass, which "
        "is why this one is here."
    )
    assert min(ys) + max(ys) == pytest.approx(2 * (top + height / 2)), (
        f"{name} is drawn off the vertical centre of ICON_BOX: y runs "
        f"{min(ys)}..{max(ys)} in a box centred on {top + height / 2}. This is the "
        "mutation that survived the whole repository before this file existed -- the "
        "arrowhead moves down the page and the element's box does not."
    )


@pytest.mark.parametrize("name", ["CHEVRON_PATH", "CROSS_PATH"])
def test_the_icon_is_mirror_symmetric_about_the_axis_it_turns_on(name):
    """Each shape is its own mirror image across the vertical centre of its box.

    **Not "a half turn maps it onto itself", which was the claim this case was first
    written to assert and is false of a chevron.** `M 5 9 L 12 15 L 19 9` rotated 180
    degrees about the centre of its box is `M 19 15 L 12 9 L 5 15` -- an arrow pointing
    the other way, which is the entire purpose of `.chevron.expanded`. A disclosure
    indicator that survived its own rotation unchanged would indicate nothing. (The
    cross does map onto itself under a half turn, being symmetric on both axes; stating
    the weaker property here is what lets one case cover both shapes and say something
    true about each.)

    What the rotation must not do is move the ink, and mirror symmetry about the
    vertical axis is what guarantees it together with the centred extent asserted above:
    the turned shape occupies the same box, so the chevron turns in place and the header
    row does not shift under it. It is also what makes the turned arrow a well-formed
    arrow rather than a lopsided one -- the two arms have to be the same length for the
    up state to look like the down state reflected rather than like a different glyph.
    """
    left, _top, width, _height = _box(_constant("ICON_BOX"))
    axis = left + width / 2
    points = _points(_constant(name))
    place = lambda pair: (round(pair[0], 6), round(pair[1], 6))
    mirrored = sorted(place((2 * axis - x, y)) for x, y in points)
    assert sorted(place(point) for point in points) == mirrored, (
        f"{name} is not symmetric about the vertical centre of its box, so turning it "
        f"moves its ink and its arms are uneven: {points}"
    )
