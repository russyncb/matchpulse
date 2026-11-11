# MatchPulse

Live football data monitoring platform. See the master proposal for the full design.

## Run (Docker)
    cp .env.example .env
    docker compose up --build -d
    curl http://localhost:8000/api/v1/health
    # Swagger UI: http://SERVER_IP:8000/docs
    docker compose logs -f api

## Run tests locally
    cd backend
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    pytest
