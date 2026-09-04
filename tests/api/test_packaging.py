"""Every file `api/pdf_render.py` reads at runtime has to be in the wheel.

**Found live, not written first.** `assets/kai-commitment-logo.png` (v1.50
review) was on disk, byte-identical to `web/assets/`'s copy, referenced by
`results.html.j2`'s `<img src="kai-commitment-logo.png">`, and every test in
`tests/api/test_pdf_render.py` that renders through a checkout passed —
because a checkout is exactly where the file is simply there. The built `api`
image was not: `[tool.setuptools.package-data]`'s `api` list had no pattern
for a bare `assets/*.png`, `include-package-data = false` means there is no
fallback, and a curl against the running container returned a 200 PDF with an
empty `<title>` and a masthead with no logo — `_local_url_fetcher` reads a
file that is not there, WeasyPrint logs a failed image load and keeps going,
and nothing anywhere raises. The same failure mode `tests/admin/test_
packaging.py`'s own docstring describes for the panel's guidance blocks,
one package over.

Same shape as that file, adapted to `api/`'s two runtime directories -
`templates/` (the document and its stylesheet) and `assets/` (fonts, the
twenty-one locale catalogues, and now the logo). `.py` files under either are
excluded outright: `admin.py`, `db.py` and `engine.py` files are covered by
`packages = [...]` and need no pattern, and the one `.py` file this
repository keeps under `api/assets/` -
`api/assets/fonts/noto/recut_cjk_subsets.py` - is a maintainer's tool run
against a checkout (see `PROVENANCE.md`), not something `pdf_render.py`
reads, and is deliberately not shipped in the wheel.
"""

from __future__ import annotations

import tomllib
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "api"

#: Directories whose contents are read at runtime rather than imported.
RUNTIME_DIRS = ("templates", "assets")


def _patterns() -> list[str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return data["tool"]["setuptools"]["package-data"]["api"]


def _covered(relative: PurePosixPath, patterns: list[str]) -> bool:
    """Whether one package-relative path is matched by any pattern.

    Anchored at both ends, the same way `tests/admin/test_packaging.py`'s own
    `_covered` is: `PurePath.match` is right-anchored on its own, so
    `assets/*.png` would "match" `assets/fonts/x.png` and report a pattern
    that setuptools does not in fact apply that way. Requiring the same
    number of path components is what makes this answer the question that is
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
            if path.suffix == ".py":
                continue
            relative = PurePosixPath(path.relative_to(PACKAGE).as_posix())
            if not _covered(relative, patterns):
                missing.append(str(relative))

    assert not missing, (
        "these files are read at runtime but no [tool.setuptools.package-data] "
        "pattern ships them, so a built image raises, or silently omits an "
        "image or a face, on whatever screen reads each one: "
        + ", ".join(missing)
    )


def test_the_logo_in_particular():
    """The file that was actually missed, named outright - `tests/admin/
    test_packaging.py::test_the_guidance_blocks_in_particular`'s own reason:
    the general test's failure message is a list of paths, and the next
    person to read it should not have to work out why a bare PNG needs a
    line in a build file."""
    patterns = _patterns()
    logo = PACKAGE / "assets" / "kai-commitment-logo.png"
    assert logo.is_file(), "the logo has moved; this test needs updating"
    relative = PurePosixPath(logo.relative_to(PACKAGE).as_posix())
    assert _covered(relative, patterns), f"{relative} would not ship"
