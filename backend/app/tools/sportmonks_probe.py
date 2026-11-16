"""Print a raw Sportmonks response so you can save real samples (the token is never printed).

Run inside the api container, redirecting output to a file:
    docker compose exec -T api python -m app.tools.sportmonks_probe leagues > leagues.json
    docker compose exec -T api python -m app.tools.sportmonks_probe livescores > livescores.json
    docker compose exec -T api python -m app.tools.sportmonks_probe inplay > inplay.json
    docker compose exec -T api python -m app.tools.sportmonks_probe fixture 19722750 > fixture.json
    docker compose exec -T api python -m app.tools.sportmonks_probe between 2026-11-08 2026-11-08 > between.json
Each run uses one API request.
"""
import json
import sys

import httpx

from app.config import get_settings
from app.providers.sportmonks import BASE_URL, FIXTURE_INCLUDES

USAGE = __doc__


def main(argv: list[str]) -> int:
    token = get_settings().sportmonks_api_token
    if not token:
        print("SPORTMONKS_API_TOKEN is not set in .env", file=sys.stderr)
        return 2
    cmd, args = (argv[0] if argv else ""), argv[1:]
    inc = {"include": FIXTURE_INCLUDES}
    if cmd == "leagues":
        path, params = "/leagues", {}
    elif cmd == "livescores":
        path, params = "/livescores", inc
    elif cmd == "inplay":
        path, params = "/livescores/inplay", inc
    elif cmd == "fixture" and len(args) == 1:
        path, params = f"/fixtures/{args[0]}", inc
    elif cmd == "between" and len(args) == 2:
        path, params = f"/fixtures/between/{args[0]}/{args[1]}", inc
    else:
        print(USAGE, file=sys.stderr)
        return 2
    resp = httpx.get(BASE_URL + path, params=params, headers={"Authorization": token}, timeout=15)
    print(f"HTTP {resp.status_code}", file=sys.stderr)
    try:
        print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
    except ValueError:
        print(resp.text)
    return 0 if resp.is_success else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
