"""``/admin/try`` — contract §8.2. Enter a scenario, see what it computes.

This view calculates nothing. It builds a contract v1.1 request body from
whatever staff typed into the form, hands it to ``runtime.calc_client`` (see
admin/calc_client.py), and renders whatever came back — verbatim, as
strings. The client is what guarantees ``X-Dry-Run: true`` is sent on every
request, so this module never touches httpx directly and never persists a
submission.

``CalculateRefused`` and ``CalculateUnavailable`` are caught separately and
given their own message on the result page. B's endpoint is not deployed for
most of this project's life, and the two failure modes must never be shown
to staff as if they were the same thing: one says the engine looked at the
formula and objected, the other says nothing looked at it at all.
"""

from pathlib import Path

from sqladmin import BaseView, expose
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import Response
from starlette.templating import Jinja2Templates

from admin.calc_client import CalculateRefused, CalculateUnavailable
from admin.factor_models import FactorSet, FactorSetStatus
from admin.runtime import get_runtime
from admin.taxonomy_models import Destination, FoodCategory, Sector

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _form_context(db) -> dict:
    """Everything the GET form's selects are built from.

    Factor sets: drafts and published only — an archived set is not one a
    staff member tuning a formula is trying next, and offering it invites a
    confusing "why doesn't this match" report. Sectors, food categories and
    destinations: active rows only, ordered the way the rest of the panel
    orders them.
    """
    factor_sets = db.execute(
        select(FactorSet)
        .where(FactorSet.status.in_([FactorSetStatus.draft, FactorSetStatus.published]))
        .order_by(FactorSet.version_label)
    ).scalars().all()
    sectors = db.execute(
        select(Sector).where(Sector.active.is_(True)).order_by(Sector.sort_order)
    ).scalars().all()
    food_categories = db.execute(
        select(FoodCategory).where(FoodCategory.active.is_(True))
        .order_by(FoodCategory.sort_order)
    ).scalars().all()
    destinations = db.execute(
        select(Destination).where(Destination.active.is_(True))
        .order_by(Destination.sort_order)
    ).scalars().all()
    return {
        "factor_sets": factor_sets,
        "sectors": sectors,
        "food_categories": food_categories,
        "destinations": destinations,
    }


def _request_body(form) -> dict:
    """The contract v1.1 body for one entry, one current-scenario line.

    One entry, one line is deliberate (Task 3's brief): this view exists so
    a single formula can be tuned against a single input, not to build
    arbitrary multi-entry requests — that is the comparison view's job.
    ``HttpCalculateClient.dry_run`` fills in ``dry_run`` itself from the
    ``factor_set_version`` keyword, so it is left out here.
    """
    gwp_horizon_raw = form.get("gwp_horizon") or "100"
    return {
        "token": None,
        "gwp_horizon": int(gwp_horizon_raw),
        "entries": [
            {
                "sector": form.get("sector"),
                "food_category": form.get("food_category") or None,
                "current": [
                    {
                        "destination": form.get("destination"),
                        "qty_kg": form.get("qty_kg"),
                    }
                ],
                "alternative": None,
            }
        ],
    }


class DryRunView(BaseView):
    name = "Try a scenario"
    icon = "fa-solid fa-flask"

    @expose("/try", methods=["GET", "POST"])
    async def try_scenario(self, request: Request) -> Response:
        runtime = get_runtime(request)

        if request.method == "GET":
            with runtime.session_factory() as db:
                context = _form_context(db)
            return templates.TemplateResponse(request, "brand/dry_run.html", context)

        form = await request.form()
        body = _request_body(form)
        factor_set_version = form.get("factor_set") or None

        try:
            result = runtime.calc_client.dry_run(
                body,
                cookies=dict(request.cookies),
                factor_set_version=factor_set_version,
            )
        except CalculateRefused as exc:
            return templates.TemplateResponse(
                request,
                "brand/dry_run_result.html",
                {
                    "outcome": "refused",
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                    "result": None,
                },
            )
        except CalculateUnavailable as exc:
            return templates.TemplateResponse(
                request,
                "brand/dry_run_result.html",
                {
                    "outcome": "unavailable",
                    "message": str(exc),
                    "code": None,
                    "details": None,
                    "result": None,
                },
            )

        return templates.TemplateResponse(
            request,
            "brand/dry_run_result.html",
            {
                "outcome": "ok",
                "result": result,
                "code": None,
                "message": None,
                "details": None,
            },
        )
