# team-16-project
COMPSCI 399 project repository for Team 16 - 502 Bad Gateway

## Part B: database and public API

This branch contains the MySQL 8 schema, Alembic migration, repository layer,
and the public FastAPI endpoints under `/api/v1`. It deliberately contains no
production factor seed: until a factor set is published, public taxonomy and
calculation requests return `503 NO_PUBLISHED_FACTOR_SET`.

### Local setup

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

### Tests

```bash
pytest
```

The regular suite uses SQLite so API and repository contract tests run without
Docker. MySQL-specific migration, functional-index, cascade, and decimal tests
are explicitly marked and require a dedicated MySQL 8 database:

```bash
TEST_DATABASE_URL=mysql+pymysql://kaicalc:devpass@127.0.0.1:3307/kaicalc \
  pytest -m db
```
