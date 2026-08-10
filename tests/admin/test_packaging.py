"""Every file the panel renders at runtime has to be in the wheel.

**This test exists because the checkout cannot fail the way the wheel can.**
`admin/templates/` is on disk during development, so Jinja finds a template
whether or not anything ships it; the same template is absent from an
installed package unless a pattern in `[tool.setuptools.package-data]` names
its directory, and `include-package-data = false` means there is no fallback.
The two states are indistinguishable from any test that renders a page.

It is written from a live failure, not a hypothetical one. The five
page-level guidance blocks were added under `templates/brand/guidance/`,
which no pattern matched. Four screens - factor sets, upstream factors,
formulas - and the dry-run page raised `TemplateNotFound` and returned 500 in
the built container, with 845 tests passing against the checkout. It was
found by starting the shipped stack from an empty database and following a
page's own links, which is not something a test suite does.

The failure mode is a *new directory*, so this asserts over the tree rather
than over a list: anything under `admin/templates/` or `admin/static/` that
no pattern reaches fails here, including files nobody has thought of yet.
"""

import tomllib
from pathlib import PurePosixPath

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "admin"

#: Directories whose contents are read at runtime rather than imported.
#: `.py` files are covered by `packages = [...]` and need no pattern.
RUNTIME_DIRS = ("templates", "static")


def _patterns() -> list[str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return data["tool"]["setuptools"]["package-data"]["admin"]


def _covered(relative: PurePosixPath, patterns: list[str]) -> bool:
    """Whether one package-relative path is matched by any pattern.

    Anchored at both ends. `PurePath.match` is right-anchored on its own, so
    `brand/*.html` would "match" `templates/brand/x.html` and report a
    pattern that setuptools does not in fact apply that way; requiring the
    same number of path components is what makes this answer the question
    actually being asked.
    """
    return any(
        len(PurePosixPath(pattern).parts) == len(relative.parts)
        and relative.match(pattern)
        for pattern in patterns
    )


def test_every_runtime_file_is_shipped():
    patterns = _patterns()

    missing = []
    for directory in RUNTIME_DIRS:
        for path in sorted((PACKAGE / directory).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = PurePosixPath(path.relative_to(PACKAGE).as_posix())
            if not _covered(relative, patterns):
                missing.append(str(relative))

    assert not missing, (
        "these files are read at runtime but no [tool.setuptools.package-data] "
        "pattern ships them, so an installed panel raises on the screen that "
        "uses each one: " + ", ".join(missing)
    )


def test_the_guidance_blocks_in_particular():
    """The directory that was actually missed, named outright.

    The test above is the general one and would catch this again on its own.
    This is here because the general test's failure message is a list of
    paths, and the next person to read it should not have to work out why a
    template directory needs a line in a build file. Also: the guidance
    blocks are the whole of Task 5, and they are includes - a missing include
    is a 500 on somebody else's screen rather than a blank page on their own.
    """
    patterns = _patterns()

    guidance = sorted((PACKAGE / "templates" / "brand" / "guidance").glob("*.html"))
    assert guidance, "the guidance blocks have moved; this test needs updating"

    for path in guidance:
        relative = PurePosixPath(path.relative_to(PACKAGE).as_posix())
        assert _covered(relative, patterns), f"{relative} would not ship"
