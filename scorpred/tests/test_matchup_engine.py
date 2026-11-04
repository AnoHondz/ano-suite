from services import matchup_engine as me


def nba_game(game_id, date, defense, opponent, pg_assists):
    """A box score where `opponent`'s starting lead guard gets pg_assists against `defense`."""
    players = [{"id": "pg", "name": "Lead Guard", "position": "G", "starter": True, "assists": pg_assists, "points": 20, "rebounds": 4, "threes": 2},
               {"id": "sg", "name": "Off Guard", "position": "G", "starter": True, "assists": 2, "points": 15, "rebounds": 3, "threes": 3},
               {"id": "c", "name": "Big", "position": "C", "starter": True, "assists": 1, "points": 12, "rebounds": 11, "threes": 0}]
    return {"id": game_id, "date": date, "teams": {defense: {"opponent": opponent, "players": []},
                                                  opponent: {"opponent": defense, "players": players}}}


def test_lead_guard_is_the_starting_guard_with_most_assists():
    roles = me.nba_groups(nba_game("1", "2026-10-30", "a", "b", 9)["teams"]["b"]["players"])
    assert roles == {"pg": "PG", "sg": "G", "c": "C"}


def test_soccer_positions_find_wingers_and_strikers():
    assert [me.soccer_group(p) for p in ("LM", "AM-R", "CF-L", "AM", "CM-L", "RB", "CD", "G", "SUB")] == \
        ["W", "W", "ST", "AM", "CM", "FB", "CB", None, None]


def test_leaky_defense_gets_a_shrunk_factor_a_rank_and_a_note():
    games = {"jazz": [nba_game(f"j{i}", f"2026-10-{10 + i}", "jazz", "x", 11) for i in range(8)],
             "celtics": [nba_game(f"c{i}", f"2026-10-{10 + i}", "celtics", "y", 5) for i in range(8)],
             "knicks": [nba_game(f"k{i}", f"2026-10-{10 + i}", "knicks", "z", 8) for i in range(8)]}
    profiles = me.Profiles("nba", games, {"jazz": "Jazz", "celtics": "Celtics", "knicks": "Knicks"})
    assert profiles.average("jazz", "PG", "assists") == 11
    assert profiles.league_average("PG", "assists") == 8
    assert round(profiles.factor("jazz", "PG", "assists"), 4) == round(1 + me.SHRINK * (11 / 8 - 1), 4)   # shrunk toward 1
    assert profiles.rank("jazz", "PG", "assists") == (1, 3)
    assert profiles.note("jazz", "PG", "assists") == "Jazz allow 11.0 ast a game to point guards, the 1st-most of 3 teams (+38% vs average)"
    assert profiles.note("knicks", "PG", "assists") is None        # average defense: no claim
    assert profiles.factor("nobody", "PG", "assists") == 1.0


def test_only_top_or_bottom_quarter_defenses_get_a_note_and_overpriced_edges_drop():
    games = {f"t{k}": [nba_game(f"{k}-{i}", f"2026-10-{10 + i}", f"t{k}", f"o{k}", 4 + k) for i in range(6)] for k in range(12)}
    profiles = me.Profiles("nba", games, {f"t{k}": f"Team{k}" for k in range(12)})
    assert profiles.note("t11", "PG", "assists").startswith("Team11 allow 15.0 ast")   # most allowed
    assert profiles.note("t0", "PG", "assists").startswith("Team0 allow 4.0 ast")      # fewest allowed
    assert profiles.note("t5", "PG", "assists") is None                                # middle of the league
    from services.tiered_parlays import top_edges
    leg = {"player": "A", "price": 1.04, "adjusted": 0.94, "edge": 0.94 - 1 / 1.04}
    assert top_edges([leg]) == []                                                      # -2500 favourite: no value


def test_a_story_needs_the_attack_strength_to_meet_the_defensive_weakness():
    def game(gid, date, team, opp, rebounds):
        players = [{"id": f"{team}-c", "name": "Big", "position": "C", "starter": True, "rebounds": rebounds, "points": 10, "assists": 1, "threes": 0}]
        return {"id": gid, "date": date, "teams": {team: {"opponent": opp, "players": players}, opp: {"opponent": team, "players": [
            {"id": f"{opp}-c", "name": "Other", "position": "C", "starter": True, "rebounds": 8, "points": 10, "assists": 1, "threes": 0}]}}}
    # 76ers' centers grab 14 a game; Cavs give up 14 to centers (others grab 8 and allow 8)
    games = {"sixers": [game(f"s{i}", f"2026-10-{10 + i}", "sixers", f"x{i}", 14) for i in range(8)],
             "cavs": [game(f"c{i}", f"2026-10-{10 + i}", f"y{i}", "cavs", 14) for i in range(8)],
             **{f"t{k}": [game(f"t{k}{i}", f"2026-10-{10 + i}", f"t{k}", f"z{k}{i}", 8) for i in range(8)] for k in range(6)}}
    p = me.Profiles("nba", games, {"sixers": "76ers", "cavs": "Cavaliers"})
    told = me.story(p, "sixers", p, "cavs", "C", "rebounds")
    assert told and told["side"] == "over" and told["score"] > me.STORY_MIN
    assert told["lines"][0].startswith("Cavaliers struggle to stop opposing centers: they give up 14.0 reb a game")
    assert told["lines"][1].startswith("76ers' centers are among the most productive here: 14.0 reb a game, the 1st-most")
    assert me.story(p, "t0", p, "t1", "C", "rebounds") is None   # an average attack against an average defence: no story


def test_matchup_scales_the_players_games_before_counting_hits():
    values = [6, 7, 7, 8, 6, 9, 5, 7]
    assert me.adjusted_hits(values, 7.5, "over", 1.0) == 2
    assert me.adjusted_hits(values, 7.5, "over", 1.2) == 5          # the 7s become 8.4 and clear; the 6s (7.2) still miss
    assert me.player_group("nba", [{"group": "PG"}, {"group": "PG"}, {"group": "G"}]) == "PG"
