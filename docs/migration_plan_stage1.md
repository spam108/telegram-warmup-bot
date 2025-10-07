# Stage 1 Migration Plan: Preparing PostgreSQL Environment

## Current State Summary
- **Database**: Single SQLite file configured via `DATABASE_URL=sqlite:///data/CDXBOT0310.db` in `docker-compose.yml`, with WAL and other pragmas applied during runtime (`db.py`).
- **Python Dependencies**: `requirements.txt` currently lists `aiosqlite` but no PostgreSQL client libraries.
- **Docker Setup**: Only the `bot` service exists. Volumes map host directories for sessions, logs, schedule, and `data` which currently stores the SQLite database.

## Goals for Stage 1
Prepare the infrastructure to run PostgreSQL alongside the existing bot without disrupting current SQLite-based operation. This includes container orchestration, configuration, dependency management, and readiness for dual-write logic in later stages.

## Action Items

### 1. Docker Compose Enhancements
- Add a `postgres` service using the official `postgres:15` image with the recommended configuration (environment variables, tuning flags, mounted init script, data volume, and port exposure).
- Create named volume `postgres_data` for persistent storage. Retain the existing `data` bind mount for SQLite until decommissioning in later stages.
- Update the `bot` service to depend on `postgres` and expose a new environment variable `POSTGRES_URL` (or reuse `DATABASE_URL` once dual-write is complete). Include connection retries or wait-for logic in later stages.

### 2. Dependency Preparation
- Extend `requirements.txt` with async PostgreSQL libraries: `asyncpg` (primary driver) and optionally `psycopg[binary]` if migration tooling or sync utilities require it.
- Ensure Docker image or virtual environment builds install system packages needed for compiling drivers (if not using binary wheels). Document any necessary additions to `Dockerfile` such as `libpq`.

### 3. Configuration & Secrets
- Define `.env` entries for `POSTGRES_PASSWORD`, `POSTGRES_USER`, `POSTGRES_DB`, and new connection URIs. Provide fallback defaults and document expected values in `README.md` / `DEPLOYMENT.md`.
- Prepare a connection string format compatible with asyncpg, e.g. `postgresql+asyncpg://bot_user:${POSTGRES_PASSWORD}@postgres:5432/telegram_bot`.

### 4. Initialization Scripts
- Draft `init.sql` aligned with the target schema (`telegram_sessions`, `account_settings`). Ensure the script resides at the project root so Docker can mount it into the postgres container.
- Include comments about future schema evolution (additional tables for warmup data once migrated).

### 5. Backward Compatibility & Testing Strategy
- Keep current SQLite functionality untouched while introducing PostgreSQL infrastructure.
- Plan for integration tests that can run against both databases: consider feature flags or environment variable switches.
- Document migration toggles and fallback procedure for rolling back to SQLite during early deployment.

## Acceptance Criteria for Stage 1 Completion
- `docker-compose up` starts both `bot` and `postgres` services successfully; PostgreSQL initializes using `init.sql` and stores data in the named volume.
- Python dependencies install without build errors in Docker build context.
- Documentation clearly explains new environment variables and setup steps.
- Existing bot continues to run using SQLite until Stage 2 introduces dual-write logic.
