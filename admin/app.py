"""The composition root: settings, engine, throttle, sqladmin, views.

Nothing below this module knows about the others; this is the only place
they are wired together.
"""

from fastapi import FastAPI
from sqladmin import Admin
from starlette.staticfiles import StaticFiles

from admin.backend import AdminAuth
from admin.config import Settings, load_settings
from admin.runtime import Runtime
from admin.throttle import build_throttle
from db.session import create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="Kai Commitment Admin")

    session_factory = create_session_factory(settings.database_url)
    app.state.session_factory = session_factory
    app.state.settings = settings
    # One throttle for the process. A per-request instance would hold a fresh
    # counter every time and never lock anything.
    app.state.throttle = build_throttle(settings)

    # NOTE (Task 3, deviation from the brief): Admin() mounts sqladmin's own
    # Starlette sub-application at "/admin" as the *last* line of its
    # __init__ (a Mount whose path_regex matches any "/admin/..." prefix).
    # Starlette's router tries routes in registration order and stops at
    # the first match, so if that mount is registered before this one,
    # every "/admin/static/..." request is swallowed by sqladmin's own
    # sub-app (which has no route for it) and 404s before our StaticFiles
    # mount is ever reached. Verified by running the brief's literal
    # ordering first: both static-file tests failed with 404. Mounting the
    # static files before constructing Admin() fixes it.
    app.mount(
        "/admin/static", StaticFiles(directory="admin/static"), name="brand-static"
    )

    admin = Admin(
        app,
        session_maker=session_factory,
        base_url="/admin",
        title="Kai Commitment",
        templates_dir="admin/templates",
        authentication_backend=AdminAuth(settings=settings, app=app),
    )

    # `admin.admin` is sqladmin's own mounted Starlette application - the
    # exact object `request.app` resolves to inside a view (see
    # admin/runtime.py). Attaching Runtime here rather than to a
    # module-level global means each create_app() call's Runtime lives
    # exactly as long as that call's app.
    admin.admin.state.runtime = Runtime(
        session_factory=session_factory,
        throttle=app.state.throttle,
        settings=settings,
    )

    from admin.views import ChangePasswordView, EnrolView, VerifyView

    admin.add_base_view(VerifyView)
    admin.add_base_view(ChangePasswordView)
    admin.add_base_view(EnrolView)

    # No ModelViews yet: the eleven taxonomy and factor tables are blocked on
    # B delivering db/models.py. They mount here when they arrive.

    return app
