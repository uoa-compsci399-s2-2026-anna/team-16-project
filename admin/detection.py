"""Re-export of ``db.detection``. Contract §2.3, §8.3.

The implementation moved to ``db/detection.py`` so that the public API and the
panel share one copy — §8.3 recorded the choice as open between moving and
duplicating, and named the cost of duplicating: two copies of a detection rule
drift, and the copy that stops matching is the one nobody notices. See that
module's docstring for what these checks do, what they deliberately do not do,
and why ``looks_automated`` is applied to the panel and not to ``/api/v1/``.

This file stays because ``admin.detection`` is the name the panel, its tests
and §8.3 itself all use. It re-exports rather than re-implements: every name
below **is** the object in ``db.detection``, which
``tests/db/test_detection_shared.py`` asserts with ``is`` so that this can
never quietly become a fork.
"""

from db.detection import (  # noqa: F401  - re-exported, imported for its name here
    _SCRIPTING_TOOL_MARKERS,
    _get,
    RequestRate,
    client_ip,
    looks_automated,
)

__all__ = ["RequestRate", "client_ip", "looks_automated"]
