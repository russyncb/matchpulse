# MatchPulse

Live football data monitoring platform. See the master proposal for the full design.

## Run (Docker)
    cp .env.example .env        # then change POSTGRES_PASSWORD
    docker compose up --build -d
    curl http://localhost:8000/api/v1/health
    # Swagger UI: http://SERVER_IP:8000/docs
    docker compose logs -f api

Migrations are applied automatically when the API container starts.

## Tests (need PostgreSQL; run inside the stack)
    docker compose exec api pytest
Tests use a separate database named `<POSTGRES_DB>_test`, created automatically.

## Database migrations
    docker compose exec api alembic upgrade head                      # apply
    docker compose exec api alembic revision --autogenerate -m "msg" # new migration (then review it!)
    docker compose exec api alembic downgrade -1                      # undo last
Generated migration files are written inside the container; to keep them, either
copy them out (`docker compose cp api:/code/alembic/versions ./backend/alembic/`)
or add a bind mount for ./backend/alembic/versions while developing.

## Inspect the database
    docker compose exec db psql -U matchpulse -d matchpulse -c '\dt'

## Providers (Phase 3A)
Providers are adapters that return MatchPulse's internal data classes (`app/providers/models.py`).
All calls go through `ProviderGateway`, which tracks usage per provider, enforces hourly/daily
budgets, retries transient errors and never lets a provider failure crash the app.
A `ReplayProvider` replays a scripted match for tests and demos (no API key needed).
    curl http://localhost:8000/api/v1/providers/status

## Sportmonks (Phase 3B)
Put your free-plan token in `.env` as `SPORTMONKS_API_TOKEN=...` (never commit it), then
`docker compose up -d` to apply it. The adapter is `app/providers/sportmonks.py`; the token is sent
in a header, not the URL. To capture raw responses safely (one API request each):

    docker compose exec -T api python -m app.tools.sportmonks_probe leagues > leagues.json
    docker compose exec -T api python -m app.tools.sportmonks_probe livescores > livescores.json
    docker compose exec -T api python -m app.tools.sportmonks_probe inplay > inplay.json
    docker compose exec -T api python -m app.tools.sportmonks_probe fixture 19722750 > fixture.json
