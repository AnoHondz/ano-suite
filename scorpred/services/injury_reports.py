"""Live injury reports: ESPN league feeds (NBA, NFL) and Fantasy Premier League (EPL).

Each feed is cached for ``FRESH_SECONDS``; when a refresh fails the last snapshot is
served for up to ``MAX_STALE_SECONDS`` and labelled degraded. NFL reports are joined
to the official weekly practice reports in the local nflverse store by the caller.
"""
import json
import re
import threading
import time
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime

import requests

from runtime_paths import cache_dir
from services.data_freshness import metadata

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/{path}/injuries"
ESPN_PATHS = {"nba": "basketball/nba", "nfl": "football/nfl"}
FPL_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
FRESH_SECONDS = 600
MAX_STALE_SECONDS = 6 * 3600
_ATHLETE_ID = re.compile(r"/id/(\d+)")
_locks = defaultdict(threading.Lock)


class InjuryFeedUnavailable(RuntimeError):
    pass


def normalize_name(name):
    """Accent-, case- and suffix-insensitive key for joining reports across providers."""
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z ]", "", text.replace("-", " "))
    return " ".join(part for part in text.split() if part not in {"jr", "sr", "ii", "iii", "iv", "v"})


def _fetch(name, url, fresh_seconds=FRESH_SECONDS, *, refresh=False):
    """JSON payload plus its observation time, with a bounded stale fallback on failure.

    ``refresh`` skips the fresh-cache shortcut so the scheduler can renew a feed before it expires.
    """
    path = cache_dir("injuries") / f"{name}.json"
    cached = None
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None
    if cached and not refresh and time.time() - cached["fetched"] < fresh_seconds:
        return cached["payload"], cached["fetched"], False
    with _locks[name]:
        try:
            response = requests.get(url, timeout=(4, 15), headers={"User-Agent": "Mozilla/5.0 ScorPred"})
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            if cached and time.time() - cached["fetched"] < MAX_STALE_SECONDS:
                return cached["payload"], cached["fetched"], True
            raise InjuryFeedUnavailable(f"{name} injury feed is unavailable") from exc
        fetched = time.time()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fetched": fetched, "payload": payload}), encoding="utf-8")
        return payload, fetched, False


def _freshness(source, fetched, stale):
    return metadata(source, datetime.fromtimestamp(fetched, UTC).isoformat(), FRESH_SECONDS, MAX_STALE_SECONDS,
                    degraded=stale)


def espn(sport, *, refresh=False):
    """Every listed player in the league: id, name, team abbreviation, position, status, detail, update time."""
    payload, fetched, stale = _fetch(sport, ESPN_URL.format(path=ESPN_PATHS[sport]), refresh=refresh)
    rows = []
    for team in payload.get("injuries") or []:
        for entry in team.get("injuries") or []:
            athlete = entry.get("athlete") or {}
            links = " ".join(link.get("href") or "" for link in athlete.get("links") or [])
            match = _ATHLETE_ID.search(links)
            details = entry.get("details") or {}
            rows.append({"espn_id": match.group(1) if match else None, "name": athlete.get("displayName") or "",
                         "team": ((athlete.get("team") or {}).get("abbreviation") or "").upper(),
                         "position": (athlete.get("position") or {}).get("abbreviation") or "",
                         "status": entry.get("status") or "", "injury": details.get("type") or "",
                         "return_date": details.get("returnDate"), "updated": entry.get("date"),
                         "comment": entry.get("shortComment") or "", "source": "ESPN"})
    return rows, _freshness("ESPN injuries", fetched, stale)


def fpl(*, refresh=False):
    """Fantasy Premier League bootstrap: every player's status, chance of playing and season data."""
    payload, fetched, stale = _fetch("fpl", FPL_URL, refresh=refresh)
    if not payload.get("elements") or not payload.get("teams"):
        raise InjuryFeedUnavailable("Fantasy Premier League returned no players")
    return payload, _freshness("Fantasy Premier League", fetched, stale)


def refresh_all():
    """Renew every league-wide feed; one outcome per feed for the pipeline monitor."""
    outcomes = []
    for name, load in (("ESPN NBA", lambda: espn("nba", refresh=True)),
                       ("ESPN NFL", lambda: espn("nfl", refresh=True)),
                       ("Fantasy Premier League", lambda: fpl(refresh=True))):
        try:
            _rows, freshness = load()
        except InjuryFeedUnavailable as exc:
            outcomes.append({"status": "failed", "observed_at": None, "error": str(exc)})
            continue
        degraded = freshness.get("state") != "fresh"
        outcomes.append({"status": "degraded" if degraded else "success", "observed_at": freshness.get("observed_at"),
                         "error": f"{name} refresh failed; serving the last snapshot" if degraded else None})
    return outcomes
