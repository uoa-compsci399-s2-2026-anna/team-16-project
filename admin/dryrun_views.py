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

``CompareView`` (``/admin/factor-sets/{factor_set_id}/compare``, contract
§8.2) lives in this same module because it is built the same way: it never
calculates anything itself, only asks ``runtime.calc_client`` twice per
scenario - once at the published set's ``version_label``, once at the
target draft's - and renders whatever came back. Same v1.1 request shape as
``_request_body`` above, same two exception types, same reason for both.
See ``CompareView``'s own docstring for what is different about it: several
scenarios instead of one, and the question of what "the change" means when
there are two separate calls instead of one current-versus-alternative
request.
"""

from pathlib import Path

from sqladmin import BaseView, expose
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import Response
from starlette.templating import Jinja2Templates

from admin.auth import SESSION_KEY
from admin.calc_client import CalculateRefused, CalculateUnavailable
from admin.comparison_models import ComparisonScenario
from admin.factor_models import FactorSet, FactorSetStatus
from admin.runtime import get_runtime
from admin.taxonomy_models import Destination, FoodCategory, FoodItem, Sector
from admin import i18n as admin_i18n

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Contract O-8. This environment is not sqladmin's, so it does not inherit
# `_()` from it - see admin/i18n.py::install.
admin_i18n.install(templates.env)


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
    #: v1.54. The vocabulary step 2.5 offers, so that the staff releasing that
    #: step can test it here rather than only on the public calculator. Every
    #: item carries its parent category's code, because the two travel together
    #: in the request and an item filed under a different category is refused —
    #: the form uses it to hide items that do not belong to the chosen
    #: category. Empty in every deployment today, which is what keeps this form
    #: sending exactly the request body it sent before.
    food_items = db.execute(
        select(FoodItem, FoodCategory.code)
        .join(FoodCategory, FoodItem.food_category_id == FoodCategory.id)
        .where(FoodItem.active.is_(True))
        .order_by(FoodItem.sort_order, FoodItem.code)
    ).all()
    return {
        "factor_sets": factor_sets,
        "sectors": sectors,
        "food_categories": food_categories,
        "food_items": [
            {"code": item.code, "name": item.name, "food_category": food_code}
            for item, food_code in food_items
        ],
        "destinations": destinations,
    }


def _request_body(form) -> dict:
    """One entry, one current-scenario line, in the ``entries``-array shape.

    This is **not** the shape in this tree's own ``docs/interfaces.md``
    §6.2, which is v0.10 and flat (``sector``/``food_category``/``current``
    at the top level). It follows v1.1, still on the unmerged
    ``docs/contract-v1.0`` branch (PR #10): a teammate found that a flat,
    one-POST-per-entry front end sharing one session token had each POST's
    token upsert overwrite the previous entry's row, so a five-entry
    calculation persisted one row while the client summed the rest in
    JavaScript client-side. ``entries`` closes that by putting every entry
    in one request, computed and persisted server-side as one row. The
    project owner has confirmed v1.1 is the direction the tree's own copy
    will be reconciled to; build against it now rather than the copy that
    predates the fix.

    One entry, one line is still deliberate (Task 3's brief): this view
    exists so a single formula can be tuned against a single input, not to
    build arbitrary multi-entry requests — that is the comparison view's
    job. ``HttpCalculateClient.dry_run`` fills in ``dry_run`` itself from
    the ``factor_set_version`` keyword, so it is left out here.
    """
    gwp_horizon_raw = form.get("gwp_horizon") or "100"
    try:
        gwp_horizon = int(gwp_horizon_raw)
    except ValueError:
        # A malformed form post, not a reason to 500 - fall back to the
        # contract's own default rather than let this view join
        # CalculateRefused/CalculateUnavailable as a third, undesigned
        # failure mode.
        gwp_horizon = 100
    #: v1.54, and **the key is still omitted rather than sent as null when no
    #: food was chosen.** It was omitted originally because `api/schemas.py`
    #: sets `extra="forbid"` on the entry model and that model did not know
    #: `food_item` yet, so sending the key was a 422 for every dry run on the
    #: panel. **v1.58 landed the field and a chosen food now reaches the API**,
    #: so that reason is spent — but the omission is kept, because absent and
    #: `null` mean the same thing to §6.2 and a form that sends nothing when
    #: nothing was chosen sends byte-for-byte the request it has always sent.
    #: The panel is the one caller staff use to tune a formula; a request that
    #: differs from the pre-v1.58 one in a way nobody chose is a difference
    #: they would have to rule out first.
    entry = {
        "sector": form.get("sector"),
        "food_category": form.get("food_category") or None,
    }
    food_item = form.get("food_item") or None
    if food_item is not None:
        entry["food_item"] = food_item
    return {
        "token": None,
        "gwp_horizon": gwp_horizon,
        "entries": [
            {
                **entry,
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
                actor=request.session.get(SESSION_KEY, "unknown"),
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


def _scenario_request_body(scenario: ComparisonScenario) -> dict:
    """One contract v1.1 ``entries`` request built from a saved
    ``ComparisonScenario``, the same shape ``_request_body`` above builds by
    hand from a submitted form.

    Every line the scenario carries becomes one ``current`` line within a
    single entry - a scenario is a saved *request*, not a saved single line
    the way ``/admin/try``'s form is (see ``admin.comparison_models
    .ComparisonScenario``'s own docstring on why it carries no
    current/alternative split of its own).
    """
    return {
        "token": None,
        "gwp_horizon": scenario.gwp_horizon,
        "entries": [
            {
                "sector": scenario.sector.code,
                "food_category": (
                    scenario.food_category.code if scenario.food_category else None
                ),
                "current": [
                    {"destination": line.destination.code, "qty_kg": str(line.qty_kg)}
                    for line in scenario.lines
                ],
                "alternative": None,
            }
        ],
    }


def _metrics_of(result: dict | None) -> dict:
    """The ``totals.current.metrics`` map out of one ``/calculate``
    response, or ``{}`` for any shape that does not carry one - including
    ``None``, which is what a refused call leaves this function looking at,
    and a scalar or explicit ``null`` at any level, which B's endpoint is
    not deployed to rule out yet. Checked with ``isinstance`` rather than
    ``or {}`` throughout: ``or {}`` only catches a *falsy* wrong shape
    (``None``, ``""``, ``0``) and lets a truthy one (a string, a list) pass
    through to a caller that assumes a dict.
    """
    if not isinstance(result, dict):
        return {}
    totals = result.get("totals")
    if not isinstance(totals, dict):
        return {}
    current = totals.get("current")
    if not isinstance(current, dict):
        return {}
    metrics = current.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def _run_call(calc_client, body: dict, actor: str, factor_set_version: str) -> tuple:
    """Run one dry run; turn a refusal into data for that call's own row.

    ``CalculateRefused`` is caught here because it is a per-row condition -
    the engine looked at *this* factor set's formulas against *this*
    scenario and objected, which says nothing about the other call for the
    same scenario or about any other scenario.

    ``CalculateUnavailable`` is deliberately **not** caught here - see this
    module's own docstring and ``CompareView``'s below. An absent service
    is page-level, not per-row: it is left to propagate out of the whole
    scenario loop in ``CompareView.compare``, so one outage reads as one
    message rather than as the same "refused" line repeated for every
    scenario the loop never got to before the connection failed.
    """
    try:
        result = calc_client.dry_run(
            body, actor=actor, factor_set_version=factor_set_version
        )
        return result, None
    except CalculateRefused as exc:
        return None, {"code": exc.code, "message": exc.message, "details": exc.details}


def _metric_rows(published_result: dict | None, draft_result: dict | None) -> list[dict]:
    """Both totals, side by side, per metric present in either response.

    No subtraction happens here or in the template that renders this -
    see ``CompareView``'s own docstring for why. A metric absent from one
    side (typically because that side's call was refused, so
    ``published_result``/``draft_result`` is ``None``) renders as ``None``
    on that side rather than as a zero this function invented.
    """
    published_metrics = _metrics_of(published_result)
    draft_metrics = _metrics_of(draft_result)
    codes = sorted(set(published_metrics) | set(draft_metrics))
    rows = []
    for code in codes:
        published = published_metrics.get(code)
        draft = draft_metrics.get(code)
        # A per-metric entry that is not itself a dict (B's endpoint is not
        # deployed, so its exact shape is unconfirmed) reads as "could not
        # be computed" for that side, the same as an absent one - not a
        # TypeError from indexing a string or a list.
        published = published if isinstance(published, dict) else None
        draft = draft if isinstance(draft, dict) else None
        rows.append({
            "code": code,
            "published_total": published.get("total") if published else None,
            "published_unit": published.get("unit") if published else None,
            "draft_total": draft.get("total") if draft else None,
            "draft_unit": draft.get("unit") if draft else None,
        })
    return rows


class CompareView(BaseView):
    """``/admin/factor-sets/{factor_set_id}/compare`` - contract §8.2's
    "last gate before publishing": every active ``ComparisonScenario`` run
    against both the published factor set and the one at ``factor_set_id``,
    published value and draft value shown side by side.

    **Does not compute a difference.** Decision 6 puts every impact number
    server-side, in exactly one place - the engine, behind ``POST
    /api/v1/calculate``. That endpoint returns ``net_benefit`` for a
    current-versus-alternative comparison *within one call*; it has no
    concept of a difference between two separate calls made at two
    different ``factor_set_version``s, which is what this page would need.
    Subtracting the two response strings here would put a number in front
    of staff that no server-side calculation ever produced - exactly what
    Decision 6 forbids. So the table shows both values, plainly labelled,
    and the page says in words that no difference is shown. Whether B's
    endpoint should grow a two-version diff is a contract question for B
    and the project owner (raised in this task's own report), not
    something to settle with a ``-`` in this template.

    **One scenario's refusal does not hide the rest.** ``_run_call`` above
    catches ``CalculateRefused`` per call and turns it into that row's own
    error; the loop below keeps going. A draft with one bad formula is
    exactly when a staff member needs to see every *other* scenario still
    compute cleanly before deciding whether to publish anyway.

    **An unreachable service is a page-level message, not a per-row one.**
    ``CalculateUnavailable`` is left to propagate out of ``_run_call`` and
    out of the scenario loop below, caught once here - the same distinction
    ``DryRunView.try_scenario`` draws between "the engine looked at this
    and objected" and "nothing looked at it at all", extended to a page
    that makes several calls instead of one.

    **No published factor set is not an error.** Checked before any
    scenario runs, and rendered as its own outcome rather than an empty
    table or a comparison against a missing baseline - contract §9's
    NO_PUBLISHED_FACTOR_SET (503) is the API's own version of the same
    designed response to a fresh deployment.

    Not in the top-level menu (``is_visible`` below) - the route needs a
    ``factor_set_id`` a bare menu entry cannot supply. Reached instead from
    ``FactorSetAdmin``'s own ``compare`` row action (admin/factor_views.py).
    """

    name = "Compare with published"
    icon = "fa-solid fa-code-compare"

    def is_visible(self, request: Request) -> bool:
        return False

    @expose("/factor-sets/{factor_set_id:int}/compare", methods=["GET"])
    async def compare(self, request: Request) -> Response:
        runtime = get_runtime(request)
        factor_set_id = request.path_params["factor_set_id"]

        with runtime.session_factory() as db:
            target = db.get(FactorSet, factor_set_id)
            if target is None:
                return templates.TemplateResponse(
                    request,
                    "brand/compare.html",
                    {
                        "outcome": "missing_target",
                        "factor_set_id": factor_set_id,
                        "target": None,
                        "published": None,
                        "rows": [],
                    },
                    status_code=404,
                )

            published = db.execute(
                select(FactorSet).where(FactorSet.status == FactorSetStatus.published)
            ).scalars().first()

            if published is None:
                return templates.TemplateResponse(
                    request,
                    "brand/compare.html",
                    {
                        "outcome": "no_published",
                        "target": target,
                        "published": None,
                        "rows": [],
                    },
                )

            scenarios = db.execute(
                select(ComparisonScenario)
                .where(ComparisonScenario.active.is_(True))
                # `.id` breaks ties: every scenario fixture defaults
                # `sort_order` to 0, and MySQL gives no ordering guarantee
                # among rows equal on the only ORDER BY key - display order
                # would otherwise be whatever the query plan happened to
                # return, not the order staff set up.
                .order_by(ComparisonScenario.sort_order, ComparisonScenario.id)
            ).scalars().all()

            # Built while the session is still open: each scenario's
            # sector/food_category/line/destination relationships are
            # touched here, not after the `with` block closes it.
            bodies = [
                (scenario, _scenario_request_body(scenario)) for scenario in scenarios
            ]

        actor = request.session.get(SESSION_KEY, "unknown")
        rows = []
        try:
            for scenario, body in bodies:
                published_result, published_error = _run_call(
                    runtime.calc_client, body, actor, published.version_label
                )
                draft_result, draft_error = _run_call(
                    runtime.calc_client, body, actor, target.version_label
                )
                rows.append({
                    "scenario": scenario,
                    "published_error": published_error,
                    "draft_error": draft_error,
                    "metrics": _metric_rows(published_result, draft_result),
                })
        except CalculateUnavailable as exc:
            return templates.TemplateResponse(
                request,
                "brand/compare.html",
                {
                    "outcome": "unavailable",
                    "message": str(exc),
                    "target": target,
                    "published": published,
                    "rows": [],
                },
            )

        return templates.TemplateResponse(
            request,
            "brand/compare.html",
            {
                "outcome": "ok",
                "target": target,
                "published": published,
                "rows": rows,
            },
        )
