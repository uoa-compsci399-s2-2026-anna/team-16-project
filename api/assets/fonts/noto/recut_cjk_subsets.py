"""Re-cut the four embedded CJK subset faces from their own catalogues.

**Why this file exists.** `PROVENANCE.md`'s "Re-cut, 2026-09-04" section used
to describe the character-extraction step as prose — "the catalogue's own
characters, plus ASCII and Latin-1" — and left the actual `--text-file`
content for a future maintainer to reconstruct by hand. That is the same kind
of drift that produced the six-character gap the 2026-09-04 re-cut closed in
the first place: a recipe nobody can run unchanged is a recipe that quietly
stops matching the catalogues the day one of them gains a string. This script
is the extraction step, committed rather than described, so re-cutting is
`py -3.12 recut_cjk_subsets.py` rather than a paragraph.

**What it does not do.** It does not fetch the source `.ttc` — that is the
`fonts-noto-cjk` apt package, installed inside the `api` container image (see
`docker/api.Dockerfile`) or, for a one-off re-cut, added to a running
container the way `PROVENANCE.md` describes. This script only turns each of
the four catalogues into a `--text-file` and drives `fontTools.subset` over
the collection already on disk, one face per language, with the exact flags
`PROVENANCE.md` has always documented.

**Run inside the `api` container** (or any environment with `fontTools` and
`brotli` installed and the source collection present):

    apt-get install --no-install-recommends fonts-noto-cjk
    py -3.12 api/assets/fonts/noto/recut_cjk_subsets.py

Writes the four `.woff2` files in place, in this same directory. Nothing here
touches `web/locales/*.json` — it only reads them.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]  # noto -> fonts -> assets -> api -> repo root
LOCALES_DIR = REPO_ROOT / "web" / "locales"

#: The source collection `fonts-noto-cjk` installs, and the face index of
#: each language within it — the same four faces `PROVENANCE.md`'s table
#: names, in the same order.
SOURCE_COLLECTION = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")

#: `(catalogue file, face number, output file)`. Face numbers are the
#: collection's own layout, not a choice this script makes: 0 Japanese,
#: 1 Korean, 2 Simplified Chinese, 3 Traditional Chinese.
FACES = (
    ("ja.json", 0, "NotoSansCJKjp-Regular.woff2"),
    ("ko.json", 1, "NotoSansCJKkr-Regular.woff2"),
    ("zh.json", 2, "NotoSansCJKsc-Regular.woff2"),
    ("zh-Hant.json", 3, "NotoSansCJKtc-Regular.woff2"),
)

#: "Plus ASCII and Latin-1" (`PROVENANCE.md`): printable ASCII (space through
#: tilde) and the printable half of Latin-1 Supplement (`U+00A0`-`U+00FF`).
#: Neither set is Han or Hangul, so neither changes with a catalogue edit —
#: they are here so a CJK document can still set a stray Latin word, a digit
#: or a currency sign without falling back to a second face mid-sentence.
_ASCII = "".join(chr(code) for code in range(0x20, 0x7F))
_LATIN1_SUPPLEMENT = "".join(chr(code) for code in range(0xA0, 0x100))


def catalogue_characters(path: Path) -> set[str]:
    """Every character `i18n.Catalogue.strings` would hand to `gettext` for
    this language — the same collection `assert_every_character_is_drawable`
    checks a render against, read the same way: `raw["strings"].values()`,
    not the whole file. `endonym` and every other top-level metadata field
    are deliberately excluded, because nothing in the rendered document ever
    prints them — see `PROVENANCE.md`'s note on `U+672C` (`ja`) and `U+AD6D`
    (`ko`), both `endonym`-only and both correctly absent from every face.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    characters: set[str] = set()
    for value in raw["strings"].values():
        characters.update(value)
    return characters


def text_file_for(locale_file: str) -> set[str]:
    return catalogue_characters(LOCALES_DIR / locale_file) | set(_ASCII) | set(_LATIN1_SUPPLEMENT)


def subset_one(source: Path, face_number: int, characters: set[str], output: Path) -> None:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", encoding="utf-8", delete=False
    ) as handle:
        handle.write("".join(sorted(characters)))
        text_file = Path(handle.name)
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "fontTools.subset",
                str(source),
                f"--font-number={face_number}",
                f"--text-file={text_file}",
                "--flavor=woff2",
                f"--output-file={output}",
                "--layout-features=*",
                "--glyph-names",
                "--symbol-cmap",
                "--legacy-cmap",
                "--notdef-glyph",
                "--notdef-outline",
                "--recommended-glyphs",
                "--name-legacy",
            ],
            check=True,
        )
    finally:
        text_file.unlink(missing_ok=True)


def main() -> None:
    if not SOURCE_COLLECTION.is_file():
        raise SystemExit(
            f"{SOURCE_COLLECTION} not found — install fonts-noto-cjk first "
            "(see this file's own module docstring)"
        )
    for locale_file, face_number, output_name in FACES:
        characters = text_file_for(locale_file)
        output = HERE / output_name
        subset_one(SOURCE_COLLECTION, face_number, characters, output)
        print(f"{output_name}: {len(characters)} characters, {output.stat().st_size:,} B")


if __name__ == "__main__":
    main()
