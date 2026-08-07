from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migration_chain_has_one_head_and_one_initial_revision():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["20260806_01"]
    assert [revision.revision for revision in script.walk_revisions()] == ["20260806_01"]
