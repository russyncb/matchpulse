"""Sportmonks Football API v3 adapter.

Turns Sportmonks JSON into MatchPulse's internal data classes. Nothing outside this file
should know what a Sportmonks response looks like.

Facts used here come from real responses (fixtures 19713866 and 19722750) and Sportmonks'
published definitions: state names, event type ids, and the fact that `periods[].minutes`
is the absolute match minute including added time.

The token is sent in the Authorization header, never in the URL, so it cannot leak into
logs or error messages.
"""
import logging
from datetime import date, datetime, timezone

import httpx

from app.config import Settings, get_settings
from app.models.enums import MatchStatus
from app.providers.base import FootballProvider
from app.providers.errors import AuthError, BadResponse, ProviderDown, RateLimited
from app.providers.gateway import ProviderGateway
from app.providers.models import CompetitionData, EventData, FixtureData, TeamData

logger = logging.getLogger("matchpulse.sportmonks")

PROVIDER = "sportmonks"
BASE_URL = "https://api.sportmonks.com/v3/football"
FIXTURE_INCLUDES = "participants;scores;state;league;periods;events"

# state.developer_name -> internal status. Verified against Sportmonks' state list, except the
# ones marked (unverified): their exact names were not visible in the docs we read.
# Anything not listed is skipped with a warning, never guessed.
STATE_MAP: dict[str, MatchStatus] = {
    "NS": MatchStatus.NOT_STARTED,
    "TBA": MatchStatus.NOT_STARTED,
    "PENDING": MatchStatus.NOT_STARTED,
    "INPLAY_1ST_HALF": MatchStatus.IN_PLAY,
    "INPLAY_2ND_HALF": MatchStatus.IN_PLAY,
    "BREAK": MatchStatus.IN_PLAY,  # break between normal time and extra time
    "HT": MatchStatus.HALF_TIME,
    "INPLAY_ET": MatchStatus.EXTRA_TIME,
    "INPLAY_ET_SECOND_HALF": MatchStatus.EXTRA_TIME,  # state 23's developer_name
    "INPLAY_ET_2ND_HALF": MatchStatus.EXTRA_TIME,
    "EXTRA_TIME_BREAK": MatchStatus.EXTRA_TIME,
    "PEN_BREAK": MatchStatus.PENALTIES,
    "INPLAY_PENALTIES": MatchStatus.PENALTIES,
    "FT": MatchStatus.FINISHED,
    "AET": MatchStatus.FINISHED,
    "FT_PEN": MatchStatus.FINISHED,
    "POSTPONED": MatchStatus.POSTPONED,
    "SUSPENDED": MatchStatus.SUSPENDED,
    "CANCELLED": MatchStatus.CANCELLED,
    "DELETED": MatchStatus.CANCELLED,
    "WO": MatchStatus.FINISHED,  # (unverified name)
    "AWARDED": MatchStatus.FINISHED,  # (unverified name)
    "ABANDONED": MatchStatus.ABANDONED,  # (unverified name)
    "DELAYED": MatchStatus.SUSPENDED,  # (unverified name)
    "INTERRUPTED": MatchStatus.SUSPENDED,  # (unverified name)
}

# type_id -> internal event type (ids confirmed in Sportmonks' event type definitions)
EVENT_TYPES: dict[int, str] = {
    10: "var", 14: "goal", 15: "own_goal", 16: "penalty_goal", 17: "missed_penalty",
    18: "substitution", 19: "yellow_card", 20: "red_card", 21: "yellow_red_card",
    22: "shootout_miss", 23: "shootout_goal",
}


# ---------------------------------------------------------------- parsing (pure functions)
def parse_competition(raw: dict) -> CompetitionData:
    return CompetitionData(PROVIDER, str(raw["id"]), raw["name"], None)


def _kickoff(raw: dict) -> datetime:
    ts = raw.get("starting_at_timestamp")
    if ts is not None:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return datetime.strptime(raw["starting_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _current_score(scores: list[dict]) -> tuple[int, int]:
    goals = {"home": 0, "away": 0}
    for s in scores:
        if s.get("description") == "CURRENT":
            goals[s["score"]["participant"]] = int(s["score"]["goals"])
    return goals["home"], goals["away"]


def _elapsed_minute(periods: list[dict]) -> int | None:
    if not periods:
        return None
    latest = max(periods, key=lambda p: p.get("sort_order", 0))
    minutes = latest.get("minutes")
    return int(minutes) if minutes is not None else None


def _parse_event(ev: dict, side_by_participant: dict) -> EventData | None:
    if ev.get("rescinded") is True:  # cancelled after the fact (e.g. a card overturned by VAR)
        return None
    minute, extra = ev.get("minute"), ev.get("extra_minute")
    payload = {
        "type_id": ev.get("type_id"), "minute": minute, "extra_minute": extra,
        "player_name": ev.get("player_name"), "related_player_name": ev.get("related_player_name"),
        "info": ev.get("info"), "addition": ev.get("addition"), "result": ev.get("result"),
    }
    return EventData(
        provider_event_id=str(ev["id"]),
        event_type=EVENT_TYPES.get(ev.get("type_id"), "other"),
        elapsed_minute=None if minute is None else int(minute) + int(extra or 0),
        team_side=side_by_participant.get(ev.get("participant_id")),
        payload={k: v for k, v in payload.items() if v is not None},
    )


def parse_fixture(raw: dict) -> FixtureData:
    """Raises ValueError/KeyError/TypeError if the fixture lacks what we need."""
    sides = {}
    for p in raw.get("participants") or []:
        location = (p.get("meta") or {}).get("location")
        if location in ("home", "away"):
            sides[location] = p
    if set(sides) != {"home", "away"}:
        raise ValueError("participants include missing (need home and away teams)")
    state = raw.get("state")
    if not state:
        raise ValueError("state include missing")
    status = STATE_MAP.get(state.get("developer_name"))
    if status is None:
        raise ValueError(f"unknown state {state.get('developer_name')!r}")
    league = raw.get("league")
    if not league:
        raise ValueError("league include missing")

    side_by_participant = {sides["home"]["id"]: "home", sides["away"]["id"]: "away"}
    raw_events = raw.get("events")  # key absent = include not applied = "not fetched"
    events = None if raw_events is None else tuple(
        e for e in (_parse_event(ev, side_by_participant) for ev in raw_events) if e
    )
    home_score, away_score = _current_score(raw.get("scores") or [])
    return FixtureData(
        provider=PROVIDER,
        provider_id=str(raw["id"]),
        competition=parse_competition(league),
        home_team=TeamData(PROVIDER, str(sides["home"]["id"]), sides["home"]["name"]),
        away_team=TeamData(PROVIDER, str(sides["away"]["id"]), sides["away"]["name"]),
        kickoff=_kickoff(raw),
        status=status,
        provider_status=state["developer_name"],
        elapsed_minute=_elapsed_minute(raw.get("periods") or []),
        home_score=home_score,
        away_score=away_score,
        events=events,
    )


# ---------------------------------------------------------------- the adapter
class SportmonksProvider(FootballProvider):
    name = PROVIDER

    def __init__(self, api_token: str | None, *, base_url: str = BASE_URL,
                 client: httpx.Client | None = None, timeout: float = 10.0, max_pages: int = 10):
        if not api_token:
            raise ValueError("SPORTMONKS_API_TOKEN is not set")
        self._token = api_token
        self._base_url = base_url
        self._client = client or httpx.Client(timeout=timeout)
        self._max_pages = max_pages
        self.last_rate_limit: dict | None = None  # {"remaining":..,"resets_in_seconds":..,...}

    # -- public API
    def get_competitions(self) -> list[CompetitionData]:
        return [parse_competition(x) for x in self._get_paged("/leagues", {})]

    def get_fixtures(self, competition_provider_id: str, date_from: date,
                     date_to: date) -> list[FixtureData]:
        path = f"/fixtures/between/{date_from:%Y-%m-%d}/{date_to:%Y-%m-%d}"
        items = self._get_paged(path, {"include": FIXTURE_INCLUDES})
        wanted = str(competition_provider_id)
        return self._parse_all([i for i in items if str(i.get("league_id")) == wanted])

    def get_live_fixtures(self) -> list[FixtureData]:
        # /livescores lists matches from 15 min before kickoff until 15 min after full time, so a
        # match reaches FINISHED in our database instead of freezing as "in play".
        return self._parse_all(self._get_paged("/livescores", {"include": FIXTURE_INCLUDES}))

    # -- internals
    def _parse_all(self, items: list[dict]) -> list[FixtureData]:
        fixtures, errors = [], []
        for raw in items:
            try:
                fixtures.append(parse_fixture(raw))
            except (KeyError, ValueError, TypeError) as exc:
                errors.append(f"fixture {raw.get('id')}: {exc}")
                logger.warning("skipping unusable Sportmonks fixture %s: %s", raw.get("id"), exc)
        if items and not fixtures:
            raise BadResponse(f"none of {len(items)} fixtures could be parsed; first problem: {errors[0]}")
        return fixtures

    def _get_paged(self, path: str, params: dict) -> list[dict]:
        out: list[dict] = []
        for page in range(1, self._max_pages + 1):
            body = self._request(path, {**params, "page": page})
            data = body["data"]
            out.extend([data] if isinstance(data, dict) else data)
            if not (body.get("pagination") or {}).get("has_more"):
                break
        return out

    def _request(self, path: str, params: dict) -> dict:
        try:
            resp = self._client.get(self._base_url + path, params=params,
                                    headers={"Authorization": self._token})
        except httpx.TimeoutException as exc:
            raise ProviderDown("Sportmonks request timed out") from exc
        except httpx.TransportError as exc:
            raise ProviderDown(f"Sportmonks connection problem: {type(exc).__name__}") from exc

        code = resp.status_code
        body = self._json_or_none(resp)
        message = str(body.get("message", ""))[:200] if isinstance(body, dict) else ""
        if code == 429:
            raise RateLimited("Sportmonks rate limit reached", retry_after=self._retry_after(resp, body))
        if code in (401, 403):
            raise AuthError(f"Sportmonks refused the request (HTTP {code}): check token and plan. {message}")
        if code >= 500:
            raise ProviderDown(f"Sportmonks server error (HTTP {code})")
        if code >= 400:
            if "no result" in message.lower():
                return {"data": []}
            raise BadResponse(f"Sportmonks returned HTTP {code}: {message}")
        if not isinstance(body, dict):
            raise BadResponse("Sportmonks response was not a JSON object")
        self._note_rate_limit(body)
        if "data" not in body:
            if "no result" in message.lower():  # an empty live poll is normal, not an error
                return {"data": []}
            raise BadResponse(f"Sportmonks response had no data: {message}")
        return body

    @staticmethod
    def _json_or_none(resp: httpx.Response):
        try:
            return resp.json()
        except ValueError:
            return None

    @staticmethod
    def _retry_after(resp: httpx.Response, body) -> float | None:
        header = resp.headers.get("Retry-After")
        if header and header.isdigit():
            return float(header)
        if isinstance(body, dict):
            reset = (body.get("rate_limit") or {}).get("resets_in_seconds")
            if isinstance(reset, (int, float)):
                return float(reset)
        return None

    def _note_rate_limit(self, body: dict) -> None:
        rl = body.get("rate_limit")
        if isinstance(rl, dict):
            self.last_rate_limit = rl
            remaining = rl.get("remaining")
            if isinstance(remaining, int) and remaining < 10:
                logger.warning("Sportmonks rate limit nearly used: %s left (%s)",
                               remaining, rl.get("requested_entity"))


def create_sportmonks_gateway(settings: Settings | None = None) -> ProviderGateway:
    settings = settings or get_settings()
    provider = SportmonksProvider(settings.sportmonks_api_token)
    return ProviderGateway(provider, hourly_limit=settings.sportmonks_hourly_limit)
