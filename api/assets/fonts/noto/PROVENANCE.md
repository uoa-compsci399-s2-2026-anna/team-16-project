# The export document's script fallback faces

The brand faces — Geologica Bold and Kumbh Sans Regular, one directory up — are
228-glyph Latin subsets. The calculator's interface ships in twenty languages,
and those twenty span Arabic, Devanagari, Gurmukhi, Gujarati, Tamil, Malayalam,
Thai, Japanese, Korean and two Chinese scripts. **The brand faces cover none of
them.** A character with no glyph in any embedded face is drawn as a box or
dropped altogether, and either is the same class of defect as the WinAnsi
rasterisation the server-rendered export exists to replace: a document that
looks fine to whoever generated it and is corrupt for whoever reads it.

So the faces below are here to be *embedded in the PDF*, not fetched. Contract
§7.6 rule 7 forbids a runtime asset from a third-party host and a
server-rendered document is the stricter case, not an exception — the request
would leave the API container where nobody would ever see it.

**They are not the same thing as the `fonts-noto-core` apt package** that
`docker/api.Dockerfile` installs. That package is a safety net for whatever a
Debian image happens to carry; these files are what the stylesheet names, so
the document is set in the same faces on a developer's checkout, in CI and in
the container. A render whose typography depends on the host is a render nobody
can reproduce.

## Licence

**SIL Open Font License, Version 1.1**, for every file here. The full text is
in `LICENCE-fonts-noto-core.txt` and `LICENCE-fonts-noto-cjk.txt`, copied
verbatim from the two Debian packages the faces were taken from.

The OFL permits redistribution, modification (including subsetting) and
embedding in a document. Both upstreams state the grant as *"This Font Software
is licensed under the SIL Open Font License, Version 1.1"* with **no Reserved
Font Name**, so the subsets below may keep their family names. Neither the
fonts nor this repository sells the fonts on their own, which is the OFL's one
prohibition.

## Where each file came from

Built inside `debian:bookworm`-based `python:3.11-slim`, from:

```sh
apt-get install --no-install-recommends fonts-noto-core fonts-noto-cjk
pip install fonttools brotli
```

| File | Source | Cut |
| --- | --- | --- |
| `NotoSans-Regular.woff2` | `fonts-noto-core` 20201225-2 | whole face (2,840 characters) |
| `NotoSansArabic-Regular.woff2` | `fonts-noto-core` 20201225-2 | whole face |
| `NotoSansDevanagari-Regular.woff2` | `fonts-noto-core` 20201225-2 | whole face |
| `NotoSansGurmukhi-Regular.woff2` | `fonts-noto-core` 20201225-2 | whole face |
| `NotoSansGujarati-Regular.woff2` | `fonts-noto-core` 20201225-2 | whole face |
| `NotoSansTamil-Regular.woff2` | `fonts-noto-core` 20201225-2 | whole face |
| `NotoSansMalayalam-Regular.woff2` | `fonts-noto-core` 20201225-2 | whole face |
| `NotoSansThai-Regular.woff2` | `fonts-noto-core` 20201225-2 | whole face |
| `NotoSansCJKjp-Regular.woff2` | `fonts-noto-cjk` 1:20240730+repack1-1, `NotoSansCJK-Regular.ttc` face 0 | **subset** |
| `NotoSansCJKkr-Regular.woff2` | same collection, face 1 | **subset** |
| `NotoSansCJKsc-Regular.woff2` | same collection, face 2 | **subset** |
| `NotoSansCJKtc-Regular.woff2` | same collection, face 3 | **subset** |

Each was converted to WOFF2 with `fontTools` (`font.flavor = "woff2"`), which
is lossless — the same outlines, Brotli-compressed.

## Urdu is set in naskh, and that was a decision

`NotoNastaliqUrdu-Regular.woff2` was vendored here and then removed. Nastaliq
is the style an Urdu reader expects, but it shapes a line into long cascading
ligatures — one PDF glyph standing for several characters — and the text a
reader *selects* out of the result comes back mangled: `تصدیق` extracted as
`تصدیت`, `فراہم` as `نراہم`. Twelve of PR #46's twenty locales were
unselectable and unsearchable because the page had been rasterised; shipping a
face that garbles the same twelve on copy would be that defect returning in a
subtler form.

Urdu is therefore set in `NotoSansArabic-Regular.woff2`, which covers every
character of it and extracts cleanly. A real typographic compromise, recorded
rather than quietly taken.

## Why the four CJK faces are subsets and nothing else is

**Measured, not assumed.** A whole Noto Sans CJK face is 10.9 MiB as WOFF2, and
Japanese, Korean, Simplified and Traditional Chinese need four of them because
they draw shared codepoints differently — 43.6 MiB, in a repository, in a
wheel, and in two container images. The `.ttc` collection they ship in is 18.6
MiB and cannot be used at all: CSS `@font-face` has no way to name a face
inside a collection.

Cut to the characters the four Chinese, Japanese and Korean catalogues actually
contain, plus ASCII and Latin-1, the same four faces are **696 KiB**. Every
other face here is whole, because a whole Indic or Arabic face costs tens of
kilobytes and a subset would buy nothing.

### What the subset gives up, and what stops it being silent

The catalogues are checked-in files, so the subset is complete over a *closed*
set — and `tests/api/test_pdf_render.py` asserts exactly that, per locale, so
the day a catalogue gains a character the subset lacks, a test fails rather
than a document quietly losing a glyph.

What it does not cover is a **staff-typed taxonomy name in CJK**. Sector,
destination, food-category and metric names are database rows printed verbatim
in every locale (§2.1), and a Chinese destination name would reach a face that
has 688 characters in it.

That case does not render a box. `api/pdf_render.py::assert_every_character_is_drawable` checks the document's own text against the embedded faces' character
maps before WeasyPrint is called and raises `UndrawableCharacterError`, naming
the characters and this file. **A loud failure, not tofu** — which is the whole
point of the exercise. If it ever fires in earnest, the fix is to re-cut the
face concerned with the extra characters, or to ship the whole 10.9 MiB face
for that language and accept the weight.

## Re-cut, 2026-09-04

The four CJK subsets had drifted from "the characters the four catalogues
actually contain": `ja.json`, `ko.json`, `zh.json` and `zh-Hant.json` had each
grown at least one string since the faces were last cut, and six characters
across the four catalogues (`及` `涉` `率` `笔` `部` and one Hangul syllable)
had no glyph in any embedded face — a fact `test_no_character_in_any_catalogue_
would_print_as_a_box` had been failing on for some time without anything
actually asking a CJK document to print one of them. Task 5's title block did:
the Traditional Chinese "not supplied" sentence contains `涉`, and rendering it
raised `UndrawableCharacterError` rather than shipping a box.

Re-cut inside the running `api` container (already `debian:bookworm`-based
with Pango/HarfBuzz installed) rather than a fresh build, following the same
recipe as the table above:

```sh
apt-get install --no-install-recommends fonts-noto-cjk   # fonttools + brotli already present
fonttools subset /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
  --font-number=<0|1|2|3> --text-file=<catalogue's own characters, plus ASCII and Latin-1> \
  --flavor=woff2 --output-file=<NotoSansCJK{jp,kr,sc,tc}-Regular.woff2> \
  --layout-features=* --glyph-names --symbol-cmap --legacy-cmap \
  --notdef-glyph --notdef-outline --recommended-glyphs --name-legacy
```

Each face is cut from its own language's *current* `web/locales/*.json` (`ja`
→ face 0, `ko` → face 1, `zh` → face 2, `zh-Hant` → face 3), read fresh rather
than from the character list the previous cut used — a list frozen at the last
cut is exactly how this drifted the first time. New sizes: jp 228,224 B (was
208,076), kr 79,916 B (was 72,968), sc 200,708 B (was 188,332), tc 261,024 B
(was 243,356) — a few kilobytes each for the characters that were missing,
still two orders of magnitude under the 10.9 MiB whole face.

**The character-extraction step above was prose, not a committed script, and
that gap is now closed.** `recut_cjk_subsets.py`, beside this file, is the
`--text-file` step: it reads each language's own `web/locales/*.json`
`strings` values (the same collection `assert_every_character_is_drawable`
checks a render against), adds printable ASCII and the printable half of
Latin-1 Supplement, and drives `fontTools.subset` with the flags above.
Re-cutting all four faces from the current catalogues is `apt-get install
fonts-noto-cjk` followed by `py -3.12
api/assets/fonts/noto/recut_cjk_subsets.py` — no longer a paragraph a future
maintainer has to reconstruct into a `--text-file` by hand, which is the same
drift that produced the gap this section exists to record.

### What the review that closed this task found

The re-cut's six-character gap (`及` `涉` `率` `笔` `部` and one Hangul
syllable) was confirmed genuinely pre-existing — present against the parent
commit's fonts, absent against these — and closed by this re-cut. Two things
the re-cut itself changed, neither caught by any test because neither is a
regression a test watches for:

* **`U+5360` (`占`) is gone from the Traditional Chinese face.** It was in the
  face this re-cut replaced and is not in the one it produced, because
  `zh-Hant.json`'s current strings no longer contain it — the previous cut was
  frozen at an older catalogue and this one reads fresh, exactly as designed.
  Benign: the Simplified Chinese face still carries `U+5360` for `zh`, and
  `zh-Hant` never asks for it. Recorded because a *harmful* drop would look
  identical to this one from the outside — same silent size change, same
  "still all green" test run — and nothing before this line distinguished
  them from each other.
* **`U+672C` (`本`, in `ja`) and `U+AD6D` (`국`, in `ko`) are not covered by
  any embedded face, and are not a gap.** Both live only in their catalogue's
  top-level `endonym` field ("日本語", "한국어" — the language's own name for
  itself), which sits beside `strings` in the JSON file, not inside it.
  `i18n.Catalogue.strings` is built from the `strings` key alone, so neither
  character ever reaches `gettext`, `assert_every_character_is_drawable`, or
  the rendered document — `recut_cjk_subsets.py`'s `catalogue_characters`
  reads the same key for the same reason. Pre-existing, unrelated to this
  re-cut, and left exactly as they were rather than added to a subset for
  characters nothing prints.

## Re-cut, 2026-09-04 (v1.51, second re-cut this day)

v1.51 gave `production_share_percent` and `wasted_share_percent` a fourth
`data_state`, `undefined`, and three new strings reach the twenty catalogues
for it — `Undefined`, and one note each for the production-share card and the
money block. `ja`, `zh` and `zh-Hant`'s translations of the one-word value
all draw on `義`/`义`, "meaning" — `U+7FA9` in Traditional, `U+4E49` in
Simplified — and neither had a glyph in the June re-cut's subsets, which had
been cut from the catalogues as they stood before this task.
`test_no_character_in_any_catalogue_would_print_as_a_box` caught it before
anything asked a document to print the new string, the same way it caught
the six-character gap above.

Re-cut with `recut_cjk_subsets.py`, run inside the `api` container after
rebuilding it with the new catalogues baked in — `fonts-noto-cjk` installed
fresh (the base image carries no state between builds), `fonttools` and
`brotli` already present as `weasyprint`'s own dependencies. `ko`'s subset is
untouched (`정의되지 않음`, the Korean translation of `Undefined`, composes
entirely from syllables the existing subset already drew); the other three
grew a handful of characters each: jp 228,432 B (was 228,224), sc 201,112 B
(was 200,708), tc 261,488 B (was 261,024).

## Re-cut, 2026-09-11 (equivalence basis, Task 7)

Task 6 keyed four new page strings for the equivalence disclosure —
`Total`, `Per unit`, `Basis:`, and the fallback sentence "The basis for
this conversion is not recorded yet." — and Task 7 wired the same four,
plus the standing caveat "The conversion factor comes from the client. The
total it is applied to comes from placeholder factors.", into the PDF and
the text export as well, so a document read months later can say which
figures are measured and which are borrowed (§6.3). Two of the eight new
strings' Korean and Japanese translations (`근거:`/`근거는` for `ko`'s
`Basis:` and its fallback sentence) contain a Hangul syllable, `U+ADFC`
(근), that the existing `jp`/`kr` subsets did not carry, and rendering
either raised `UndrawableCharacterError` before this re-cut.

Re-cut the same way as both 2026-09-04 entries above:
`recut_cjk_subsets.py`, run inside the `api` container with
`fonts-noto-cjk` installed fresh. Compared character-for-character against
the faces this replaces, per the module's own worry above ("What stops it
being silent"): **nothing was lost.**

| Face | Characters before | Characters after | Lost |
| --- | --- | --- | --- |
| `NotoSansCJKjp-Regular.woff2` | 631 | 634 | 0 |
| `NotoSansCJKkr-Regular.woff2` | 554 | 556 | 0 |
| `NotoSansCJKsc-Regular.woff2` | 694 | 694 | 0 |
| `NotoSansCJKtc-Regular.woff2` | 689 | 690 | 0 |

`sc` is untouched — none of the eight new strings' Simplified Chinese
translations introduced a character the existing subset lacked. `tc`
gained one character, `套` (`U+5957`, "set of"), not from a *new* string
this task added to `DOCUMENT_STRINGS`, but because `recut_cjk_subsets.py`
cuts each face from its language's **whole catalogue** (`catalogue_
characters`, above) rather than only the subset a given render happens to
use — and Task 6's disclaimer sentence, already present in `zh-Hant.json`
for the page, had never previously been cut into this face at all. `jp`
and `kr` each gained the handful of characters their own translations of
the eight strings above actually use.

## Re-cut, 2026-09-19 (step 3's zones and term tooltips, v1.61)

Step 3 was re-laid as two tinted zones and its four field names became
tooltip terms, which added eighteen keys to every catalogue and removed six
(`docs/interfaces.md` v1.61). Twelve of the new strings are prose rather than
labels — two zone sub-lines, eight tooltip sentences and two money hints — so
the four CJK catalogues gained more new characters at once than any previous
task: `test_no_character_in_any_catalogue_would_print_as_a_box` named
**twenty-three** code points across `ja`, `ko`, `zh` and `zh-Hant`, among them
廢 and 弃 ("discard"), 報/报 ("report"), 補/补 ("supplement") and 錢/钱
("money") — all four ideas new to this screen's copy, and none of them in the
faces as cut on 2026-09-11.

Re-cut the same way as every entry above: `recut_cjk_subsets.py`, run inside
the `api` container with `fonts-noto-cjk` (1:20240730+repack1-1, the same
package version the table at the top of this file names) installed fresh.

| Face | Characters before | Characters after | Bytes before | Bytes after |
| --- | --- | --- | --- | --- |
| `NotoSansCJKjp-Regular.woff2` | 634 | 646 | 229,496 | 232,416 |
| `NotoSansCJKkr-Regular.woff2` | 556 | 558 | 80,220 | 80,568 |
| `NotoSansCJKsc-Regular.woff2` | 694 | 705 | 201,112 | 204,096 |
| `NotoSansCJKtc-Regular.woff2` | 690 | 705 | 262,040 | 267,240 |

**Three characters present in the old faces are absent from the new ones, and
none of them is a loss.** `jp` drops 般, `sc` and `tc` drop 特 (and `sc`
「」 and 规, `tc` 般), and `kr` drops ‘ and ’. Every one of them was checked
against the catalogue it would have to come from, at `HEAD` as well as in the
working tree: **not one appears in any string of its own catalogue in either**.
They are residue from a cut taken before some earlier catalogue edit removed
the string that needed them, carried forward because a re-cut only ever
happens when something is *missing*. This entry is the first to say so; the
2026-09-11 table's "Lost: 0" column was true of that re-cut and is recorded
here in its own terms instead, because "lost" and "no longer required" are
different facts and only the first is a defect.
