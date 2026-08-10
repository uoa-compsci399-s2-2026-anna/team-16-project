# Part B: database and public API

Written by B on the `database` branch and moved here during integration —
`README.md` at the repository root is the course template established by Asma
Shakil and may not be modified.

This module contains the MySQL 8 schema, the repository layer, and the public
FastAPI endpoints under `/api/v1`. It deliberately contains no production
factor seed: until a factor set is published, public taxonomy and calculation
requests return `503 NO_PUBLISHED_FACTOR_SET`.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
docker compose up -d db
alembic upgrade head
uvicorn api.app:app_from_environment --factory --reload
```

Available endpoints:

- `GET /api/v1/taxonomy`
- `POST /api/v1/calculate`
- `GET /api/v1/factors?format=json|csv`
- `GET /api/v1/stats`

The production calculation engine and staff authentication are intentionally
connected through injected adapters. Until the engine branch is merged, the
API tests use a test-only adapter; none of its mock arithmetic is imported by
production code.

## Tests

```bash
pytest
```

B's API and repository contract tests run on SQLite so they need no Docker.
Their fixtures live in `tests/db/conftest.py` as `sqlite_engine` and
`seeded_session` — renamed during integration, because the repository-wide
`tests/conftest.py` already defined `engine` and `session` against a real
MySQL scratch database with per-test rollback, and the two sets have opposite
semantics.

MySQL-specific migration, functional-index, cascade and decimal tests are
marked `db` and require a dedicated MySQL 8 database:

```bash
TEST_DATABASE_URL=mysql+pymysql://kaicalc:devpass@127.0.0.1:3307/kaicalc \
  pytest -m db
```

`tests/test_mysql_integration.py` is deliberately separate: the
`COALESCE(food_category_id, 0)` functional unique index on `factor_downstream`
it proves has no meaning on SQLite.

## Integration notes

- Alembic lives at `alembic/`, not `migrations/`. B's `migrations/` directory
  and her `alembic.ini` were deleted during integration — both chains were
  rooted at `down_revision = None`, so merging the two `versions/` directories
  produced two heads and `alembic upgrade heads` aborted on
  `Table 'staff' already exists`.
- The submissions tables still need to be filed onto the `alembic/` chain, in
  the v1.2 multi-entry shape, as `0008` with `down_revision = "0007"`.
