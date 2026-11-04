"""Session, request-bootstrap, and route-selection helpers."""

from __future__ import annotations

from utils.parsing import normalize_team_name


def refresh_requested(args) -> bool:
    return str(args.get("refresh", "")).strip().lower() in {"1", "true", "yes", "on"}


def set_data_refresh(api_client, args) -> bool:
    refresh = refresh_requested(args)
    try:
        api_client.set_force_refresh(refresh)
    except Exception:
        pass
    return refresh


def reset_force_refresh(api_client, response):
    try:
        api_client.set_force_refresh(False)
    except Exception:
        pass
    return response


def football_supported_leagues(api_client, league_by_id: dict) -> list[dict]:
    leagues = []
    for key, league_id in getattr(api_client, "LEAGUES", {}).items():
        config = league_by_id.get(league_id, {})
        leagues.append(
            {
                "key": key,
                "id": league_id,
                "name": config.get("name", key.replace("_", " ").title()),
                "country": config.get("country", ""),
                "flag": config.get("flag", ""),
                "difficulty": config.get("difficulty", 1.0),
                "type": config.get("type", "competition"),
            }
        )
    return leagues

def resolve_provider_team_by_name(name: str, teams: list[dict]) -> dict | None:
    target = normalize_team_name(name)
    if not target:
        return None

    provider_teams = [(entry.get("team") or entry) for entry in teams]
    provider_teams = [team for team in provider_teams if team.get("id")]

    for team in provider_teams:
        if normalize_team_name(team.get("name")) == target:
            return team

    for team in provider_teams:
        candidate = normalize_team_name(team.get("name"))
        if candidate and (target in candidate or candidate in target):
            return team

    target_tokens = set(target.split())
    best_team = None
    best_score = 0
    for team in provider_teams:
        candidate_tokens = set(normalize_team_name(team.get("name")).split())
        score = len(target_tokens & candidate_tokens)
        if score > best_score:
            best_score = score
            best_team = team

    return best_team if best_score else None


def fixture_context_from_form(form_data) -> dict | None:
    fixture_id = form_data.get("fixture_id", "").strip()
    fixture_date = form_data.get("fixture_date", "").strip()
    if not fixture_id and not fixture_date:
        return None
    return {
        "id": fixture_id,
        "date": fixture_date,
        "league_name": form_data.get("league_name", "").strip(),
        "round": form_data.get("round", "").strip(),
        "venue_name": form_data.get("venue_name", "").strip(),
        "data_source": form_data.get("data_source", "configured").strip().lower() or "configured",
        "home_name": form_data.get("team_a_name", "").strip(),
        "home_logo": form_data.get("team_a_logo", "").strip(),
        "away_name": form_data.get("team_b_name", "").strip(),
        "away_logo": form_data.get("team_b_logo", "").strip(),
    }


def selected_fixture(session_obj) -> dict:
    return session_obj.get("selected_fixture", {})


def require_teams(session_obj):
    if "team_a_id" not in session_obj:
        return None, None
    return (
        {
            "id": session_obj["team_a_id"],
            "name": session_obj["team_a_name"],
            "logo": session_obj["team_a_logo"],
        },
        {
            "id": session_obj["team_b_id"],
            "name": session_obj["team_b_name"],
            "logo": session_obj["team_b_logo"],
        },
    )


def store_selected_teams(session_obj, team_a: dict, team_b: dict, fixture_context: dict | None = None) -> None:
    session_obj["team_a_id"] = int(team_a["id"])
    session_obj["team_a_name"] = team_a.get("name", "")
    session_obj["team_a_logo"] = team_a.get("logo", "")
    session_obj["team_b_id"] = int(team_b["id"])
    session_obj["team_b_name"] = team_b.get("name", "")
    session_obj["team_b_logo"] = team_b.get("logo", "")

    if fixture_context:
        fixture_context["home_name"] = fixture_context["home_name"] or team_a.get("name", "")
        fixture_context["home_logo"] = fixture_context["home_logo"] or team_a.get("logo", "")
        fixture_context["away_name"] = fixture_context["away_name"] or team_b.get("name", "")
        fixture_context["away_logo"] = fixture_context["away_logo"] or team_b.get("logo", "")
        session_obj["selected_fixture"] = fixture_context
    else:
        session_obj.pop("selected_fixture", None)
