"""``docker/mock-factors.json``'s three client equivalences, against the
values ``data/upstream-factors-draft/build_upstream_factors_draft.py``
produces.

**Why this file exists.** `docker/mock-factors.json` is what
`docker/seed_mock_factors.py` loads into `MOCK-v0` on any fresh checkout --
it is the file the client's own conversions actually reach a deployment
through, and until now nothing in the repository read it at all
(`grep -rl mock-factors.json tests/` returned nothing). `a208b95`'s own
commit message states the three rows it added -- `vehicles_year`,
`olympic_pools`, `meals` -- are "the same codes, factors and source_note
text" `build_upstream_factors_draft.py` already emits into
`upstream_factors_draft.json`; nothing enforced that claim, so a
transcription slip in either file could drift from the other silently.

**Decimal, not string.** The two files legitimately differ in notation --
`docker/mock-factors.json` writes `"0.0000004000"`, the draft JSON writes
`"4.000E-7"` -- while being numerically identical. A string comparison would
fail on that difference for the wrong reason; parsing both through
`decimal.Decimal` and comparing the parsed values is what actually proves
"the same three conversions", which is what these two files are meant to be.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MOCK_FACTORS = REPO_ROOT / "docker" / "mock-factors.json"
DRAFT_FACTORS = REPO_ROOT / "data" / "upstream-factors-draft" / "upstream_factors_draft.json"

#: The three conversions the client's own document supplies (`a208b95`,
#: `b35bb1f`). `km_driven` is deliberately excluded -- it has no counterpart
#: in the draft file at all (see item 6 of the final review; its own
#: `source_note` is still a placeholder, not the client's).
CLIENT_EQUIVALENCE_CODES = ("vehicles_year", "olympic_pools", "meals")


def _equivalences_by_code(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {row["code"]: row for row in data["equivalences"]}


def test_mock_factors_carries_every_client_equivalence():
    """The premise the value-level assertions below rest on: all three codes
    are actually present in the file a fresh deployment loads."""
    mock = _equivalences_by_code(MOCK_FACTORS)
    missing = [code for code in CLIENT_EQUIVALENCE_CODES if code not in mock]
    assert not missing, f"docker/mock-factors.json is missing {missing}"


def test_mock_factors_and_the_draft_builder_specify_the_same_three_conversions():
    """Compares `value_per_unit` as `Decimal`, never as the JSON's own string
    -- `"0.0000004000"` and `"4.000E-7"` are the same division of 1 by
    2,500,000, and only a `Decimal` comparison sees that. `name`,
    `label_template`, `source_metric` and `source_note` are compared as
    text: `source_note` is the client's verbatim wording (§7.6 rule 9) and
    must be byte-for-byte the same string wherever it is copied, not merely
    numerically equivalent.
    """
    mock = _equivalences_by_code(MOCK_FACTORS)
    draft = _equivalences_by_code(DRAFT_FACTORS)

    for code in CLIENT_EQUIVALENCE_CODES:
        mock_row = mock[code]
        draft_row = draft[code]
        assert Decimal(mock_row["value_per_unit"]) == Decimal(draft_row["value_per_unit"]), (
            f"{code}: mock-factors.json's {mock_row['value_per_unit']!r} and "
            f"the draft's {draft_row['value_per_unit']!r} are not the same "
            f"conversion factor"
        )
        for field in ("name", "label_template", "source_metric", "source_note"):
            assert mock_row[field] == draft_row[field], (
                f"{code}.{field}: mock-factors.json says {mock_row[field]!r}, "
                f"the draft says {draft_row[field]!r}"
            )
