"""The calculator's negotiator, executed rather than read.

`test_i18n_web.py` proves the catalogues are complete and `test_i18n_browser.py`
proves a page renders in the right one — but the browser file needs the stack up
and takes minutes, so the rule itself had no fast check that could be run
against a mutation. This file is that check: `web/js/i18n.js` under Node,
against the real `web/locales/index.json`, so the twenty catalogues' own tag
claims are the index being matched against.

**The rule under test (v1.26): only the highest-priority tag is consulted, and
if it has no catalogue the answer is English.** Every assertion of that form is
written next to one that would fail if the matcher had simply stopped working —
`fr-CA, zh, en` answering English proves nothing on its own, because a
negotiator that always returned English would pass it too.

Node is the runner only and does not enter the stack (`docs/architecture.md`
§3): there is still no build step and no `package.json`. `i18n.js`'s top-level
`await load()` returns immediately under a `file:` module URL, which is what
makes importing it here possible at all.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
I18N_JS = ROOT / "web" / "js" / "i18n.js"
MANIFEST = ROOT / "web" / "locales" / "index.json"

node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is required to execute web/js/i18n.js; the rule is unverified without it",
)

#: `negotiate` is called with `navigator.languages` alone. A forced `?lang=` is
#: matched by `load()` *before* it — deliberately outside the single-tag rule —
#: and that composition is asserted end to end in `test_i18n_browser.py`, which
#: is the only place a real query string exists.
HARNESS = """
import { readFileSync, writeFileSync } from 'node:fs'
const { negotiate } = await import(process.argv[2])
const { tagIndex } = await import(process.argv[2])
const manifest = JSON.parse(readFileSync(process.argv[3], 'utf8'))
const index = tagIndex(manifest.catalogues)
const cases = JSON.parse(readFileSync(process.argv[4], 'utf8'))
writeFileSync(
  process.argv[5],
  JSON.stringify(cases.map((languages) => negotiate(languages, index))),
  'utf8',
)
"""


def resolved(tmp_path: Path, cases: list[list[str]]) -> list[str]:
    harness = tmp_path / "harness.mjs"
    harness.write_text(HARNESS, encoding="utf-8")
    request = tmp_path / "cases.json"
    request.write_text(json.dumps(cases), encoding="utf-8")
    out = tmp_path / "languages.json"
    completed = subprocess.run(
        [
            shutil.which("node"),
            str(harness),
            I18N_JS.as_uri(),
            str(MANIFEST),
            str(request),
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, (
        f"node could not run the negotiation:\n{completed.stdout}\n{completed.stderr}"
    )
    return json.loads(out.read_text(encoding="utf-8"))


@node
def test_an_unmatched_first_tag_ends_at_english_and_a_matched_one_does_not(tmp_path):
    """The rule and its control, in one test so neither can be read alone.

    The first three are the rule: the head has no catalogue, so English, and
    the supported language sitting behind it never gets a turn. The last three
    are what make those three evidence rather than a tautology — the same
    lists with a supported tag at the head still reach their own catalogue.

    The unsupported head is `sv` and not the `fr-CA` the panel's tests use:
    **French is one of the calculator's twenty**, so `fr-CA, zh, en` reaches
    French here and demonstrates nothing about the rule. The two surfaces ship
    different catalogue sets and share only the rule.
    """
    assert resolved(
        tmp_path,
        [
            ["sv-SE", "zh", "en"],
            ["sv", "ko", "en"],
            ["xx", "de", "ja"],
            ["zh", "sv"],
            ["ko", "sv-SE"],
            ["ja", "xx", "de"],
        ],
    ) == ["en", "en", "en", "zh", "ko", "ja"]


@node
def test_truncation_still_applies_to_the_one_tag_consulted(tmp_path):
    """The rule removes the walk down the list, not the lookup within a tag.

    `de-AT, xx` reaching German is the case that separates the two: a
    negotiator that had stopped truncating would answer English here and pass
    every assertion above about an unsupported tag.
    """
    assert resolved(
        tmp_path,
        [
            ["de-AT", "xx"],
            ["zh-CN"],
            ["zh-Hans-CN", "en"],
            ["en-NZ", "zh"],
            ["pa-Guru-IN", "en"],
        ],
    ) == ["de", "zh", "zh", "en", "pa"]


@node
def test_a_catalogue_s_own_claim_still_beats_truncation(tmp_path):
    """`zh-TW` must reach Traditional Chinese and not Simplified.

    Held here as well as in the browser because it is the one mechanism the
    previous pass shipped with no test at all: every tag `zh.json` claims is
    also reachable by truncation, so only the Traditional catalogue makes the
    claims load-bearing. `zh-CN` sits beside it, because a claim table that
    had collapsed onto one language would answer `zh-Hant` for both.
    """
    assert resolved(
        tmp_path,
        [["zh-TW"], ["zh-HK", "en"], ["zh-MO"], ["zh-CN"], ["fil"], ["zh-Hant"]],
    ) == ["zh-Hant", "zh-Hant", "zh-Hant", "zh", "tl", "zh-Hant"]


@node
def test_an_empty_or_unknown_list_is_english(tmp_path):
    assert resolved(
        tmp_path, [[], ["xx-YY", "zz"], [""], ["*", "zh"], ["zh", "*"]]
    ) == ["en", "en", "en", "en", "zh"]
