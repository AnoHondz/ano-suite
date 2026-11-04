"""Build the audit feature inventory and league measurements from saved diagnostics."""
import argparse
import csv
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from scripts.audit_scorp import DEFAULT_OUTPUT, ROOT, literal, load, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    out, inventory = args.output, []
    def add(model, sport, name, source, calculation, required, *, missing=None, denominator=None,
            evidence="Not verified", freshness="Unknown", used=False, limitation=""):
        inventory.append({"model_or_engine": model, "sport": sport, "feature": name, "source": source,
                          "calculation": calculation, "required_fields": required, "missing_fraction": missing,
                          "measured_denominator": denominator, "freshness": freshness, "historical_availability": evidence,
                          "evidence_quality": evidence, "used_in_decisions": used, "limitations": limitation})
    soccer = load(out / "soccer_model_replay.json")
    for name in soccer["features"]:
        stat = soccer["feature_coverage"][name]
        optional = name.startswith(("home_implied", "away_implied", "draw_implied", "referee_", "home_xg", "away_xg")) or name in {"market_confidence", "odds_home_away_ratio", "xg_diff"}
        formula = "Prior rolling goals/results; last 5/10 or venue-specific last 5; differences as named"
        fields = "date,home_team,away_team,goals_scored,goals_conceded,result"
        if "elo" in name:
            formula = "Sequential pre-match Elo, base 1500, K=20; home/away difference where named"
        elif "opp_avg_ppg" in name:
            formula = "Mean of five opponents' points/game snapshots before each historical meeting"
        elif "h2h" in name:
            formula = "Prior five meetings from home-team perspective, points or goal margin"
        elif "days_since" in name or "rest_diff" in name:
            formula = "Elapsed date days before target; cap 60; default 7; home-away difference where named"
        elif optional:
            formula = "Normalized market implied odds / rolling xG / expanding referee statistics as named; missing sentinels imputed using training policy"
            fields = "odds_home,odds_draw,odds_away,home_xg,away_xg,referee,card/penalty fields as applicable"
        add("soccer_production_classifier", "soccer", name, "historical_matches.csv -> train_model.build_clean_features; live scormastermind._ml_features",
            formula, fields, missing=1.0 if optional else stat["raw_missing"] / stat["rows"], denominator=stat["rows"],
            evidence="Reconstructed chronology; source receipt history unavailable", freshness="History ends 2026-04-19; live provider times vary",
            used=not optional, limitation="Schema contains this field, but training has no varying evidence" if optional else "Classifier receives up to ten matches separately from the five-match rule/UI form; live optional-input availability is not exhaustively archived")
    recovered = load(ROOT / "cache/nflverse/phase2_2/run_30fe98722d5901403574f017/dataset.json")
    if recovered["dataset_fingerprint"] != "55774852dd69eb7d3c0945795febce780dfeec8b6c7588d6fe458214304d5f86":
        raise ValueError("Historical NFL audit dataset fingerprint changed")
    rows = recovered["rows"]
    if any(r["season"] >= 2026 for r in rows):
        raise ValueError("Unexpected current-season rows; no NFL labels inspected")
    for name in recovered["feature_names"]:
        count = sum(row["features"].get(name) is None for row in rows)
        if "epa" in name or "qb" in name:
            fields = "eligible PBP epa/pass_attempt/sack/passer_player_id; model-dependent metrics require publication provenance"
        elif "success" in name:
            fields = "eligible PBP success flags with observed denominator"
        elif "rest" in name or "week" in name:
            fields = "prior schedules,gameday,gametime,completion/publication bounds"
        else:
            fields = "eligible schedules/scores and scrimmage PBP event fields, native IDs, observation timestamps"
        add("nfl_phase2_winner_schema", "nfl", name, "nfl_winner_features + Phase2.1 conservative reconstruction",
            "Home minus away context or rolling 3/5/8/season-to-date offense/defense/QB metric, as named", fields,
            missing=count / len(rows), denominator=len(rows), evidence="Class B reconstructed; 12+168 hour bound; unverified derived EPA/QB masked",
            freshness="Frozen 888-row historical snapshot", used=False, limitation="Disabled research; availability does not imply demonstrated predictive value")
    actual_inputs = load(ROOT / "docs/assets/nfl_phase2_3/inputs_2026_07_NE_CHI.json")
    for name in ("home_field", "diff_win_rate", "diff_rest_days"):
        add("nfl_frozen_logistic_v1", "nfl", name, "Immutable local schedule/PBP observations and collection receipts",
            {"home_field": "1 at home venue, 0 neutral", "diff_win_rate": "Home-away mean result in latest three eligible same-season measured-PBP games", "diff_rest_days": "Elapsed rest difference; missing if newer potentially played schedule is ineligible"}[name],
            "native game/team IDs,kickoffs,prior scores,measured event yards,source observation and receipt timestamps",
            missing=float(actual_inputs["features"][name] is None), denominator=1,
            evidence="Class A prospective inputs; Class B frozen training", freshness="Five-minute schedule / six-hour PBP receipt gate",
            used=False, limitation="Research archive only; measured prospective missingness denominator is one game; missing rest uses original training median")
    for name in ("home_field", "home_rating", "away_rating"):
        add("nfl_frozen_elo_v1", "nfl", name, "Frozen 1,139-game seed plus eligible unsealed observed results",
            "Sequential Elo 1500 base, K20, 55 home points, 0.75 annual retention", "prior scores,venue,season,game IDs,observation times",
            missing=0.0, denominator=1, evidence="Class A observed updates; Class B seed", freshness="Same paired prospective input snapshot",
            used=False, limitation="All reserved outcomes excluded from rating updates; research only")
    for sport in ("nba", "soccer", "nfl"):
        report = load(ROOT / f"data/validation/scoring_{sport}.json")
        for name in report["feature_names"]:
            add(f"{sport}_experimental_score_model", sport, name, "Recorded completed game scores; NFL legacy team-stat fields where named",
                "Last six same-season scoring/conceding means, win rate, sample SD/count, home venue; NFL uses legacy rolling eight-game fields",
                "prior dated scores,season,venue; NFL team_stats EPA/CPOE/turnovers where named", evidence="Reconstructed prior-game chronology, 24-hour lag; historical source vintages unverified",
                freshness="Artifact max age 30 days; live sources checked separately", used="Separate score panel only",
                limitation="No injury/lineup/market inputs; no winner-decision validation implied; per-feature missingness not retained in scoring reports")
    for name in literal(ROOT / "nba_train_model.py", "NBA_FEATURE_COLUMNS"):
        add("nba_classifier_schema_no_artifact", "nba", name, "Legacy NBA score history feature builder",
            "Rolling 5/10 score-based proxy, win rate, rest, Elo or head-to-head as named",
            "dated team scores,home/away IDs,prior matchups", evidence="Not verified: fitted NBA classifier absent",
            used=False, limitation="Rating/pace names do not establish possession-based data; no production winner artifact present")
    components = {"form": "Recency weighted results, margin bonus, scaled 0–10 (39%)", "offense": "Last-five scored mean plus trend (14%); NBA saturates at 115 points",
                  "defense": "Last-five conceded mean plus trend (14%)", "h2h": "Recency weighted prior five meetings (9%)",
                  "home_away": "Venue-context component (8%)", "match_context": "Rest/fatigue component (5%)",
                  "squad": "Role-weighted injury penalty (4%); empty list treated as score10", "opponent_strength": "Opponent standings strength lookup (7%); missing neutral5"}
    for sport in ("soccer", "nba"):
        for name, formula in components.items():
            add("weighted_rule_engine", sport, name, "ESPN / optional credentialed providers -> normalized form, injuries, standings",
                formula, "dated results,gf/ga or our_pts/their_pts,venue,opponent names,injury roles/status",
                evidence="Current source times vary; no immutable historical feature archive", freshness="Source/route dependent; rule outputs are not calibrated probabilities",
                used=True, limitation="Missing input rates cannot be recovered from a durable rule-feature history; unavailable inputs can become neutral/full-availability defaults")
    for name in ("home_points_for", "home_points_against", "away_points_for", "away_points_against"):
        add("nfl_production_scenarios", "nfl", name, "Same-season prior eight completed game score pairs",
            "Recent scoring/conceding pairs; cross-team scenario counts", "prior scores,kickoff,standard regular/postseason game status",
            evidence="Current observed source; no historical validation of scenario shares", freshness="24-hour score lag plus source freshness gates",
            used=True, limitation="Scenario shares are not calibrated winner probabilities; cannot produce BET")
    paths = literal(ROOT / "props_engine.py", "SOCCER_FIXTURE_STAT_PATH")
    for name in literal(ROOT / "props_engine.py", "SOCCER_MARKETS"):
        source_path = paths.get(name)
        add("soccer_player_props", "soccer", name, "API-Football player fixture/season fields; endpoint access unverified in audit environment",
            "Weighted recent/career/versus-opponent samples plus context; per-90 where configured", str(source_path or "computed/unsupported; inspect market implementation"),
            evidence="Historical availability unverified", freshness="1-hour season and 24-hour career caches", used="Props only",
            limitation={"clearances": "Mapped to tackles.blocks rather than measured clearances", "aerial_duels_won": "Mapped to total duels.won without aerial separation", "chances_created": "Identical source to key_passes", "passes_completed": "Mapped to passes.total; accuracy/completions not verified", "touches_opp_box": "Explicitly unsupported"}.get(name, "No prospective prop performance validation; season endpoint entitlement unknown"))
    for name in literal(ROOT / "props_engine.py", "NBA_MARKETS"):
        add("nba_player_props", "nba", name, "Legacy nba_client; separate ESPN overview page path also exists",
            "Weighted score/boxscore samples and context; confidence/hit rates are descriptive", "real player game logs,minutes,opponent,season averages",
            evidence="Unavailable through legacy real numeric-ID player-stat interface", freshness="NBA overview stale fallback has no upper bound",
            used="Props only", limitation="Legacy numeric-ID logs empty; no demonstrated measured prop evaluation")
    write(out, "feature_inventory.json", {"audited_at": datetime.now(UTC), "entries": inventory,
          "unknown_policy": "null missing_fraction means not measurable, not zero. Optional all-zero soccer schema fields are unsupported, not 100% observed real zeros.",
          "scope": "Every declared fitted-model schema, expanded NFL winner schema, active rule component and configured player-prop market; outputs are cataloged separately in the model validation report."})
    with (out / "feature_inventory.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(inventory[0]))
        writer.writeheader()
        writer.writerows(inventory)
    # Recover competition identity only from explicit provider league metadata.
    league_lookup, team_names, event_leagues = {}, {}, {}
    for entry in load(out / "cache_manifest.json")["files"]:
        if entry.get("sport") != "soccer":
            continue
        payload = load(ROOT / entry["file"])
        leagues = payload.get("leagues") or [l for s in payload.get("sports", []) for l in s.get("leagues", [])]
        for event in payload.get("events", []):
            if (league := event.get("league")) and league.get("id"):
                leagues.append(league)
                event_leagues[str(event["id"])] = str(league["id"])
        for league in leagues:
            if league.get("id"):
                league_lookup[str(league["id"])] = {k: league.get(k) for k in ("id", "name", "slug", "abbreviation")}
            for block in league.get("teams", []):
                team = block.get("team", block)
                if team.get("id"):
                    team_names[str(team["id"])] = team.get("displayName", team.get("name"))
    cached = load(out / "cached_events.json")
    grouped = defaultdict(list)
    for item in cached["soccer"]:
        event = item["event"]
        league_id = event_leagues.get(event["id"]) or (event["uid"].split("~l:")[-1].split("~")[0] if "~l:" in event["uid"] else "unknown")
        grouped[(league_id, str(event["season"]))].append(event)
    league_counts = []
    for (league, season), events in sorted(grouped.items()):
        teams = {e[k] for e in events for k in ("home_id", "away_id")}
        league_counts.append({"provider_league": league_lookup.get(league, {"id": league}), "season": season,
                              "games": len(events), "completed": sum(e["completed"] for e in events), "teams": len(teams),
                              "draws": sum(e["completed"] and e["home_score"] == e["away_score"] for e in events),
                              "home_wins": sum(e["completed"] and e["home_score"] > e["away_score"] for e in events),
                              "away_wins": sum(e["completed"] and e["home_score"] < e["away_score"] for e in events),
                              "mean_home_goals": sum(e["home_score"] for e in events if e["completed"]) / sum(e["completed"] for e in events),
                              "mean_away_goals": sum(e["away_score"] for e in events if e["completed"]) / sum(e["completed"] for e in events),
                              "double_round_robin_capacity_if_applicable": len(teams) * (len(teams) - 1)})
    elo = load(ROOT / "data/processed/soccer_elo_state.json")
    exact = {i: name for i, name in team_names.items() if name in elo}
    normalize = lambda value: "".join(c for c in value.casefold() if c.isalnum())
    normalized = {normalize(name) for name in elo}
    write(out, "soccer_league_coverage.json", {"league_counts": league_counts, "current_provider_team_directory": len(team_names),
          "elo_exact_name_matches": len(exact), "elo_neutral_fallback_names": {i: name for i, name in team_names.items() if name not in elo},
          "elo_normalized_only_potential_matches": {i: name for i, name in team_names.items() if name not in elo and normalize(name) in normalized},
          "limitation": "A name not present in training can represent a new/promoted team or a spelling mismatch. Only normalized-only cases establish a spelling issue; other mappings need an explicit ID crosswalk."})
    nba_reference = {r["event"]["id"]: r["event"] for r in cached["nba_training"] if r["event"]["completed"] and r["event"]["season_type"] in (2, 3)
                     and all(r["event"][s].isdigit() and 1 <= int(r["event"][s]) <= 30 for s in ("home_id", "away_id"))
                     and min(r["event"]["home_score"] or 0, r["event"]["away_score"] or 0) > 0}
    nba_live = {r["event"]["id"] for r in cached["nba"]}
    write(out, "nba_archive_gaps.json", {"reference": "Deduplicated existing monthly ESPN archive, using existing training eligibility; not an independent provider universe",
          "by_season": {season: {"reference_games": len(games), "not_in_live_response_cache": sum(g["id"] not in nba_live for g in games),
                                  "season_types": dict(Counter(g["season_type"] for g in games)),
                                  "missing_examples": [g["id"] for g in games if g["id"] not in nba_live][:10]}
                        for season in sorted({str(e["season"]) for e in nba_reference.values()})
                        if (games := [e for e in nba_reference.values() if str(e["season"]) == season])}})
    print("feature inventory", len(inventory), "rows; league/archive comparisons written", flush=True)


if __name__ == "__main__":
    main()
