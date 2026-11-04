"""Player selections at fixed model targets, requiring matchup-model agreement.

These are unpriced selections, not claims of positive sportsbook expected value.
"""
from statistics import mean
from services import prop_model

TARGETS = {"shots": 1.5, "shots_on_target": .5, "goals": .5, "assists": .5}


def attach_context(players, games, config, home_id, away_id):
    conceded = prop_model.allowed(games, config["markets"])
    for player in players:
        team = str(player["team"])
        opponent = str(away_id) if team == str(home_id) else str(home_id)
        for market in player["markets"]:
            key = market["market"]
            fields = config["markets"][key][0]
            attack = []
            for game in sorted(games, key=lambda g: g["date"]):
                rows = game["teams"].get(team, {}).get("players", [])
                if rows and all(prop_model.has_stats(row, fields) for row in rows):
                    attack.append(sum(prop_model.value(row, fields) for row in rows))
            defense = conceded[key].get(opponent, [])[-8:]
            attack = attack[-8:]
            market["matchup_baseline"] = {k: market[k] for k in ("ladder", "probability", "side")}
            market["matchup_context"] = {"team_per_game": round(mean(attack), 2) if attack else None,
                "opponent_allowed_per_game": round(mean(defense), 2) if defense else None,
                "team_games": len(attack), "opponent_games": len(defense)}


def select(players, limit=5):
    from services.prop_markets import _model_at_line
    candidates = []
    for player in players:
        for market in player["markets"]:
            key = market["market"]
            context = market.get("matchup_context", {})
            baseline = market.get("matchup_baseline")
            if key not in TARGETS or market.get("model") != "soccer-player-counts-v2": continue
            if key in {"shots", "shots_on_target"} and player.get("position") not in {"F", "M"}: continue
            if market.get("games", 0) < 8 or market.get("minutes", 0) < 60: continue
            if min(context.get("team_games", 0), context.get("opponent_games", 0)) < 3 or not baseline: continue
            line = TARGETS[key]
            trained = _model_at_line({**market, "side": "over", "chance": market["probability"]}, line)
            reference = _model_at_line({**baseline, "side": "over", "chance": baseline["probability"]}, line)
            if trained is None or reference is None: continue
            for side in (("over",) if key in {"goals", "assists"} else ("over", "under")):
                p, q = (trained, reference) if side == "over" else (1 - trained, 1 - reference)
                if min(p, q) < .60: continue
                candidates.append({**{k: v for k, v in market.items() if k != "matchup_baseline"},
                    "name": player["name"], "player_id": player.get("id"), "team": player["team"],
                    "position": player["position"], "side": side, "line": line, "chance": round(min(p, q), 4),
                    "probability": round(min(p, q), 4), "record": None, "line_source": "model_target",
                    "sportsbook_quotes": [], "selection_score": min(p, q) * min(market["games"], 15) / 15,
                    "rationale": f"{market['minutes']:.0f} expected minutes; {market['games']} comparable player games. "
                        f"Team produces {context['team_per_game']:.2f} {market['label'].lower()} per game; "
                        f"opponent allows {context['opponent_allowed_per_game']:.2f} "
                        f"across {context['opponent_games']} games. Both models support this side."})
    picks, seen = [], set()
    for candidate in sorted(candidates, key=lambda p: -p["selection_score"]):
        if candidate["player_id"] in seen: continue
        seen.add(candidate["player_id"])
        picks.append(candidate)
        if len(picks) >= limit: break
    return picks
