"""The client's reputational red line, as a predicate over text.

**§6.4's copy constraint, owner D.** The subject of the statistics page must be
the calculator itself — "Across the 1,247 calculations run in this tool…" — and
never "Distribution of food waste destinations in New Zealand". The sample is
self-selected, so a sentence that makes New Zealand the subject of a food-waste
quantity or distribution is a claim the data cannot support, and an absolute
headline tonnage is the figure most likely to be screenshotted out of context.

**Why this is a module and not a regex inside one test.** The guard it replaces
read::

    not re.search(r"(?:distribution|statistics|picture)\\s+of\\s+"
                  r"(?:food waste\\s+)?(?:in\\s+)?new zealand", combined)

which pins one exact phrase order and nothing else. Every one of these passes
it, and every one of them breaks the red line:

* "Food waste destinations across New Zealand"
* "Where New Zealand's food waste goes"
* "New Zealand food waste by destination"
* "Total food waste in New Zealand: 884,200 tonnes"

They are ``COUNTEREXAMPLES`` below and are asserted to be caught, so the guard
cannot quietly go back to being decorative.

**The rule, at word level rather than phrase level.**

1. Split into sentences. A colon counts as a boundary, which is what turns
   "Total food waste in New Zealand: 884,200 tonnes" into two claims — both
   still scanned, and the half naming the country still carries "food waste".
2. In a sentence naming New Zealand, look **three words either side** of the
   mention for a food-waste quantity or distribution word.
3. Unless the sentence negates before the mention, that is a violation.

Three words, not the whole sentence, and the width is what the two directions
of failure settle between them. The whole sentence catches "This tool uses New
Zealand definitions of food waste", which is a statement about definitions and
not about a tonnage; four words catches it too. Three is the widest window that
still catches all four counterexamples and leaves ``PERMITTED`` alone.

**Sentence scope is what makes the negation exemption safe.** A negation cannot
reach across a full stop into the next claim, so "This is not a survey. New
Zealand wasted 884,200 tonnes." is still caught.

**Scope.** This is §6.4's constraint and §6.4 is about the statistics page, so
that is where it is applied. `index.html`'s "…food waste in New Zealand food
businesses" names an *audience*, not a quantity, and is legitimate; telling an
audience phrase from a claim is a distinction this predicate does not attempt
and would need before it could be run over every page.

Matching is on whole words throughout, so `waste` does not match `wastewater`.
"""

from __future__ import annotations

import re

#: The mention itself, possessive included. "New Zealand's food waste" is the
#: same claim as "food waste in New Zealand", and the guard this replaces
#: caught neither.
_NEW_ZEALAND = re.compile(r"\bnew\s+zealand(?:'s|’s)?\b", re.I)

#: A full stop, question mark, exclamation mark, newline — or a colon, which is
#: how a headline tonnage is actually written.
_SENTENCE = re.compile(r"[.!?\n\r]+|(?<=\S):\s")

#: Words that make a phrase a claim about a quantity or a distribution of food
#: waste. "food" is deliberately absent: it is the subject of the whole site and
#: carries no quantity on its own.
_QUANTITY_WORDS = frozenset(
    """
    waste wasted wastes tonne tonnes tonnage kilogram kilograms kg
    destination destinations distribution distributions breakdown breakdowns
    share shares percentage percentages proportion proportions
    statistics figures total totals amount amounts quantity quantities
    picture survey sample landfill composted discarded
    """.split()
)

#: A sentence carrying one of these *before* the mention is denying the claim
#: rather than making it. "They are **not** a survey or a picture of food waste
#: across New Zealand" is the page's own sentence and the only one on it that
#: may name the country.
_NEGATIONS = frozenset(
    """
    not never no none neither nor cannot isn't aren't wasn't weren't
    doesn't don't didn't rather instead unlike
    """.split()
)

#: See the module docstring: the widest window that catches all four
#: counterexamples and leaves every permitted sentence alone.
_WINDOW = 3

_WORD = re.compile(r"[A-Za-z']+")


def _words(text: str) -> list[str]:
    return [match.group(0).lower().strip("'") for match in _WORD.finditer(text)]


def violations(text: str) -> list[str]:
    """Every sentence in ``text`` that makes New Zealand the subject of a
    food-waste quantity or distribution.

    Returns the offending sentences rather than a boolean, so a failure names
    what it caught instead of only that it caught something.
    """
    found: list[str] = []
    for sentence in _SENTENCE.split(text or ""):
        mention = _NEW_ZEALAND.search(sentence or "")
        if not mention:
            continue
        before = _words(sentence[: mention.start()])
        after = _words(sentence[mention.end():])
        window = before[-_WINDOW:] + after[:_WINDOW]
        if not _QUANTITY_WORDS.intersection(window):
            continue
        if _NEGATIONS.intersection(before):
            continue
        found.append(" ".join(sentence.split()))
    return found


#: The four sentences the guard this replaces let through, so the
#: counterexamples are a test rather than a comment.
COUNTEREXAMPLES = (
    "Food waste destinations across New Zealand",
    "Where New Zealand's food waste goes",
    "New Zealand food waste by destination",
    "Total food waste in New Zealand: 884,200 tonnes",
)

#: Sentences that name New Zealand and must NOT be caught, so the guard cannot
#: be "fixed" into one that simply forbids the words. The first is the
#: statistics page's own copy, and the reason the page passes at all.
PERMITTED = (
    "These figures describe a self-selected sample of calculations contributed to this tool. "
    "They are not a survey or a picture of food waste across New Zealand.",
    "This tool uses New Zealand definitions of food waste.",
    "Kai Commitment is a New Zealand programme.",
)
