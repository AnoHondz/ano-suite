"""
predictor.py — All prediction logic.

Weighted 1X2 model (H2H 25% | Form 30% | Home/Away 20% | Injuries 15% | xG 10%)
Poisson distribution for correct scores and goal totals.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

from league_config import CURRENT_SEASON


# ── Form extraction ────────────────────────────────────────────────────────────

def _fixture_season_start_years(fixture: dict) -> set[int]:
    seasons: set[int] = set()
    season_value = (fixture.get("league") or {}).get("season")
    if isinstance(season_value, int):
        seasons.add(season_value)
        return seasons
    if isinstance(season_value, str):
        raw = season_value.strip()
        if raw:
            match = re.match(r"^(20\d{2})(?:[/-](?:20)?(\d{2}))?$", raw)
            if match:
                seasons.add(int(match.group(1)))
                return seasons
            years = re.findall(r"20\d{2}", raw)
            if years:
                seasons.add(int(years[0]))
    return seasons


def _fixture_season_start_year_from_date(fixture: dict) -> int | None:
    date_raw = str((fixture.get("fixture") or {}).get("date") or "")[:10]
    if not date_raw:
        return None
    try:
        date = datetime.fromisoformat(date_raw)
    except ValueError:
        return None
    return date.year if date.month >= 7 else date.year - 1


def _is_completed_fixture(fixture: dict) -> bool:
    status = (fixture.get("fixture") or {}).get("status") or {}
    short = str(status.get("short") or "")
    return short in {"FT", "AET", "PEN"}


def filter_recent_completed_fixtures(
    fixtures: list,
    current_season: int = CURRENT_SEASON,
    seasons_back: int = 2,
) -> list[dict]:
    # Keep the current season plus the requested number of prior season
    # start-years. With current_season=2025 and seasons_back=2 that means
    # 2025, 2024, and 2023, which correctly spans the active season and the
    # two previous completed soccer seasons around spring boundary dates.
    seasons_back = max(0, int(seasons_back))
    valid_seasons = {current_season - i for i in range(seasons_back + 1)}
    filtered = []
    for fixture in fixtures or []:
        if not _is_completed_fixture(fixture):
            continue
        seasons = _fixture_season_start_years(fixture)
        if seasons:
            if seasons.isdisjoint(valid_seasons):
                continue
        else:
            season_start = _fixture_season_start_year_from_date(fixture)
            if season_start not in valid_seasons:
                continue
        filtered.append(fixture)
    filtered.sort(key=lambda f: str((f.get("fixture") or {}).get("date") or ""), reverse=True)
    return filtered


def extract_form(fixtures: list, team_id: int) -> list[dict]:
    """Return per-match summary dicts for team_id from a fixture list."""
    def _parse_numeric_stat(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("%"):
            text = text[:-1].strip()
        try:
            return float(text)
        except (TypeError, ValueError):
            return None

    def _fixture_team_stat(fixture: dict, stat_keys: list[str]) -> float | None:
        stats_rows = fixture.get("stats") or []
        wanted = {key.lower() for key in stat_keys}
        for row in stats_rows:
            team_row = row.get("team") or {}
            if str(team_row.get("id")) != str(team_id):
                continue
            for stat in row.get("statistics") or []:
                stat_type = str(stat.get("type") or "").strip().lower()
                if stat_type in wanted:
                    return _parse_numeric_stat(stat.get("value"))
        return None

    form = []
    for f in filter_recent_completed_fixtures(fixtures):
        h_id = f["teams"]["home"]["id"]
        a_id = f["teams"]["away"]["id"]
        hg = f["goals"]["home"]
        ag = f["goals"]["away"]

        if hg is None or ag is None:
            continue

        is_home = h_id == team_id
        gf = hg if is_home else ag
        ga = ag if is_home else hg
        opp_name = f["teams"]["away"]["name"] if is_home else f["teams"]["home"]["name"]
        opp_logo = f["teams"]["away"]["logo"] if is_home else f["teams"]["home"]["logo"]

        if gf > ga:
            result = "W"
        elif gf < ga:
            result = "L"
        else:
            result = "D"

        shots = _fixture_team_stat(f, ["Total Shots", "Shots"])
        shots_on_target = _fixture_team_stat(f, ["Shots on Goal", "Shots on Target"])
        possession = _fixture_team_stat(f, ["Ball Possession", "Possession"])
        corners = _fixture_team_stat(f, ["Corner Kicks", "Corners"])

        form.append({
            "result": result,
            "gf": gf,
            "ga": ga,
            "goals_for": gf,
            "goals_against": ga,
            "shots": shots,
            "shots_on_target": shots_on_target,
            "possession": possession,
            "corners": corners,
            "opponent": opp_name,
            "opponent_id": a_id if is_home else h_id,
            "opponent_logo": opp_logo,
            "home": is_home,
            "is_home": is_home,
            "kickoff": f["fixture"]["date"],
            "fixture_id": f["fixture"].get("id"),
            "season": (f.get("league") or {}).get("season"),
            "date": f["fixture"]["date"][:10],
            "league": f["league"]["name"],
            "score": f"{gf}-{ga}",
            "cs": ga == 0,
        })
    return form


# ── Stat helpers ───────────────────────────────────────────────────────────────

def form_pts(form: list[dict]) -> float:
    """Points-per-game ratio (0–1)."""
    if not form:
        return 0.5
    pts = sum(3 if r["result"] == "W" else 1 if r["result"] == "D" else 0 for r in form)
    return pts / (len(form) * 3)


def avg_goals(form: list[dict], scored: bool = True) -> float:
    if not form:
        return 1.2
    goals = [r["gf"] if scored else r["ga"] for r in form]
    return float(sum(goals) / len(goals))


def home_away_split(form: list[dict]) -> dict:
    home = [r for r in form if r["home"]]
    away = [r for r in form if not r["home"]]

    def summary(rows):
        if not rows:
            return {"p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0}
        w = sum(1 for r in rows if r["result"] == "W")
        d = sum(1 for r in rows if r["result"] == "D")
        l = sum(1 for r in rows if r["result"] == "L")
        return {
            "p": len(rows), "w": w, "d": d, "l": l,
            "gf": round(avg_goals(rows, True), 2),
            "ga": round(avg_goals(rows, False), 2),
        }

    return {"home": summary(home), "away": summary(away)}


# ── H2H record ─────────────────────────────────────────────────────────────────

def h2h_record(fixtures: list, id_a: int, id_b: int) -> dict:
    a_wins = draws = b_wins = 0
    for f in filter_recent_completed_fixtures(fixtures):
        h_id = f["teams"]["home"]["id"]
        hg = f["goals"]["home"] or 0
        ag = f["goals"]["away"] or 0
        ga = hg if h_id == id_a else ag
        gb = ag if h_id == id_a else hg
        if ga > gb:
            a_wins += 1
        elif ga < gb:
            b_wins += 1
        else:
            draws += 1

    total = a_wins + draws + b_wins or 1
    return {
        "a_wins": a_wins, "draws": draws, "b_wins": b_wins, "total": total,
        "a_pct": a_wins / total, "d_pct": draws / total, "b_pct": b_wins / total,
    }


# ── Poisson model ──────────────────────────────────────────────────────────────

def _poisson_pmf(goals: int, lam: float) -> float:
    if goals < 0:
        return 0.0
    if lam <= 0:
        return 1.0 if goals == 0 else 0.0
    return math.exp(goals * math.log(lam) - lam - math.lgamma(goals + 1))


def _poisson_cdf(goals: int, lam: float) -> float:
    if goals < 0:
        return 0.0
    return float(sum(_poisson_pmf(i, lam) for i in range(goals + 1)))


def score_matrix(lam_a: float, lam_b: float, max_g: int = 6) -> dict:
    return {
        (i, j): float(_poisson_pmf(i, lam_a) * _poisson_pmf(j, lam_b))
        for i in range(max_g + 1)
        for j in range(max_g + 1)
    }


def over_prob(lam: float, threshold: float) -> float:
    return float(1 - _poisson_cdf(int(threshold), lam))


# ── Top scorer candidates ──────────────────────────────────────────────────────

def top_scorer_candidates(squad: list, injuries: list, team_lambda: float) -> list[dict]:
    """
    Build a ranked list of first-goalscorer candidates from the squad.
    Without detailed per-player API calls we rank by position heuristic.
    """
    injured_ids = {i["player"]["id"] for i in injuries}
    attackers = [p for p in squad if p.get("position") in ("Attacker", "Midfielder")]
    candidates = []
    base_prob = team_lambda / max(len(attackers), 1)
    for i, p in enumerate(attackers[:8]):
        penalty = 0.6 if p["id"] in injured_ids else 1.0
        pos_boost = 1.4 if p.get("position") == "Attacker" else 1.0
        prob = round(min(base_prob * pos_boost * penalty * 100, 35), 1)
        candidates.append({"name": p["name"], "photo": p.get("photo", ""), "prob": prob,
                           "injured": p["id"] in injured_ids})
    candidates.sort(key=lambda x: x["prob"], reverse=True)
    return candidates[:3]


# ── Fixtures page: standings-based quick prediction ───────────────────────────

def quick_predict_from_standings(home_id: int, away_id: int, standings: list) -> dict:
    """
    Fast 1X2 prediction using only league standings — no extra API calls.
    Weights: form 40% | lambda share 40% | home advantage 20%
    """
    home_s = next((s for s in standings if s["team"]["id"] == home_id), None)
    away_s = next((s for s in standings if s["team"]["id"] == away_id), None)

    if not home_s or not away_s:
        return {
            "home_pct": 40.0, "draw_pct": 25.0, "away_pct": 35.0,
            "confidence": "Low", "winner_label": "Home Win",
            "lam_home": 1.2, "lam_away": 1.0,
        }

    def _form_ratio(form_str: str) -> float:
        pts = sum(3 if c == "W" else 1 if c == "D" else 0 for c in (form_str or "")[-5:])
        return pts / 15.0

    played_h = home_s["all"]["played"] or 1
    played_a = away_s["all"]["played"] or 1
    gf_h = home_s["all"]["goals"]["for"] / played_h
    ga_h = home_s["all"]["goals"]["against"] / played_h
    gf_a = away_s["all"]["goals"]["for"] / played_a
    ga_a = away_s["all"]["goals"]["against"] / played_a

    lam_h = max(0.3, (gf_h + ga_a) / 2)
    lam_a = max(0.3, (gf_a + ga_h) / 2)
    lam_tot = lam_h + lam_a or 1

    fs_h = _form_ratio(home_s.get("form", ""))
    fs_a = _form_ratio(away_s.get("form", ""))
    f_tot = fs_h + fs_a or 1

    raw_h = (fs_h / f_tot) * 0.40 + 0.55 * 0.20 + (lam_h / lam_tot) * 0.40
    raw_a = (fs_a / f_tot) * 0.40 + 0.45 * 0.20 + (lam_a / lam_tot) * 0.40
    raw_d = max(0.10, 1.0 - raw_h - raw_a)
    total = raw_h + raw_d + raw_a

    p_h = raw_h / total
    p_d = raw_d / total
    p_a = raw_a / total

    best = max(p_h, p_d, p_a)
    confidence = "High" if best > 0.55 else "Medium" if best > 0.42 else "Low"
    winner_label = "Home Win" if p_h == best else ("Away Win" if p_a == best else "Draw")

    return {
        "home_pct": round(p_h * 100, 1),
        "draw_pct": round(p_d * 100, 1),
        "away_pct": round(p_a * 100, 1),
        "confidence": confidence,
        "winner_label": winner_label,
        "lam_home": round(lam_h, 2),
        "lam_away": round(lam_a, 2),
    }


# ── World Cup 2026 national-team predictor ─────────────────────────────────────
# Attack = avg goals scored / game at international level
# Defense = avg goals conceded / game  (lower = better defence)

WC_TEAMS: dict[str, dict[str, float]] = {
    "Argentina":     {"attack": 1.90, "defense": 0.85},
    "France":        {"attack": 1.85, "defense": 0.88},
    "Spain":         {"attack": 1.78, "defense": 0.90},
    "England":       {"attack": 1.72, "defense": 0.92},
    "Brazil":        {"attack": 1.82, "defense": 0.93},
    "Germany":       {"attack": 1.68, "defense": 1.00},
    "Portugal":      {"attack": 1.75, "defense": 1.02},
    "Netherlands":   {"attack": 1.65, "defense": 1.00},
    "Belgium":       {"attack": 1.60, "defense": 1.05},
    "Croatia":       {"attack": 1.55, "defense": 0.98},
    "Italy":         {"attack": 1.50, "defense": 0.95},
    "Morocco":       {"attack": 1.42, "defense": 0.92},
    "Japan":         {"attack": 1.48, "defense": 1.05},
    "USA":           {"attack": 1.40, "defense": 1.18},
    "Mexico":        {"attack": 1.38, "defense": 1.22},
    "Colombia":      {"attack": 1.52, "defense": 1.10},
    "Uruguay":       {"attack": 1.55, "defense": 1.02},
    "South Korea":   {"attack": 1.42, "defense": 1.15},
    "Senegal":       {"attack": 1.38, "defense": 1.08},
    "Canada":        {"attack": 1.40, "defense": 1.20},
    "Australia":     {"attack": 1.30, "defense": 1.25},
    "Ecuador":       {"attack": 1.35, "defense": 1.18},
    "Switzerland":   {"attack": 1.48, "defense": 1.05},
    "Denmark":       {"attack": 1.50, "defense": 1.02},
    "Poland":        {"attack": 1.38, "defense": 1.18},
    "Serbia":        {"attack": 1.42, "defense": 1.15},
    "Ukraine":       {"attack": 1.40, "defense": 1.12},
    "Turkey":        {"attack": 1.40, "defense": 1.15},
    "Austria":       {"attack": 1.42, "defense": 1.12},
    "Sweden":        {"attack": 1.45, "defense": 1.08},
    "Scotland":      {"attack": 1.32, "defense": 1.20},
    "Iran":          {"attack": 1.25, "defense": 1.18},
    "Saudi Arabia":  {"attack": 1.25, "defense": 1.25},
    "Ghana":         {"attack": 1.28, "defense": 1.28},
    "Nigeria":       {"attack": 1.35, "defense": 1.22},
    "Cameroon":      {"attack": 1.28, "defense": 1.28},
    "Ivory Coast":   {"attack": 1.32, "defense": 1.22},
    "Algeria":       {"attack": 1.30, "defense": 1.18},
    "Tunisia":       {"attack": 1.22, "defense": 1.20},
    "South Africa":  {"attack": 1.18, "defense": 1.28},
    "Egypt":         {"attack": 1.25, "defense": 1.20},
    "Chile":         {"attack": 1.38, "defense": 1.18},
    "Peru":          {"attack": 1.28, "defense": 1.22},
    "Paraguay":      {"attack": 1.22, "defense": 1.28},
    "Venezuela":     {"attack": 1.20, "defense": 1.30},
    "Bolivia":       {"attack": 1.10, "defense": 1.40},
    "Costa Rica":    {"attack": 1.18, "defense": 1.25},
    "Panama":        {"attack": 1.12, "defense": 1.32},
    "Honduras":      {"attack": 1.10, "defense": 1.35},
    "Jamaica":       {"attack": 1.08, "defense": 1.38},
    "New Zealand":   {"attack": 1.10, "defense": 1.30},
    "Wales":         {"attack": 1.32, "defense": 1.15},
    "Czech Republic":{"attack": 1.38, "defense": 1.15},
    "Hungary":       {"attack": 1.30, "defense": 1.22},
    "Slovakia":      {"attack": 1.25, "defense": 1.20},
    "Slovenia":      {"attack": 1.20, "defense": 1.22},
    "Romania":       {"attack": 1.28, "defense": 1.22},
    "Greece":        {"attack": 1.22, "defense": 1.25},
}


def wc_predict(team_a: str, team_b: str) -> dict | None:
    """
    Predict a neutral-venue international match between two WC 2026 teams.
    Uses static FIFA-ranking-calibrated attack/defense strengths.
    """
    a = WC_TEAMS.get(team_a)
    b = WC_TEAMS.get(team_b)
    if not a or not b:
        return None

    lam_a = max(0.3, a["attack"] * (1.0 / b["defense"]))
    lam_b = max(0.3, b["attack"] * (1.0 / a["defense"]))

    matrix = score_matrix(lam_a, lam_b)
    top3 = sorted(matrix.items(), key=lambda x: x[1], reverse=True)[:3]

    p_a = sum(v for (i, j), v in matrix.items() if i > j)
    p_d = sum(v for (i, j), v in matrix.items() if i == j)
    p_b = sum(v for (i, j), v in matrix.items() if i < j)
    total = p_a + p_d + p_b or 1

    p_a, p_d, p_b = p_a / total, p_d / total, p_b / total
    best = max(p_a, p_d, p_b)
    confidence = "High" if best > 0.55 else "Medium" if best > 0.42 else "Low"
    winner = team_a if p_a == best else (team_b if p_b == best else "Draw")

    avg_total = lam_a + lam_b
    p_over25 = over_prob(avg_total, 2)
    p_btts = min(0.95, (
        sum(1 for (i, j) in matrix if i > 0 and j > 0)
        / max(len(matrix), 1)
        + sum(v for (i, j), v in matrix.items() if i > 0 and j > 0)
    ) / 2)

    return {
        "team_a": team_a,
        "team_b": team_b,
        "a_pct": round(p_a * 100, 1),
        "draw_pct": round(p_d * 100, 1),
        "b_pct": round(p_b * 100, 1),
        "confidence": confidence,
        "winner": winner,
        "top_scores": [
            {"score": f"{s[0][0]}-{s[0][1]}", "prob": round(s[1] * 100, 1)}
            for s in top3
        ],
        "over25": round(p_over25 * 100, 1),
        "under25": round((1 - p_over25) * 100, 1),
        "lam_a": round(lam_a, 2),
        "lam_b": round(lam_b, 2),
        "avg_total": round(avg_total, 2),
    }


# ── Main prediction ────────────────────────────────────────────────────────────

def predict(
    team_a_id: int,
    team_b_id: int,
    h2h: list,
    fixtures_a: list,
    fixtures_b: list,
    injuries_a: list,
    injuries_b: list,
    squad_a: list = None,
    squad_b: list = None,
) -> dict[str, Any]:

    form_a5 = extract_form(fixtures_a, team_a_id)[:5]
    form_b5 = extract_form(fixtures_b, team_b_id)[:5]

    rec = h2h_record(h2h, team_a_id, team_b_id)

    # Form score (0–1)
    fs_a = form_pts(form_a5)
    fs_b = form_pts(form_b5)
    f_tot = fs_a + fs_b or 1

    # Goals per game (used for Poisson lambda)
    avg_gf_a = avg_goals(form_a5, scored=True)
    avg_ga_b = avg_goals(form_b5, scored=False)
    avg_gf_b = avg_goals(form_b5, scored=True)
    avg_ga_a = avg_goals(form_a5, scored=False)

    # Attack-strength weighted lambda
    lam_a = max(0.3, (avg_gf_a + avg_ga_b) / 2)
    lam_b = max(0.3, (avg_gf_b + avg_ga_a) / 2)
    avg_total = lam_a + lam_b

    # xG proxy from lambda
    xg_tot = lam_a + lam_b or 1
    xg_a = lam_a / xg_tot
    xg_b = lam_b / xg_tot

    # Injury penalty
    inj_pen_a = min(0.12, len(injuries_a) * 0.025)
    inj_pen_b = min(0.12, len(injuries_b) * 0.025)

    # Home advantage factor
    home_a = 0.55
    home_b = 1 - home_a

    # Weighted raw scores
    raw_a = (
        rec["a_pct"] * 0.25
        + (fs_a / f_tot) * 0.30
        + home_a * 0.20
        + (1 - inj_pen_a) * 0.15
        + xg_a * 0.10
    )
    raw_b = (
        rec["b_pct"] * 0.25
        + (fs_b / f_tot) * 0.30
        + home_b * 0.20
        + (1 - inj_pen_b) * 0.15
        + xg_b * 0.10
    )
    raw_d = max(0.08, 1 - raw_a - raw_b)
    total = raw_a + raw_d + raw_b
    p_a, p_d, p_b = raw_a / total, raw_d / total, raw_b / total

    # Poisson correct scores
    matrix = score_matrix(lam_a, lam_b)
    top3 = sorted(matrix.items(), key=lambda x: x[1], reverse=True)[:3]

    # BTTS
    a_scored_rate = sum(1 for r in form_a5 if r["gf"] > 0) / max(len(form_a5), 1)
    b_scored_rate = sum(1 for r in form_b5 if r["gf"] > 0) / max(len(form_b5), 1)
    a_conc_rate = sum(1 for r in form_a5 if r["ga"] > 0) / max(len(form_a5), 1)
    b_conc_rate = sum(1 for r in form_b5 if r["ga"] > 0) / max(len(form_b5), 1)
    btts = (a_scored_rate + b_conc_rate + b_scored_rate + a_conc_rate) / 4

    # Over/Under
    p_over25 = over_prob(avg_total, 2)
    p_over15 = over_prob(avg_total, 1)

    # Corners (avg from form data — exact requires fixture stats endpoint)
    avg_corners = 10.2
    p_over95_corners = round(
        sum(1 for r in (form_a5 + form_b5) if r["gf"] + r["ga"] >= 2) / max(len(form_a5 + form_b5), 1) * 60 + 40, 1
    )

    # Confidence
    best = max(p_a, p_d, p_b)
    confidence = "High" if best > 0.55 else "Medium" if best > 0.42 else "Low"
    winner_label = "Team A" if p_a == best else ("Draw" if p_d == best else "Team B")

    # First goalscorer candidates
    fgs_a = top_scorer_candidates(squad_a or [], injuries_a, lam_a)
    fgs_b = top_scorer_candidates(squad_b or [], injuries_b, lam_b)

    # Home/Away season splits
    split_a = home_away_split(form_a5)
    split_b = home_away_split(form_b5)

    return {
        # 1X2
        "win_prob": {
            "a": round(p_a * 100, 1),
            "draw": round(p_d * 100, 1),
            "b": round(p_b * 100, 1),
        },
        "recommended_winner": winner_label,
        "confidence": confidence,

        # BTTS
        "btts_yes": round(btts * 100, 1),
        "btts_no": round((1 - btts) * 100, 1),

        # Over/Under
        "over25": round(p_over25 * 100, 1),
        "over15": round(p_over15 * 100, 1),
        "avg_total_goals": round(avg_total, 2),

        # Correct scores
        "top_scores": [
            {"score": f"{s[0][0]}-{s[0][1]}", "prob": round(s[1] * 100, 1)}
            for s in top3
        ],

        # Corners
        "corners_avg": avg_corners,
        "over95_corners": p_over95_corners,

        # Lambdas
        "lambda_a": round(lam_a, 2),
        "lambda_b": round(lam_b, 2),

        # Supporting data
        "form_a": form_a5,
        "form_b": form_b5,
        "h2h_record": rec,
        "injuries_a": injuries_a,
        "injuries_b": injuries_b,
        "fgs_a": fgs_a,
        "fgs_b": fgs_b,
        "split_a": split_a,
        "split_b": split_b,

        # Derived display
        "avg_gf_a": round(avg_gf_a, 2),
        "avg_ga_a": round(avg_ga_a, 2),
        "avg_gf_b": round(avg_gf_b, 2),
        "avg_ga_b": round(avg_ga_b, 2),
    }
