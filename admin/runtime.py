"""The Runtime a view needs, scoped to its own application.

sqladmin mounts its own Starlette application under /admin, so inside a view
``request.app`` is that inner application, not the outer FastAPI - confirmed
in Task 3 by printing ``type(request.app)`` from a view (it prints
``starlette.applications.Starlette``) and by identity: the ``Mount`` object
FastAPI dispatches into is the exact same object as ``Admin(...).admin``.

``create_app`` attaches one ``Runtime`` directly to that inner application's
``.state`` (``admin_instance.admin.state.runtime = Runtime(...)``), so each
``create_app()`` call gets its own Runtime with nothing shared at module
scope. ``get_runtime(request)`` is the read side: it returns
``request.app.state.runtime``.

An earlier version of this module held the Runtime in a module-level
global, set once by ``create_app`` and read by every view regardless of
which app was serving the request. That is fine with exactly one app alive,
but Tasks 4-8 build a fresh app per test, and two ``create_app()`` results
alive at once meant a request into app A could silently read app B's
session factory and throttle while ``app.state`` on A still showed A's own
- wrong in a way no test of a single app would ever catch. Attaching to
``app.state`` instead makes a Runtime exactly as long-lived as the app that
owns it, and reachable only from a request actually routed through it.
"""

from dataclasses import dataclass
from typing import Any

from starlette.requests import Request


@dataclass(frozen=True)
class Runtime:
    session_factory: Any
    throttle: Any
    settings: Any


def get_runtime(request: Request) -> Runtime:
    """Return the Runtime for the application serving this request.

    ``request.app`` inside a sqladmin view is sqladmin's own mounted
    Starlette application (see the module docstring), and ``create_app``
    sets ``.state.runtime`` on that same object - not on the outer
    FastAPI's state, which a view never sees.
    """
    return request.app.state.runtime
