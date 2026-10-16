"""The alpha version CI derives has to survive PEP 440's own normalisation.

**What went wrong, so that the shape of this file makes sense.** The merge of
pull request #198 turned ``main`` red with every test green. Its short SHA was
``0739392`` - seven hex characters that happen to be seven decimal digits, with
a leading zero. ``ci.yaml`` asked for ``0.0.0.dev0+0739392``, the stamp action
wrote exactly that into ``pyproject.toml`` and verified it had (by re-reading
the TOML, which is a string comparison and sees nothing wrong), the wheel came
out of the Dockerfile's build stage carrying ``0.0.0.dev0+739392``, and
``_build-images.yaml``'s "does the image say what we asked for" assertion
compared those two spellings as strings and failed both architecture builds.
``Alpha tag`` then failed because neither build had pushed anything.

Nothing was built wrong. PEP 440 compares a local version segment made
entirely of digits *numerically*, and so normalises it: ``+0739392`` and
``+739392`` are the same version and the shorter one is canonical. The
assertion was holding the version to a spelling it is not allowed to keep.

**Why this is a test and not just a fixed line.** The failure needed a
one-in-268 commit - P(first character is ``0``) x P(the other six are all
digits), or 1/16 x (10/16)^6 - so it sat latent through ninety-odd merges and
would sit latent again. A CI run on the branch that fixes it proves nothing,
because that branch's own head SHA is overwhelmingly likely to contain a
letter. Only enumerating the dangerous shapes proves anything, and that is
cheap to do here and impossible to do there.

**Both halves of the trap live here on purpose.** ``release.yaml`` already
knew about this: its version regex forbids ``01.0.0`` and the comment beside it
spells out why in full. That is the same normalisation in the *public* segment,
guarded, while the *local* segment went unguarded in the other workflow. One
file now asserts both, so neither can be fixed in isolation and lost again.

**The one thing this cannot check** is that setuptools normalises the way
``packaging`` says it does. They are the same code - setuptools vendors
``packaging``, and ``packaging==26.3`` is pinned in ``docker/constraints.txt``
- but "the build back-end agrees" is a property of the artefact, so it was
checked by hand against a real wheel when the fix was made and is restated in
``_build-images.yaml``'s own assertion on every build. A test here that built
a wheel would cost a minute to say what the version job says in five seconds.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest
import yaml
from packaging.version import Version

REPO = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO / ".github" / "workflows" / "ci.yaml"
RELEASE_WORKFLOW = REPO / ".github" / "workflows" / "release.yaml"

#: The base version is read from ``pyproject.toml`` by the workflow, so this is
#: a stand-in rather than the real one. The property under test is a property
#: of the suffix and holds for any PEP 440 base.
BASE = "9.9.9"

#: The two expansions the derived version is allowed to contain. Asserted,
#: because everything below models the shell line by substituting these two
#: names - if a third ever appears, or a command substitution does, the model
#: is wrong and silently so.
ALLOWED_EXPANSIONS = {"${BASE}", "${SHA}"}

#: Short SHAs chosen to attack the normalisation rule rather than to be
#: representative. The first is the real one that broke the build.
ADVERSARIAL_SHAS = (
    "0739392",  # the merge of #198: all digits, one leading zero
    "0000000",  # every digit a zero - normalises to `0`
    "0000001",  # six leading zeros
    "0012345",  # two leading zeros
    "1234567",  # all digits, no leading zero: numeric, but already canonical
    "0abcdef",  # leading zero, but a letter makes the segment non-numeric
    "abcdef0",  # a letter, trailing zero
    "466e4c0",  # the head of #198's branch, which is why the branch was green
    "43f5276",  # the commit deployed before the move back to 10.0.0.130
    "0739392a",  # eight characters, in case the slice below ever widens
    "073939",  # six, in case it ever narrows
)


def _derive_script() -> str:
    """The real shell of ci.yaml's ``derive`` step, read out of the workflow."""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["version"]["steps"]
    derive = [s for s in steps if s.get("id") == "derive"]
    assert len(derive) == 1, (
        "expected exactly one step with `id: derive` in ci.yaml's `version` "
        f"job, found {len(derive)}. This test models that step; find it again "
        "before trusting anything below."
    )
    return derive[0]["run"]


def _version_template() -> str:
    """The right-hand side of the step's ``VERSION=`` assignment."""
    matches = re.findall(r'^\s*VERSION="([^"]*)"\s*$', _derive_script(), re.M)
    assert len(matches) == 1, (
        "expected exactly one `VERSION=\"...\"` assignment in ci.yaml's "
        f"`derive` step, found {len(matches)}."
    )
    return matches[0]


def _render(template: str, sha: str, base: str = BASE) -> str:
    return template.replace("${BASE}", base).replace("${SHA}", sha)


def _random_shas(count: int, alphabet: str, length: int = 7) -> list[str]:
    """A deterministic sample. Seeded, so a failure is reproducible verbatim."""
    rng = random.Random(1_989)
    return ["".join(rng.choice(alphabet) for _ in range(length)) for _ in range(count)]


def test_the_version_line_expands_only_the_base_and_the_sha() -> None:
    """Guards the model every other test in this file is built on.

    A `$(git ...)` or a third variable in that line would make the rendering
    below a fiction, and a fiction that still passed.
    """
    template = _version_template()
    found = set(re.findall(r"\$\{[^}]*\}|\$\(.*?\)|\$[A-Za-z_][A-Za-z0-9_]*", template))
    assert found == ALLOWED_EXPANSIONS, (
        f"ci.yaml derives the version as {template!r}, which expands {found}. "
        f"This test only knows how to render {ALLOWED_EXPANSIONS}."
    )


def test_the_commit_that_broke_the_build_is_now_stable() -> None:
    """The regression, named and pinned.

    ``0739392`` is not a hypothetical: it is the merge commit of #198, and this
    assertion is the one that would have gone red on 2026-10-15 instead of two
    image builds and a manifest job going red after forty minutes of suite.
    """
    derived = _render(_version_template(), "0739392")
    assert str(Version(derived)) == derived, (
        f"CI would ask for {derived!r}, which PEP 440 normalises to "
        f"{str(Version(derived))!r}. The image's own metadata will carry the "
        "normalised spelling and `_build-images.yaml` compares the two as "
        "strings, so this is the #198 failure again."
    )


@pytest.mark.parametrize("sha", ADVERSARIAL_SHAS)
def test_no_adversarial_short_sha_can_be_renormalised(sha: str) -> None:
    derived = _render(_version_template(), sha)
    assert str(Version(derived)) == derived, (
        f"short SHA {sha!r} derives {derived!r}, which normalises to "
        f"{str(Version(derived))!r}."
    )


@pytest.mark.parametrize(
    ("label", "alphabet"),
    [
        ("all-decimal-digit", "0123456789"),
        ("hexadecimal", "0123456789abcdef"),
    ],
)
def test_a_sample_of_short_shas_survives_normalisation(label: str, alphabet: str) -> None:
    """The dangerous class is all-digit SHAs, so it gets its own sample.

    Exhaustive would be 10^7 for the first and 16^7 for the second; neither
    belongs in a suite that already takes half an hour. The curated cases above
    carry the shapes that matter and this carries the volume.
    """
    template = _version_template()
    for sha in _random_shas(2_000, alphabet):
        derived = _render(template, sha)
        assert str(Version(derived)) == derived, (
            f"{label} short SHA {sha!r} derives {derived!r}, which normalises "
            f"to {str(Version(derived))!r}."
        )


def test_the_pre_fix_spelling_really_did_fail() -> None:
    """Proof that the assertions above are not vacuous.

    Every test in this file would pass against a workflow that had never been
    broken, which is the state a test is least useful in. This one fails if the
    bug it describes cannot be reproduced - if `packaging` stopped normalising,
    or if `0739392` were not actually a dangerous SHA, the rest of this file
    would be guarding nothing and should be read again rather than trusted.
    """
    before = f"{BASE}.dev0+0739392"
    assert str(Version(before)) == f"{BASE}.dev0+739392", (
        "the spelling CI used before this fix no longer reproduces the #198 "
        "failure, so the rest of this file is asserting a property that "
        "nothing threatens. Find out what changed."
    )


def test_the_alpha_version_still_sorts_below_its_base() -> None:
    """The other property the derived version exists to have.

    ``ci.yaml``'s comment claims an alpha sorts *below* the release it
    decorates, which is the whole reason for ``.dev0`` - a bare local segment
    sorts above, so pip handed both would pick the alpha. Prefixing the SHA
    must not have cost that.
    """
    template = _version_template()
    for sha in ADVERSARIAL_SHAS:
        derived = Version(_render(template, sha))
        assert derived < Version(BASE), f"{derived} is not below {BASE}"
        assert derived.is_devrelease, f"{derived} is not a dev release"
        assert derived.local is not None, f"{derived} carries no local segment"


def test_the_release_version_regex_refuses_a_leading_zero() -> None:
    """The public half of the same trap, where it was already guarded.

    ``release.yaml`` rejects ``01.0.0`` because PEP 440 would normalise it to
    ``1.0.0`` and the images would then disagree with the wheel. That guard is
    read out of the workflow here so that the two halves cannot drift apart:
    the local-segment half above was missing precisely because nothing tied
    them together.
    """
    source = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    patterns = re.findall(r"grep -Eq '(\^[^']*\$)'", source)
    assert patterns, "no anchored `grep -Eq` version pattern found in release.yaml"
    pattern = re.compile(patterns[0])

    for accepted in ("0.0.1", "1.0.0", "1.2.30", "10.0.0"):
        assert pattern.match(accepted), f"{accepted} should be a releasable version"
        assert str(Version(accepted)) == accepted, f"{accepted} is not canonical"

    for refused in ("01.0.0", "1.00.0", "1.0.00", "v1.0.0", "1.0.0-rc1", "1.0.0+a"):
        assert not pattern.match(refused), (
            f"release.yaml would accept {refused!r}. PEP 440 normalises it to "
            f"{str(Version(refused)) if refused[0] != 'v' else '(unparseable)'}"
            ", so the wheel and the image tags would disagree."
        )
