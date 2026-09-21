"""Every English source string the calculator can put on a screen.

**Read out of the front end, never listed here.** A hand-maintained list stops
covering the calculator the moment somebody adds a label, and nothing fails -
which is the failure mode `tests/admin/test_i18n.py` was written against and
the same one applies on this side. The catalogues are generated from what this
returns and asserted complete against it.

Four mechanisms, because the front end has four ways of naming a string:

* ``t('...')`` in the ES modules - the ordinary case.
* ``data-i18n`` in ``index.html`` and ``methodology.html``, where the element's
  own trimmed text is the key.
* ``data-i18n-attr`` in the same two files, where the named attributes' current
  values are.
* The **indirect** constants in ``_INDIRECT``: strings held in a module-level
  array or object and passed to ``t()`` by reference. They are enumerated by
  name below rather than guessed at, and `test_i18n_web.py` asserts each one
  still exists - a renamed constant has to fail loudly here, not silently drop
  its strings out of every catalogue. **How many there are is deliberately not
  written down here**: this paragraph used to say "four indirect constants"
  above a tuple that had since grown past four, because a count in prose beside
  a list that grows is a comment that goes quietly wrong. Read ``_INDIRECT``.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"
LOCALES = WEB / "locales"

#: `t('...')`, `t("...")`, with escaped quotes inside honoured. Deliberately
#: does NOT match `t(SOMETHING)` - an indirect key is listed below instead, so
#: that adding one is a decision rather than an accident.
_CALL = re.compile(r"\bt\(\s*'((?:[^'\\]|\\.)*)'|\bt\(\s*\"((?:[^\"\\]|\\.)*)\"")

#: The module-level constants whose contents reach `t()` by reference.
_INDIRECT = (
    ("js/view.js", re.compile(r"export const STEPS = \[(.*?)\]", re.S)),
    ("js/results.js", re.compile(r"const TAB_LABELS = \{(.*?)\}", re.S)),
    ("js/results.js", re.compile(r"const DEMONSTRATION_NOTICE = (.*?)\n", re.S)),
    ("js/i18n.js", re.compile(r"export const MACHINE_TRANSLATION_NOTICE =\s*(.*?)\n", re.S)),
    # The language chooser's three strings. Constants rather than inline
    # literals because the chooser builds its own markup, so `t('Language')`
    # never appears as a literal call for `_CALL` to find.
    #
    # **Anchored on `export const NAME =` and terminated at the newline**, the
    # same shape as the notice above. A looser pattern - say `LANGUAGE_LABEL`
    # anywhere - would match this module's own prose about the constant and
    # keep the key alive after the code that renders it was deleted, which is
    # exactly the JSDoc defect `_strip_comments` exists to close.
    ("js/i18n.js", re.compile(r"export const LANGUAGE_LABEL =\s*(.*?)\n", re.S)),
    ("js/i18n.js", re.compile(r"export const FOLLOW_SYSTEM_LABEL =\s*(.*?)\n", re.S)),
    ("js/i18n.js", re.compile(r"export const MACHINE_TRANSLATED_OPTION =\s*(.*?)\n", re.S)),
)

_LITERAL = re.compile(r"'((?:[^'\\]|\\.)*)'")

#: Comments, stripped before anything is scanned for a key.
#:
#: **This is not tidiness.** ``web/js/i18n.js`` documents itself with
#: ``t('Start calculator')`` inside a JSDoc block, so a scan of the raw text
#: found that key whether or not any code still rendered it - and a mutation
#: that deleted the real ``t()`` call from ``calculator.js`` survived the whole
#: suite because of it. A key has to be evidence that something renders the
#: string, and a sentence about the string is not that.
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"^\s*//.*$", re.M)

#: HTML elements that never carry a closing tag, so nothing may be nested in
#: them and nothing waits for `</meta>`.
_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}


class _MarkedElements(HTMLParser):
    """The `data-i18n` keys in one HTML file, parsed rather than matched.

    **This was a regex and the regex lost keys.** ``<(\\w+)([^>]*\\bdata-i18n\\b
    [^>]*)>(.*?)</\\1>`` matched the *outermost* marked element and consumed
    everything up to its closing tag, so a marked element inside another marked
    element was never scanned. That is not hypothetical: the public navigation is
    a ``<nav data-i18n-attr="aria-label">`` — which the pattern matched, because
    ``\\bdata-i18n\\b`` is happily satisfied by ``data-i18n-attr`` — wrapping four
    ``<a data-i18n>`` links. The nav swallowed all four, and `Home`,
    `Calculator`, `Statistics` and `Documentation` were absent from every
    catalogue on all three pages while every test stayed green: the coverage test
    cannot ask for a key nobody extracted, and the stale-key test would have
    *failed* had anyone translated them.

    `html.parser` reads the tree the browser reads, so a marked element nested in
    another is found wherever it is, and `nested` records the one structure the
    runtime cannot survive - see `has_element_children`.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.keys: set[str] = set()
        #: (outer tag, inner tag) for every ELEMENT child of a `data-i18n`
        #: element. `applyToDocument` assigns `element.textContent`, which
        #: deletes those children, so this is a defect wherever it appears.
        self.has_element_children: list[tuple[str, str]] = []
        self._open: list[str] = []
        #: depth -> [tag, explicit key or '', collected text]
        self._marked: dict[int, list] = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        for name in (attributes.get("data-i18n-attr") or "").split(","):
            value = attributes.get(name.strip())
            if value:
                self.keys.add(value)
        for record in self._marked.values():
            self.has_element_children.append((record[0], tag))
        if tag not in _VOID:
            self._open.append(tag)
            if "data-i18n" in attributes:
                self._marked[len(self._open)] = [tag, attributes["data-i18n"] or "", []]

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and self._open and self._open[-1] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in _VOID or tag not in self._open:
            return
        while self._open:
            depth = len(self._open)
            open_tag = self._open.pop()
            record = self._marked.pop(depth, None)
            if record is not None:
                key = record[1] or "".join(record[2]).strip()
                if key:
                    self.keys.add(key)
            if open_tag == tag:
                return

    def handle_data(self, data):
        for record in self._marked.values():
            record[2].append(data)


def _strip_comments(text: str) -> str:
    """Remove JSDoc blocks and whole-line `//` comments.

    Both front-end modules explain themselves at length, and an explanation
    is not a render.
    """
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", text))


def _unescape(value: str) -> str:
    """Undo the JS string escaping the regexes left in place."""
    return value.replace("\\'", "'").replace('\\"', '"').replace("\\n", "\n")


def javascript_keys() -> set[str]:
    keys: set[str] = set()
    for path in sorted((WEB / "js").glob("*.js")):
        text = _strip_comments(path.read_text(encoding="utf-8"))
        for single, double in _CALL.findall(text):
            keys.add(_unescape(single or double))
    for relative, pattern in _INDIRECT:
        text = _strip_comments((WEB / relative).read_text(encoding="utf-8"))
        match = pattern.search(text)
        assert match, f"{relative}: the indirect key source {pattern.pattern!r} is gone"
        keys.update(_unescape(literal) for literal in _LITERAL.findall(match.group(1)))
    return {key for key in keys if key.strip()}


def _parse(path: Path) -> _MarkedElements:
    parser = _MarkedElements()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser


def html_pages() -> list[Path]:
    return sorted(WEB.glob("*.html"))


def marked_elements(path: Path) -> _MarkedElements:
    """One page's parse, exposed so a test can assert on its structure."""
    return _parse(path)


def html_keys() -> set[str]:
    keys: set[str] = set()
    for path in html_pages():
        keys |= _parse(path).keys
    return keys


def source_strings() -> set[str]:
    """The calculator's whole translatable surface."""
    return javascript_keys() | html_keys()


def catalogue_languages() -> list[str]:
    manifest = json.loads((LOCALES / "index.json").read_text(encoding="utf-8"))
    return [entry["language"] for entry in manifest["catalogues"]]


def catalogue(language: str) -> dict:
    return json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
