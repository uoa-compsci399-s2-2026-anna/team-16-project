"""What the site tells a visitor about where their calculation goes.

**Item ⑬ changed the fact, and three sentences kept describing the old one.**
Until stage three a calculation was contributed by the act of running it: §2.3
ruled that "one calculation is one submission… there is no consent checkbox",
so "your entries are submitted anonymously when you calculate results" was
true. `POST /api/v1/contribute` and the tick beside it reversed that. A
calculation is now *recorded*, and it joins the **public** statistics only if
the visitor offers it — `submission.is_public_contributed` defaults to FALSE
and `get_public_stats` counts nothing else.

The shared transparency notice is the one that mattered most: it renders in the
footer of `index.html`, which is the calculator, so it sits **on the results
page a few centimetres from the consent box**. A visitor read that their
calculation was already in the aggregate statistics and was then asked to opt
in to exactly that.

**The rule below is over the copy rather than over three known sentences.** A
list of three strings goes stale the moment somebody writes a fourth, which is
how this defect survived a three-pass framing sweep in the first place. Instead:
any string that tells the visitor (`you` / `your`) that their entries are
stored, submitted or recorded has to say, in the same breath, that the public
statistics are their choice. Today that predicate selects exactly the three
sentences this file was written for and nothing else, and it will select a
fourth on the day it is written.

**v1.77 added a second place the visitor's entries sit, and it is their own
browser.** `snapshot.js` keeps the answers and the last result in
`sessionStorage`, so the notice and the methodology page now say so. Those
sentences are **not** selected by the rule above and must not be: they are about
a copy that is never published and never sent anywhere, so demanding the consent
clause of them would be demanding a sentence about the public statistics from a
sentence that has nothing to do with them. They get a rule of their own instead,
and it asks the question that *is* live for browser storage — storage a visitor
cannot see has to come with what ends it: the tab closing, and the control that
empties it before then. A page that says a copy is kept and not what removes it
has told the visitor something they can do nothing about.
"""

from __future__ import annotations

import re

from tests.web import i18n_keys

#: The verbs that make a sentence a claim about what happens to what the
#: visitor typed. Deliberately not "entered" or "entries": those describe the
#: typing, not its destination, and half the form's own labels use them.
_STORAGE = ("stored", "stores", "submitted", "submits", "recorded", "records")

#: The visitor being addressed. A sentence about the statistics page's own
#: buckets ("bucket counts are entries, not calculations") is about the data
#: and not about the person, and must not be dragged in here.
_ADDRESSES_THE_VISITOR = re.compile(r"\b(you|your)\b", re.I)

#: What makes the sentence true after item ⑬: the statistics are opt-in, and the
#: choice is the visitor's. Any one of these carries it.
_CONSENT = ("only if you choose", "if you choose", "you offer", "choose to offer")

#: The exact sentences the stage-three review found, kept as a floor under the
#: rule above. The rule is what generalises; these are what it was measured on,
#: and a reworded replacement that reintroduced one of them would pass a rule
#: written slightly too loosely.
RETIRED = (
    "Your entries are submitted anonymously when you calculate results.",
    "The sector, food category and quantities entered into the calculator are "
    "stored only to produce privacy-protected aggregate statistics.",
    "This calculator stores the sector, food category and quantities entered "
    "for aggregate statistics.",
)


def _claims_about_the_visitor_s_data() -> list[str]:
    return [
        source
        for source in sorted(i18n_keys.source_strings())
        if any(verb in source.lower() for verb in _STORAGE)
        and _ADDRESSES_THE_VISITOR.search(source)
    ]


def test_the_predicate_still_selects_the_sentences_it_is_about():
    """A rule that selects nothing passes forever.

    This is the half that stops the test below going vacuous: if a rewording
    ever puts every privacy sentence outside `_STORAGE`, the assertion below
    would be trivially true while the page said anything at all.
    """
    selected = _claims_about_the_visitor_s_data()
    assert len(selected) >= 3, (
        "the storage/visitor predicate no longer finds the site's privacy "
        f"sentences, so the rule below proves nothing: {selected}"
    )


def test_every_storage_sentence_says_the_public_statistics_are_a_choice():
    """§6.2.2 and item ⑬: recorded always, published only on request.

    Before this fix the three sentences below stated the pre-stage-three fact —
    that running the calculator put the figures into the aggregate statistics —
    and one of them renders on the results page beside the control that asks
    the visitor to opt in to that very thing.
    """
    offenders = [
        source
        for source in _claims_about_the_visitor_s_data()
        if not any(phrase in source.lower() for phrase in _CONSENT)
    ]
    assert not offenders, (
        "these sentences tell the visitor their entries are kept without saying "
        "the public statistics are their choice, which is what the contribute "
        "control asks them: " + " || ".join(offenders)
    )


def test_no_page_still_carries_a_retired_sentence():
    """The three exact claims the review measured, asserted gone.

    Absence rather than presence, for `test_the_download_no_longer_hard_codes_
    one_name`'s reason: a file carrying both the old sentence and a new one
    would satisfy any presence check while still showing the old one.
    """
    corpus = "\n".join(sorted(i18n_keys.source_strings()))
    still_there = [sentence for sentence in RETIRED if sentence in corpus]
    assert not still_there, (
        "the pre-consent wording is still on a page: " + " || ".join(still_there)
    )


def test_the_statistics_page_describes_contributed_calculations():
    """**Finding 4**, and it is a `<meta>` tag, so no visitor ever sees it fail.

    `stats.html`'s description is what a search engine and a link preview quote,
    and it said "calculations run in the Kai Commitment food waste impact
    calculator" — the sweep's own retired phrasing, surviving in the one place
    reading the page cannot show you. The aggregate counts only the calculations
    whose visitors offered them.
    """
    keys = i18n_keys.marked_elements(i18n_keys.WEB / "stats.html").keys
    descriptions = [key for key in keys if key.startswith("Explore anonymised")]
    assert len(descriptions) == 1, f"the statistics description is gone: {sorted(keys)[:5]}"
    description = descriptions[0]
    assert "run in" not in description, (
        "the statistics page still describes its sample as every calculation "
        f"run in the tool: {description!r}"
    )
    assert "contributed to" in description, (
        f"the statistics description does not say the sample was offered: {description!r}"
    )


def test_the_page_no_longer_asks_for_data_the_visitor_already_gave():
    """**Finding 2.** `#total-input` is collected on step 2 (§6.2's
    `total_input_kg`) and persisted. At the time this test was written the
    engine had no consumer for it, so the results page printed "Total food
    handled data is required." directly above a money block visibly consuming
    the value that visitor had just typed, and the fix's replacement sentence
    said the share was not reported *yet* — true then, false now.

    The engine has since grown `totals.production_share_percent`, computed
    from the two masses the visitor typed (§4.6), so the "yet" sentence is
    itself now a retired claim rather than the fix: this asserts it is gone
    the same way `test_no_page_still_carries_a_retired_sentence` above asserts
    the other three are, and asserts the sentence that actually replaced it —
    describing the share as a ratio the placeholder factors do not touch — is
    the one on the page instead.
    """
    corpus = "\n".join(sorted(i18n_keys.source_strings())).lower()
    assert "total food handled data is required" not in corpus
    assert "until total food handled data is supplied" not in corpus
    assert "this calculator does not report waste as a share of food handled yet." not in corpus, (
        "the page still claims the share is not reported, and it now is"
    )
    assert (
        "waste as a share of food handled is a ratio of the two masses you "
        "typed, not a factor-based figure, so the placeholder data above "
        "does not affect it."
    ) in corpus, "the sentence that replaced the retired claim is missing"
#: Where the visitor's own copy is said to live. Three spellings because the
#: notice, which has one sentence to spend, and the methodology section, which
#: has a paragraph, do not say it the same way.
_IN_THE_BROWSER = re.compile(
    r"\b(browser tab|your browser|this tab|the tab you are using)\b", re.I
)

#: And what it is said to be doing there. The conjunction is what keeps the
#: predicate off "opens in a new tab" (a link, not a copy) and off the
#: contribute sentence's "an anonymous copy of your results" (a copy, but one
#: going to the server rather than staying in the browser). Both are in the
#: corpus today and neither is a browser-storage claim.
_KEPT_THERE = re.compile(r"\b(stay|stays|keep|keeps|kept|copy)\b", re.I)

#: The two things such a claim has to carry. `sessionStorage` ends with the tab
#: (`snapshot.js`), and `resetCalculator()` removes both keys before then - the
#: header's Clear all data button and the results page's Start over. A sentence
#: that names neither leaves the visitor unable to act on what it just told them.
_WHAT_ENDS_IT = "close the tab"
_WHAT_REMOVES_IT = "clear all data"


def _claims_about_the_visitor_s_browser() -> list[str]:
    return [
        source
        for source in sorted(i18n_keys.source_strings())
        if _IN_THE_BROWSER.search(source)
        and _KEPT_THERE.search(source)
        and _ADDRESSES_THE_VISITOR.search(source)
    ]


def test_the_browser_storage_predicate_still_selects_the_sentences_it_is_about():
    """The non-vacuity half, for the same reason the one above it has one.

    Two sentences carry this claim today: the footer notice's second span on all
    four public pages, and the paragraph under `#what-we-record`. A rewording
    that put both outside this predicate would leave the rule below passing over
    an empty list while the pages said whatever they liked.
    """
    selected = _claims_about_the_visitor_s_browser()
    assert len(selected) >= 2, (
        "the browser-storage predicate no longer finds the copy about what the "
        f"visitor's own browser keeps, so the rule below proves nothing: {selected}"
    )


def test_every_browser_storage_sentence_says_what_ends_it():
    """§7.2a: per tab, gone when the tab closes, removed by Clear all data.

    The storage this describes is invisible - no cookie banner, nothing in the
    interface that shows it - so the sentence that admits it exists is the only
    place a visitor can learn how it ends. Both facts are asserted because they
    answer different questions: the tab close is what happens if they do
    nothing, and the button is what they can do now.
    """
    offenders = [
        source
        for source in _claims_about_the_visitor_s_browser()
        if _WHAT_ENDS_IT not in source.lower()
        or _WHAT_REMOVES_IT not in source.lower()
    ]
    assert not offenders, (
        "these sentences tell the visitor their answers are kept in their own "
        "browser without saying both that closing the tab ends it and which "
        "control removes it: " + " || ".join(offenders)
    )
