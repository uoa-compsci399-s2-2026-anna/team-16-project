"""The composition root: settings, engine, throttle, sqladmin, views.

Nothing below this module knows about the others; this is the only place
they are wired together.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqladmin import Admin
from starlette.staticfiles import StaticFiles

from admin.backend import AdminAuth
from admin.bootstrap import ensure_bootstrap_admins
from admin.calc_client import HttpCalculateClient
from admin.cli import report_bootstrap_result
from admin.config import Settings, load_settings
from admin.protection import ProtectionMiddleware
from admin.runtime import Runtime
from admin.throttle import build_throttle
from db.session import create_session_factory

#: This package's own directory. Both the static files and the templates live
#: inside it and are shipped as package data, so they are located relative to
#: this file and never relative to the working directory - an installed
#: package has no idea where the process was started from, and a panel that
#: only starts from one directory is a panel that cannot be installed.
#:
#: admin/views.py and admin/dryrun_views.py have always resolved their
#: Jinja2Templates this way; the two mounts below were the outliers, and
#: `create_app()` from any other directory raised
#: "RuntimeError: Directory 'admin/static' does not exist".
_PACKAGE_DIR = Path(__file__).parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    session_factory = create_session_factory(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        """Contract 8.3, "Bootstrap": trigger is application start.

        Wired here and not left to the operator because the alternative the
        contract names is "depending on whoever installs it remembering to
        run the CLI twice" - and a deployment where that is forgotten comes
        up as a panel nobody can enter, with no email system to recover
        through and only shell access left.

        A session of its own, committed here: nothing else exists yet to own
        a transaction, and ``ensure_bootstrap_admins`` flushes but never
        commits (admin/accounts.py's convention - the caller owns the
        transaction).

        Deliberately unguarded. A database that cannot be reached raises out
        of here and the process fails to start, which is the loud failure;
        swallowing it would produce a panel that serves a login page and
        refuses every credential, the hardest state to diagnose from
        outside. ``ensure_bootstrap_admins`` is itself idempotent - it
        returns [] whenever an active administrator exists - so a restart
        creates nothing and, via report_bootstrap_result, prints nothing.

        Note that this runs only under a real ASGI server: nothing in
        ``create_app()`` triggers it, and httpx's ASGITransport (what the
        test suite drives apps with) never speaks the lifespan protocol. A
        test that switched to Starlette's TestClient *would* run it, and
        against the empty database the schema fixture leaves behind it would
        print live credentials into the test output.
        """
        with session_factory() as db:
            created = ensure_bootstrap_admins(db)
            db.commit()
        report_bootstrap_result(created)
        yield

    app = FastAPI(title="Kai Commitment Admin", lifespan=lifespan)

    app.state.session_factory = session_factory
    app.state.settings = settings
    # One throttle for the process. A per-request instance would hold a fresh
    # counter every time and never lock anything.
    app.state.throttle = build_throttle(settings)

    # Global, and installed on the *outer* app - not passed into Admin()'s
    # own ``middlewares=`` list. Either placement runs before sqladmin's
    # inner SessionMiddleware (see admin/protection.py's module docstring
    # for why: a Mount hands the request to the inner app only after the
    # outer app's own middleware has already run), so where it sits doesn't
    # change what it can read - this is simply the natural home for
    # something that has to see every request under /admin, static files
    # included, ahead of the mount-ordering note just below.
    app.add_middleware(
        ProtectionMiddleware, session_factory=session_factory, settings=settings
    )

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
        "/admin/static",
        StaticFiles(directory=str(_PACKAGE_DIR / "static")),
        name="brand-static",
    )

    admin = Admin(
        app,
        session_maker=session_factory,
        base_url="/admin",
        title="Kai Commitment",
        templates_dir=str(_PACKAGE_DIR / "templates"),
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
        calc_client=HttpCalculateClient(base_url=settings.api_base_url),
    )

    from admin.views import ChangePasswordView, EnrolView, VerifyView

    admin.add_base_view(VerifyView)
    admin.add_base_view(ChangePasswordView)
    admin.add_base_view(EnrolView)

    from admin.dryrun_views import CompareView, DryRunView

    admin.add_base_view(DryRunView)
    admin.add_base_view(CompareView)

    from admin.modelviews import AuditLogAdmin

    admin.add_view(AuditLogAdmin)

    from admin.accounts_view import StaffAdmin

    admin.add_view(StaffAdmin)

    from admin.blocklist_views import IpBlockAdmin

    admin.add_view(IpBlockAdmin)

    # The six taxonomy views of contract §8.1 landed in E-4 (this block). All
    # six of E-5's factor views - factor_set, factor_upstream,
    # factor_downstream, constant, formula, equivalence - are registered just
    # below; a read-only submission view is still to come. All inherit
    # AuditedModelView, so each arrives already audited.
    from admin.taxonomy_views import (
        DestinationAdmin, DestinationGroupAdmin, FoodCategoryAdmin, MetricAdmin,
        SectorAdmin, UnitPresetAdmin,
    )

    for view in (SectorAdmin, FoodCategoryAdmin, DestinationGroupAdmin,
                 DestinationAdmin, MetricAdmin, UnitPresetAdmin):
        admin.add_view(view)

    from admin.factor_views import (
        ConstantAdmin, EquivalenceAdmin, FactorDownstreamAdmin,
        FactorSetAdmin, FactorUpstreamAdmin, FormulaAdmin,
    )

    for view in (FactorSetAdmin, FactorUpstreamAdmin, FactorDownstreamAdmin,
                 ConstantAdmin, FormulaAdmin, EquivalenceAdmin):
        admin.add_view(view)

    from admin.comparison_views import ComparisonScenarioAdmin, ComparisonScenarioLineAdmin

    for view in (ComparisonScenarioAdmin, ComparisonScenarioLineAdmin):
        admin.add_view(view)

    return app
