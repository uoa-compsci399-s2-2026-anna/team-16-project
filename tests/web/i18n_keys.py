"""Every English source string the calculator can put on a screen.

**Read out of the front end, never listed here.** A hand-maintained list stops
covering the calculator the moment somebody adds a label, and nothing fails -
which is the failure mode `tests/admin/test_i18n.py` was written against and
the same one applies on this side. The catalogues are generated from what this
returns and asserted complete against it.

Four sources, because the front end has four ways of naming a string:

* ``t('...')`` in the ES modules - the ordinary case.
* ``data-i18n`` in ``index.html`` and ``methodology.html``, where the element's
  own trimmed text is the key, and ``data-i18n-attr``, where the named
  attributes' current values are.
* Four **indirect** constants: strings held in a module-level array or object
  and passed to ``t()`` by reference. They are enumerated by name below rather
  than guessed at, and `test_i18n_web.py` asserts each one still exists - a
  renamed constant has to fail loudly here, not silently drop its strings out
  of every catalogue.
"""

from __future__ import annotations

import json
import re
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
)

_LITERAL = re.compile(r"'((?:[^'\\]|\\.)*)'")

_ELEMENT = re.compile(r"<(\w+)([^>]*\bdata-i18n\b[^>]*)>(.*?)</\1>", re.S)
_ATTRIBUTE_HOST = re.compile(r"<\w+[^>]*\bdata-i18n-attr=\"([^\"]+)\"[^>]*>")


def _unescape(value: str) -> str:
    """Undo the JS string escaping the regexes left in place."""
    return value.replace("\\'", "'").replace('\\"', '"').replace("\\n", "\n")


def javascript_keys() -> set[str]:
    keys: set[str] = set()
    for path in sorted((WEB / "js").glob("*.js")):
        text = path.read_text(encoding="utf-8")
        for single, double in _CALL.findall(text):
            keys.add(_unescape(single or double))
    for relative, pattern in _INDIRECT:
        text = (WEB / relative).read_text(encoding="utf-8")
        match = pattern.search(text)
        assert match, f"{relative}: the indirect key source {pattern.pattern!r} is gone"
        keys.update(_unescape(literal) for literal in _LITERAL.findall(match.group(1)))
    return {key for key in keys if key.strip()}


def html_keys() -> set[str]:
    keys: set[str] = set()
    for path in sorted(WEB.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        for _tag, attributes, body in _ELEMENT.findall(text):
            explicit = re.search(r'data-i18n="([^"]*)"', attributes)
            key = explicit.group(1) if explicit and explicit.group(1) else body.strip()
            if key and "<" not in key:
                keys.add(key)
        for names in _ATTRIBUTE_HOST.findall(text):
            for name in names.split(","):
                attribute = name.strip()
                for host in re.findall(
                    rf'<\w+[^>]*\bdata-i18n-attr="[^"]*{re.escape(attribute)}[^"]*"[^>]*>',
                    text,
                ):
                    value = re.search(rf'\b{re.escape(attribute)}="([^"]*)"', host)
                    if value:
                        keys.add(value.group(1))
    return keys


def source_strings() -> set[str]:
    """The calculator's whole translatable surface."""
    return javascript_keys() | html_keys()


def catalogue_languages() -> list[str]:
    manifest = json.loads((LOCALES / "index.json").read_text(encoding="utf-8"))
    return [entry["language"] for entry in manifest["catalogues"]]


def catalogue(language: str) -> dict:
    return json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
