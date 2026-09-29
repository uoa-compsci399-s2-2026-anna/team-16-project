"""The composition root: settings, engine, throttle, sqladmin, views.

Nothing below this module knows about the others; this is the only place
they are wired together.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from admin.backend import AdminAuth
from admin.bootstrap import ensure_bootstrap_admins
from admin.calc_client import HttpCalculateClient
from admin.cli import report_bootstrap_result
from admin.config import Settings, load_settings
from admin.csrf import issue_token
from admin import i18n as admin_i18n
from admin.importing import KaiAdmin
from admin.language_view import register as register_language_route
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

logger = logging.getLogger(__name__)

#: Logged once at start-up when the staff session cookie is being issued
#: without the `Secure` attribute. The counterpart to
#: `api/app.py`'s `_UNTRUSTED_PROXY_WARNING`, and it exists for the same
#: reason: a setting whose safe value had to be relaxed for one environment
#: must not be silent in the one it was not relaxed for.
#:
#: The concrete case this is written for. The shipped container arrangement
#: (docker/compose.yaml) sets `SESSION_HTTPS_ONLY=false`, because out of the
#: box the panel is served over plain http and a `Secure` cookie is one the
#: browser accepts and then never sends back - the login form takes the
#: password and returns to the login form, a panel nobody can enter. That
#: relaxation is correct for a first run and wrong the moment TLS is in
#: front, and an operator who fronts this with TLS and never reads
#: compose.yaml would keep the weakened cookie indefinitely with nothing
#: anywhere saying so.
#:
#: A warning and not a refusal, exactly like the API's: there is no value
#: this could be defaulted to that is right in both environments, so the
#: hazard is operational and the only thing to do about it is to make it
#: impossible to miss.
_INSECURE_SESSION_COOKIE_WARNING = (
    "SESSION_HTTPS_ONLY is false, so the staff session cookie is issued "
    "WITHOUT the Secure attribute and a browser will send it over plain "
    "http. That cookie carries admin access. This is the correct setting "
    "only where the panel is genuinely served over http - local development, "
    "or a first container run before TLS is arranged. Behind TLS it means one "
    "http:// navigation on the admin origin hands over a live staff session, "
    "including to anyone on the same network. Set SESSION_HTTPS_ONLY=true as "
    "soon as TLS terminates in front of this panel."
)


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
            created = ensure_bootstrap_admins(db, secret_key=settings.secret_key)
            db.commit()
        report_bootstrap_result(created)
        yield

    app = FastAPI(title="Kai Commitment Admin", lifespan=lifespan)

    app.state.session_factory = session_factory
    app.state.settings = settings
    # One throttle for the process. A per-request instance would hold a fresh
    # counter every time and never lock anything.
    app.state.throttle = build_throttle(settings)

    # Emitted here rather than in the lifespan hook so that it also reaches
    # anything that builds an app without running one - and unconditionally on
    # the value, never on whether a proxy is detected, because this process
    # cannot tell whether TLS terminates in front of it.
    if not settings.session_https_only:
        logger.warning(_INSECURE_SESSION_COOKIE_WARNING)

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

    # Added last, so it is the OUTERMOST middleware: Starlette builds the
    # stack in reverse registration order. That ordering is the point - a
    # request ProtectionMiddleware refuses still has a language negotiated by
    # the time its refusal is rendered, and the login page (the one page a
    # locked-out person can still reach) is the page most in need of being
    # readable by somebody who does not read English.
    #
    # Outermost is also the only position from which `Vary: Accept-Language`
    # reaches EVERY response, including the ones the inner middlewares
    # short-circuit. A 403 that omits Vary is the response a shared cache is
    # most likely to hold and hand to the next visitor.
    app.add_middleware(admin_i18n.LanguageMiddleware)

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

    # The language chooser's endpoint, registered here for the same
    # mount-ordering reason as the static files above - sqladmin's mount matches
    # every /admin/... prefix and Starlette stops at the first match - and for a
    # second reason of its own: @expose wraps every sqladmin route in
    # login_required, and this one has to work before anybody has logged in.
    # admin/language_view.py says why it carries no CSRF token.
    register_language_route(app)

    # KaiAdmin rather than sqladmin's Admin: one overridden route, the one
    # that carries a bulk CSV upload. admin/importing.py says what it adds and
    # why none of the three additions can live on the view instead.
    admin = KaiAdmin(
        app,
        session_maker=session_factory,
        base_url="/admin",
        title="Kai Commitment",
        templates_dir=str(_PACKAGE_DIR / "templates"),
        authentication_backend=AdminAuth(settings=settings, app=app),
    )

    # The login page is the one page a locked-out person can still reach, and
    # it has to state the lockout rule without either number written into the
    # template - a hard-coded "5 attempts" or "15 minutes" is confidently
    # wrong the first time a deployment sets LOGIN_MAX_FAILURES or
    # LOGIN_LOCKOUT_MINUTES to anything else. Neither can come through the
    # view context: sqladmin owns the /admin/login route and passes that
    # template a context of its own ({"error": ...} and nothing more,
    # sqladmin/application.py), so a Jinja global is the only seam. Scoped to
    # this app the same way the Runtime below is - sqladmin builds one
    # Jinja2Templates per Admin instance (init_templating_engine), so two
    # create_app() results never share it.
    admin.templates.env.globals["login_lockout_minutes"] = (
        settings.login_lockout_minutes
    )
    admin.templates.env.globals["login_max_failures"] = settings.login_max_failures

    # THE CSRF TOKEN, FOR THE TEMPLATES SQLADMIN RENDERS ITSELF.
    #
    # Every hand-written screen in this panel is rendered by its own view,
    # which puts `csrf_token` in the context it passes (admin/views.py,
    # admin/accounts_view.py, admin/factor_views.py, admin/self_service_view.py).
    # sqladmin's own list page is rendered by sqladmin, from a context this
    # project never sees, so a template shadowed under templates/sqladmin/ has
    # no other way to reach the session's token - the same seam, and the same
    # reason, as the two login globals above.
    #
    # A callable taking the request rather than a value: a Jinja global is
    # evaluated once per render and there is one environment per app, so a
    # bare string here would be one session's token handed to every session.
    # `issue_token` mints on first use and is idempotent afterwards
    # (admin/csrf.py), and the render happens inside the endpoint, before
    # SessionMiddleware writes the response's cookie.
    admin.templates.env.globals["kaicalc_csrf_token"] = (
        lambda request: issue_token(request.session)
    )

    # TRANSLATION. Contract open item O-8, and admin/i18n.py says why it is
    # shaped this way rather than as gettext or as sqladmin's own i18n.
    #
    # `install_gettext_callables` REPLACES the null translations sqladmin
    # installed for itself a few lines earlier in its own constructor
    # (application.py's init_templating_engine: with no I18nConfig it calls
    # install_null_translations, which makes `_()` the identity). Ours goes in
    # afterwards and wins - which is what puts sqladmin's OWN twenty-five
    # strings ("Save", "Delete", the pagination line, the empty-list text)
    # through this panel's catalogue without forking a single one of its
    # templates.
    #
    # newstyle=True because that is what sqladmin's templates are written
    # against: Jinja applies `translated % variables` itself after our
    # callable returns, so `_("Showing %(start)s to %(end)s of %(count)s
    # items")` still interpolates. A translation that damages one of those
    # placeholders is a rendering error rather than a wrong word, which is
    # why tests/admin/test_i18n.py asserts they survive translation.
    # The language globals go in alongside, named rather than reusing
    # sqladmin's `get_locale`/`get_locale_display_name`: those are only
    # defined when an I18nConfig is passed, and this panel deliberately
    # passes none.
    admin_i18n.install(admin.templates.env)

    # `admin.admin` is sqladmin's own mounted Starlette application - the
    # exact object `request.app` resolves to inside a view (see
    # admin/runtime.py). Attaching Runtime here rather than to a
    # module-level global means each create_app() call's Runtime lives
    # exactly as long as that call's app.
    admin.admin.state.runtime = Runtime(
        session_factory=session_factory,
        throttle=app.state.throttle,
        settings=settings,
        # `secret_key` is what makes a dry run succeed against a real API
        # (open item O-9). The panel signs a short-lived proof with it; the API
        # verifies that proof with the same value, which the deployment
        # guarantees is one value by mounting one `secret` volume into both
        # services. A mismatch here is not a subtle bug - every dry run 401s.
        calc_client=HttpCalculateClient(
            base_url=settings.api_base_url, secret_key=settings.secret_key
        ),
    )

    # Registered first, and that is the whole of why it is here rather than
    # further down: sqladmin builds the menu in registration order
    # (`_build_menu`), so this puts "Getting started" at the top of the
    # navigation instead of below fourteen model screens. The panel index
    # links it as well (templates/sqladmin/index.html) - a first-run
    # walkthrough that has to be looked for is not one.
    from admin.getting_started_view import GettingStartedView

    admin.add_base_view(GettingStartedView)

    from admin.views import ChangePasswordView, EnrolView, VerifyView

    admin.add_base_view(VerifyView)
    admin.add_base_view(ChangePasswordView)
    admin.add_base_view(EnrolView)

    # The signed-in account's own security screen. Registered as a base view
    # rather than beside StaffAdmin because it is not an administrator
    # screen: `staff` reaches it, and it acts only on the account in the
    # session. See admin/self_service_view.py's docstring.
    from admin.self_service_view import SecurityView

    admin.add_base_view(SecurityView)

    from admin.dryrun_views import CompareView, DryRunView

    admin.add_base_view(DryRunView)
    admin.add_base_view(CompareView)

    # The deployment read-back. A BaseView rather than a model screen because
    # it reads no table: everything on it comes from this request's own
    # headers and from `settings`. Registered next to the audit log and the
    # blocklist because it shares their role floor - it describes the
    # deployment's security posture, which contract §8.3 makes an
    # administrator's concern - and `AdministratorOnly` keeps it out of a
    # staff member's sidebar.
    from admin.deployment_view import DeploymentView

    admin.add_base_view(DeploymentView)

    from admin.modelviews import AuditLogAdmin

    admin.add_view(AuditLogAdmin)

    from admin.accounts_view import StaffAdmin

    admin.add_view(StaffAdmin)

    from admin.blocklist_views import IpBlockAdmin

    admin.add_view(IpBlockAdmin)

    from admin.submission_views import SubmissionAdmin

    admin.add_view(SubmissionAdmin)

    # The taxonomy views of contract §8.1 landed in E-4 (this block) as six;
    # `FoodItemAdmin` (v1.54 part two) makes seven. All
    # six of E-5's factor views - factor_set, factor_upstream,
    # factor_downstream, constant, formula, equivalence - are registered just
    # below. The submission view this comment used to call "still to come" is
    # the line above. All inherit AuditedModelView, so each arrives already
    # audited.
    from admin.taxonomy_views import (
        DestinationAdmin, DestinationGroupAdmin, FoodCategoryAdmin,
        FoodItemAdmin, MetricAdmin, SectorAdmin, UnitPresetAdmin,
    )

    #: `FoodItemAdmin` (v1.54) sits immediately after `FoodCategoryAdmin`
    #: because it is that table's refinement: a food item's only required field
    #: is the category it belongs to, and a staff member authoring one is
    #: looking at the category list one line above.
    for view in (SectorAdmin, FoodCategoryAdmin, FoodItemAdmin,
                 DestinationGroupAdmin, DestinationAdmin, MetricAdmin,
                 UnitPresetAdmin):
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

    # After every view is registered, and it has to be after: this walks the
    # registered set. sqladmin builds its menu from these same attributes, so
    # the navigation, the page headings and the delete modal all follow from
    # here. See admin/i18n.py::_TranslatedAttribute for why the attribute
    # rather than the catalogue is the thing that has to change.
    admin_i18n.translate_view_names(admin.views)

    return app
