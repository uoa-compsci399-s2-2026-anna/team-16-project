"""``/admin/getting-started`` — the first-run walkthrough.

Written for one reader: a Kai Commitment staff member who has just been
handed this system and has nobody to ask. It explains four things in the
order they have to happen, and sends the reader to the screen that carries
each explanation in full rather than repeating it — Task 4 put a description
under every editable box and Task 5 put a block at the top of every screen
whose order matters, and a third copy of either would be a third copy to
keep true.

**What this view holds and the template does not.** Nothing. There is no
context, no database read and no branch: the page is prose with links. It is
a ``BaseView`` only because that is how this panel adds a route and a menu
entry, and it is registered first in ``create_app`` so that "Getting started"
sits at the top of the navigation rather than under fourteen model screens —
"visible without knowing it exists" is the whole requirement, and a reader
who has to be told where the walkthrough is has not been helped.

**Rendered through ``self.templates``, not the module-level
``Jinja2Templates`` the other hand-written views use.** Those views' pages
extend ``brand/base.html``, which needs nothing but ``request``. This one
extends ``sqladmin/layout.html`` for its navigation, and that template reads
``admin`` (the menu, the title), ``i18n_config`` and ``get_locale`` — all of
them globals sqladmin installs on *its own* environment
(``Admin.init_templating_engine``). A second environment does not have them,
and the page fails at render with ``'admin' is undefined``. ``add_base_view``
assigns ``view.templates`` for exactly this.

**Deliberately behind the ordinary gate**, which means the first two steps it
describes are already finished by the time anyone can read it: ``@expose``
wraps this route in ``login_required``, and ``AdminAuth.authenticate``
redirects to the forced password change and then to enrolment before any
panel URL renders. Putting this page in ``_PRE_LOGIN_PAGES`` so a newcomer
could read it first would open an unauthenticated page describing the
panel's screens, and would widen a set whose membership
``admin/backend.py::_may_open_pre_login_page`` gates one page at a time. The
copy names the situation instead: steps 1 and 2 say what the panel has
already forced, and go on to the parts it does not — the recovery codes, and
a second device.
"""

from sqladmin import BaseView, expose
from starlette.requests import Request
from starlette.responses import Response


class GettingStartedView(BaseView):
    name = "Getting started"
    icon = "fa-solid fa-list-check"

    @expose("/getting-started", identity="getting-started", methods=["GET"])
    async def getting_started(self, request: Request) -> Response:
        # `identity` is given explicitly above because sqladmin falls back to
        # the *method* name for a BaseView's route name
        # (`expose`, sqladmin/application.py), which is how the dry-run page
        # ended up reachable as `admin:view-try_scenario`. Naming it here
        # keeps `admin:view-getting-started` a property of this line rather
        # than of what the method below happens to be called.
        return await self.templates.TemplateResponse(
            request, "brand/getting_started.html", {"title": "Getting started"}
        )
