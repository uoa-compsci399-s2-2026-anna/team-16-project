"""unit_preset becomes New Zealand's actual containers, at a sourced density

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-15 09:12:04.331902

Contract §2.1 and §7.3. **No schema change** — every column this revision
touches already exists. It is here because `admin.seed._ensure` creates a row
only when its `code` is absent and never updates one, so a seed edit alone
reaches a fresh database and leaves every deployed one on the old numbers. The
old numbers are the ones the calculator would have quoted at a visitor.

**What was wrong with the set this replaces.** It offered a 10 L bucket, a 20 L
bucket, a 30 L crate, and 120 L and 240 L wheelie bins, at a flat 0.30 kg/L that
no source was ever given for. It missed both ends of the case the client asked
for: the 23 L kerbside food scraps bin that most of urban Auckland was issued
from March 2023, and the 660 L and 1100 L front-loader bins that are the
standard commercial sizes in New Zealand — which is to say, the container a café
or a school actually fills, and the whole reason a container input exists. It
also missed the 80 L and 140 L kerbside bins several councils issue instead of
the 120 L.

**The density is sourced and is still not measured.** 0.29 kg/L comes from the
Food Loss & Waste Protocol's *Guidance on FLW Quantification Methods*, Table
3.2, where it appears twice independently — household food waste in a small
container (WRAP 2010) and commerce-and-industry animal and vegetable wastes
(Jacobs Engineering UK 2010). It is not a New Zealand measurement; none was
found, and MfE's *Solid Waste Analysis Protocol* publishes none and warns
against volume-based estimation for exactly this reason. Every row's
`source_note` says all of that, and O-6 stays open. `admin/seed.py` carries the
reasoning in full; this file carries the numbers so a deployed database gets
them.

**Staff edits are not overwritten, and that is the only reason this is safe to
run.** These are taxonomy rows and §8.1 exposes them for direct CRUD, so a
staff member may already have corrected one by hand. The UPDATE is therefore
conditional on `source_note` still being the untouched placeholder string this
seed wrote — a row anybody has touched is left exactly as it is, and the
operator sees a smaller row count than they expected rather than losing work.
The INSERTs are conditional on the code being absent, for the same reason.

**No `audit_log` row is written, deliberately.** §2.4's rule is that every
*staff* write is audited, and `write_audit` records an actor. A migration has no
actor: it runs as part of `alembic upgrade head` in `docker/init.sh`, before and
outside any session. Recording one would mean inventing a username, and the
deployment record for this change is the revision itself.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0015'
down_revision: Union[str, Sequence[str], None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The exact `source_note` `admin/seed.py` wrote before this revision. An
#: UPDATE is applied only to a row still carrying it verbatim.
_OLD_NOTE = (
    "Placeholder conversion — the client has not yet supplied measured data. "
    "Replace before the calculator is published."
)

_DENSITY_BASIS = (
    "Bulk density 0.29 kg/L from the Food Loss & Waste Protocol, Guidance on "
    "FLW Quantification Methods, Table 3.2 (household food waste, small "
    "container — WRAP 2010; the same figure appears for commerce-and-industry "
    "animal and vegetable wastes — Jacobs Engineering UK 2010). The capacity is "
    "a real New Zealand container size; the density is not a New Zealand "
    "measurement and no published one was found, so this conversion is a "
    "sourced PLACEHOLDER and not measured data. It also assumes the container "
    "holds food waste alone and is filled level. Replace with measured data "
    "before the calculator is published — architecture.md O-6."
)

#: (code, litres, label, kg_per_unit). Transcribed from `admin.seed`, not
#: imported from it: a migration states the numbers of its own moment, and a
#: later seed edit must not retroactively change what this revision did.
_CONTAINERS = [
    ("bucket_10l_full", 10, "10 L bucket (full)", "2.9000"),
    ("bucket_20l_full", 20, "20 L bucket (full)", "5.8000"),
    ("food_scraps_bin_23l", 23, "23 L kerbside food scraps bin (full)", "6.6700"),
    ("crate_30l_full", 30, "30 L crate (full)", "8.7000"),
    ("wheelie_bin_80l", 80, "80 L wheelie bin (full)", "23.2000"),
    ("wheelie_bin_120l", 120, "120 L wheelie bin (full)", "34.8000"),
    ("wheelie_bin_140l", 140, "140 L wheelie bin (full)", "40.6000"),
    ("wheelie_bin_240l", 240, "240 L wheelie bin (full)", "69.6000"),
    ("front_loader_660l", 660, "660 L front-loader bin (full)", "191.4000"),
    ("front_loader_1100l", 1100, "1100 L front-loader bin (full)", "319.0000"),
]

#: The rows this revision found in the seed, and what they held. Used by
#: `downgrade`, which cannot recompute them from anything else.
_SUPERSEDED = [
    ("bucket_10l_full", "10 L bucket (full)", "3.0000"),
    ("bucket_20l_full", "20 L bucket (full)", "6.0000"),
    ("crate_30l_full", "30 L crate (full)", "9.0000"),
    ("wheelie_bin_120l", "120 L wheelie bin (full)", "36.0000"),
    ("wheelie_bin_240l", "240 L wheelie bin (full)", "72.0000"),
]


def _note(litres: int, kilograms: str) -> str:
    return f"{litres} L × 0.29 kg/L = {kilograms} kg. {_DENSITY_BASIS}"


def upgrade() -> None:
    bind = op.get_bind()
    update = sa.text(
        "UPDATE unit_preset SET label = :label, kg_per_unit = :kg, "
        "source_note = :note WHERE code = :code AND source_note = :old_note"
    )
    insert = sa.text(
        "INSERT INTO unit_preset "
        "(code, label, food_category_id, kg_per_unit, source_note, active) "
        "SELECT :code, :label, NULL, :kg, :note, 1 FROM (SELECT 1) AS one "
        "WHERE NOT EXISTS "
        "(SELECT 1 FROM unit_preset existing WHERE existing.code = :code)"
    )
    for code, litres, label, kilograms in _CONTAINERS:
        parameters = {
            "code": code,
            "label": label,
            "kg": kilograms,
            "note": _note(litres, kilograms),
        }
        bind.execute(update, {**parameters, "old_note": _OLD_NOTE})
        bind.execute(insert, parameters)


def downgrade() -> None:
    """Put the five superseded rows back and delete the five this added.

    A row a staff member edited after the upgrade is left alone by the same
    `source_note` test the upgrade used, in the same direction: only a row still
    carrying the note this revision wrote is reverted, and only a row still
    carrying it is deleted. Anything else is somebody's work.
    """
    bind = op.get_bind()
    added = {code for code, _, _, _ in _CONTAINERS} - {
        code for code, _, _ in _SUPERSEDED
    }
    revert = sa.text(
        "UPDATE unit_preset SET label = :label, kg_per_unit = :kg, "
        "source_note = :old_note WHERE code = :code AND source_note = :note"
    )
    remove = sa.text(
        "DELETE FROM unit_preset WHERE code = :code AND source_note = :note"
    )
    notes = {
        code: _note(litres, kilograms)
        for code, litres, _, kilograms in _CONTAINERS
    }
    for code, label, kilograms in _SUPERSEDED:
        bind.execute(revert, {
            "code": code, "label": label, "kg": kilograms,
            "note": notes[code], "old_note": _OLD_NOTE,
        })
    for code in sorted(added):
        bind.execute(remove, {"code": code, "note": notes[code]})
