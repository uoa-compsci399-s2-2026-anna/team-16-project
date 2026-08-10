"""Every editable field on every admin form explains itself.

Product Decision 2 is that this system is fully configurable: categories,
formulas, constants and factors live in the database and staff edit them
through this panel, with no code change and no redeploy. A panel nobody can
operate makes that decision false. The team hands over at the end of
semester and there is nobody left to ask, so the help text *is* the handover
for these screens.

**This file is the finish line and the regression guard.** The work it
guards has no natural end - at field 60 of 81 nobody knows how many remain -
and, worse, no natural floor: a column added in six months arrives
unexplained, nothing fails, and the panel drifts back to what it was. This
test fails until somebody explains it.

What counts as an editable field is taken from sqladmin's own resolution,
not from a hand-maintained list: ``ModelView._form_prop_names`` (what
``get_form_columns`` produced from ``form_columns``/``form_excluded_columns``,
or every mapped attribute when neither is set), minus exactly what
``ModelConverterBase._prepare_kwargs`` drops before a field is ever built -
primary keys and raw foreign-key columns, unless ``form_include_pk``. That
mirroring is deliberate: a view that widens its form by *deleting* its
``form_columns`` line, rather than by adding a name to it, still gets caught.

Where a description comes from, in sqladmin's own order: ``form_args[name]
["description"]`` if the view sets one, otherwise the mapped attribute's own
``doc``, which ``_prepare_kwargs`` passes as the WTForms field description
(``kwargs.setdefault("description", prop.doc)``). Either satisfies this
test. ``templates/sqladmin/_macros.html`` renders whichever arrives, under
the input, as ``<small class="text-muted">``.

**Not covered here, and not coverable here.** Three screens carry fields
this test cannot see, because they are not sqladmin forms:

* ``IpBlockAdmin``'s manual block form is a custom template
  (``brand/block_ip.html``) behind an ``@expose`` route - ``can_create``,
  ``can_edit`` and ``can_delete`` are all False on that view, so sqladmin
  scaffolds no form for it at all. Its three fields carry their help in the
  template, pinned by
  ``test_blocklist_view.test_the_block_form_warns_that_a_blank_duration_wipes_an_expiry``.
* ``DryRunView``/``CompareView`` are ``BaseView``s, not ``ModelView``s. The
  dry-run form's own six controls are pinned by
  ``test_dryrun_view.test_every_choice_on_the_form_explains_itself``.
* ``StaffAdmin`` likewise scaffolds no form (``can_create``/``can_edit`` are
  both False); every mutation there is a confirmed ``@action``.

A field that leaves a sqladmin form for one of those templates therefore
leaves this test's reach with it. That is the known hole; nothing here can
close it.
"""

import pytest
from sqladmin import ModelView
from sqlalchemy.orm import ColumnProperty, RelationshipProperty

# Imported for the side effect of defining their ModelView subclasses, so
# `_concrete_model_views` below sees the whole set. create_app() imports all
# of these too, but it imports them *inside* the function - a subclass this
# module never imported would be invisible to __subclasses__ if the app were
# ever built some other way.
import admin.accounts_view  # noqa: F401
import admin.blocklist_views  # noqa: F401
import admin.comparison_views  # noqa: F401
import admin.factor_views  # noqa: F401
import admin.modelviews  # noqa: F401
import admin.taxonomy_views  # noqa: F401

#: ``(identity, field name)`` pairs that genuinely need no explanation, with
#: the reason, because "it is currently unexplained" is never one.
#:
#: Empty, and that is the finding rather than an oversight. The two
#: categories the brief allowed for - surrogate ``id`` columns and
#: system-maintained timestamps - are already absent from every form on this
#: panel before this test looks: ``id`` is a primary key, which sqladmin's
#: own converter drops, and every timestamp a machine writes
#: (``factor_set.published_at``, ``audit_log.at``, ``ip_block.created_at``,
#: ``staff.last_login_at``) is off its view's ``form_columns`` deliberately.
#: ``factor_set.effective_from`` is the one date left on a form and a staff
#: member types it, so it needs a description like anything else.
#:
#: Adding an entry here is a claim that a reader who has never seen this
#: system needs nothing said about the field. Say why, beside the entry.
EXEMPT_FIELDS: set[tuple[str, str]] = set()


def _concrete_model_views() -> dict[str, type[ModelView]]:
    """Every ModelView subclass that is bound to a model, by class name.

    ``ModelViewMeta`` returns early for a subclass declared without a
    ``model=`` keyword and never sets ``model``/``is_model`` on it, so the
    two intermediate bases on this project - ``AuditedModelView`` and
    ``_TaxonomyAdmin`` - fall out here rather than needing to be named.
    """
    found: dict[str, type[ModelView]] = {}
    stack = list(ModelView.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        if getattr(cls, "is_model", False) and getattr(cls, "model", None) is not None:
            found[cls.__name__] = cls
    return found


def _editable_fields(view: ModelView) -> list[str]:
    """The names sqladmin will actually render an input for on this view.

    Mirrors ``ModelConverterBase._prepare_kwargs``/``_prepare_column``
    (sqladmin/forms.py): anything that is neither a column nor a
    relationship never becomes a field, and a column that is a primary key
    or carries a foreign key is dropped unless ``form_include_pk`` is set -
    which is why ``factor_set_id`` is not on this list while the
    ``factor_set`` relationship beside it is.
    """
    fields = []
    for name in view._form_prop_names:
        prop = view._mapper.attrs.get(name)
        if isinstance(prop, RelationshipProperty):
            fields.append(name)
            continue
        if not isinstance(prop, ColumnProperty):
            continue
        column = prop.columns[0]
        if (column.primary_key or column.foreign_keys) and not view.form_include_pk:
            continue
        fields.append(name)
    return fields


def _description(view: ModelView, name: str) -> str | None:
    """What ``_macros.html`` would render under this field, or None.

    sqladmin's own order: an explicit ``form_args`` description wins,
    otherwise the mapped attribute's ``doc``.
    """
    explicit = view.form_args.get(name, {}).get("description")
    if explicit:
        return explicit
    prop = view._mapper.attrs.get(name)
    return getattr(prop, "doc", None)


def _has_form(view: ModelView) -> bool:
    """Whether sqladmin ever scaffolds a form for this view at all.

    ``AuditLogAdmin``, ``StaffAdmin`` and ``IpBlockAdmin`` all refuse both
    create and edit, so no create or edit route exists to render fields on.
    Requiring descriptions from them would demand help text for inputs no
    one can reach - and, worse, would be satisfied by writing it, which
    hides the fact that the fields are unreachable.
    """
    return bool(view.can_create or view.can_edit)


@pytest.fixture()
def registered_views(admin_app) -> list[ModelView]:
    """The ModelView *instances* the running panel renders from.

    Instances, not classes: ``_form_prop_names`` and ``_mapper`` are built
    in ``ModelView.__init__``, and ``AuditedModelView.__init__`` needs the
    ``session_maker`` class attribute ``Admin.add_model_view`` assigns - so
    the objects the app already constructed are both the cheapest and the
    most faithful thing to inspect. Reached through ``_admin_ref``, the
    back-reference sqladmin sets on every registered view class.
    """
    from admin.modelviews import AuditLogAdmin

    return [v for v in AuditLogAdmin._admin_ref.views if isinstance(v, ModelView)]


def test_every_model_view_is_registered(registered_views):
    """A view class nobody added to create_app() is a screen nobody sees.

    This is the other half of the guard below: coverage over the registered
    views alone would pass for a new screen that was never wired up, and
    "the test passes" would be true for the wrong reason.
    """
    registered = {type(view).__name__ for view in registered_views}
    defined = set(_concrete_model_views())
    assert defined - registered == set(), (
        "ModelView subclasses defined but never registered in admin/app.py"
    )


def test_the_walker_finds_fields_on_every_screen_that_has_a_form(registered_views):
    """Guards the coverage test against passing for the wrong reason.

    ``test_every_editable_field_has_a_description`` asserts that a list is
    empty, and the cheapest way for that to be true is for the walk to have
    found nothing at all - a rename inside sqladmin's ``_form_prop_names``,
    an ``isinstance`` check that stops matching after an upgrade, an
    exception swallowed somewhere. Every one of those turns the guard into a
    test that passes on an unexplained panel, which is precisely the state
    this file exists to prevent from recurring.

    Deliberately not a fixed total: hard-coding "81" would fail the day
    somebody adds a column, which is the day the coverage test above is
    supposed to be the thing that fails, with a message that says what to do.
    """
    form_views = [v for v in registered_views if _has_form(v)]
    assert form_views, "no ModelView on this panel scaffolds a form at all"

    barren = [v.identity for v in form_views if not _editable_fields(v)]
    assert not barren, (
        "these screens offer a create or edit form but the walk found no "
        f"fields on them, so nothing about them was checked: {barren}"
    )


def test_every_editable_field_has_a_description(registered_views):
    """Contract §8: no editable field on this panel goes unexplained.

    Collects every miss and reports them together rather than stopping at
    the first. The count is the point: this started at 81 fields with 1
    description, and a test that fails one field at a time would never have
    shown that.
    """
    missing = []
    checked = 0
    for view in registered_views:
        if not _has_form(view):
            continue
        for name in _editable_fields(view):
            if (view.identity, name) in EXEMPT_FIELDS:
                continue
            checked += 1
            if not _description(view, name):
                missing.append(f"{view.identity}.{name}")

    assert not missing, (
        f"{len(missing)} of {checked} editable fields have no description. "
        "Each one is a field a staff member has to guess at, on a panel "
        "whose whole purpose is that the numbers can be changed without a "
        "developer. Add form_args[name]['description'] on the view.\n  "
        + "\n  ".join(sorted(missing))
    )


def test_a_description_is_not_the_label_again(registered_views):
    """Help text that restates the field name teaches staff to stop reading.

    Deliberately narrow - it catches only the degenerate case, a description
    that is the label with an article in front of it ("The sector" under a
    field called Sector). Nothing automatic can judge the rest; this stops
    the cheapest way of turning the test above green without doing the work.
    """
    lazy = []
    for view in registered_views:
        if not _has_form(view):
            continue
        for name in _editable_fields(view):
            description = _description(view, name)
            if not description:
                continue
            words = description.lower().rstrip(".").replace("_", " ").split()
            if words and words[0] in {"the", "a", "an"}:
                words = words[1:]
            if " ".join(words) == name.replace("_", " ").lower():
                lazy.append(f"{view.identity}.{name}: {description!r}")

    assert not lazy, (
        "These descriptions only restate the field name:\n  " + "\n  ".join(lazy)
    )
