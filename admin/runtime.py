"""Process-wide handles the page views need.

sqladmin mounts its own Starlette application under /admin, so inside a view
``request.app`` is that inner application and its ``state`` does not carry
what ``create_app`` set up on ours. This module is how the views reach the
session factory and the throttle.

``set_runtime`` is called once by ``create_app``. Tests that build several
apps get the last one, which is correct for them because each test drives
the app it just built.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Runtime:
    session_factory: Any
    throttle: Any
    settings: Any


_runtime: Runtime | None = None


def set_runtime(runtime: Runtime) -> None:
    global _runtime
    _runtime = runtime


def get_runtime() -> Runtime:
    if _runtime is None:
        raise RuntimeError("create_app() has not run; no runtime is configured")
    return _runtime
