"""Soccer player value in goals per match, from Fantasy Premier League season data.

Attack: expected goals plus half of expected assists per 90 (the chance is the shooter's;
the pass that made it earns half), above the position's replacement level. Defence:
expected goals conceded per 90 with the player off the pitch minus with them on it,
shrunk hard because on/off splits are noisy. Goalkeepers: goals prevented per 90
(expected minus actual conceded), also shrunk. Each is scaled by how much of a match
the player plays when fit. Not backtested: no historical availability is stored.
"""
from statistics import quantiles

GOALKEEPER, DEFENDER, MIDFIELDER, FORWARD = 1, 2, 3, 4
POSITION_NAMES = {GOALKEEPER: "GK", DEFENDER: "DEF", MIDFIELDER: "MID", FORWARD: "FWD"}
ASSIST_CREDIT = 0.5
REPLACEMENT_QUANTILE = 0.35   # a squad backup attacks like a lower-third regular at the position
MIN_MINUTES = 450
ATTACK_PRIOR_MINUTES = 450
DEFENCE_PRIOR_MINUTES = 1800
KEEPER_PRIOR_MINUTES = 1800
VALUE_CAP = 0.4               # goals per match, either side of the ball


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def attack_per_90(player):
    minutes = _float(player.get("minutes"))
    if minutes <= 0:
        return 0.0
    return 90 * (_float(player.get("expected_goals")) + ASSIST_CREDIT * _float(player.get("expected_assists"))) / minutes


def replacement_levels(players):
    levels = {}
    for position in POSITION_NAMES:
        rates = sorted(attack_per_90(p) for p in players
                       if p.get("element_type") == position and _float(p.get("minutes")) >= MIN_MINUTES)
        levels[position] = quantiles(rates, n=100)[int(REPLACEMENT_QUANTILE * 100) - 1] if len(rates) >= 10 else 0.0
    return levels


def team_concession(players, team):
    """Team expected goals conceded and minutes, from its goalkeepers (always on the pitch)."""
    keepers = [p for p in players if p.get("team") == team and p.get("element_type") == GOALKEEPER]
    return sum(_float(p.get("expected_goals_conceded")) for p in keepers), sum(_float(p.get("minutes")) for p in keepers)


def match_share(player):
    """Share of a match the player plays when picked: minutes per start, capped at a full match."""
    starts, minutes = _float(player.get("starts")), _float(player.get("minutes"))
    return min(1.0, minutes / starts / 90) if starts else min(1.0, minutes / 90 / 3)


def player_value(player, players, replacement):
    """Goals per match: attack the team loses and goals the opponent gains when the player is absent."""
    minutes = _float(player.get("minutes"))
    position = player.get("element_type")
    share = match_share(player)
    attack = (attack_per_90(player) - replacement.get(position, 0.0)) * minutes / (minutes + ATTACK_PRIOR_MINUTES)
    defence = 0.0
    if position == GOALKEEPER and minutes:
        prevented = (_float(player.get("expected_goals_conceded")) - _float(player.get("goals_conceded"))) * 90 / minutes
        defence = prevented * minutes / (minutes + KEEPER_PRIOR_MINUTES)
    elif position in {DEFENDER, MIDFIELDER} and minutes:
        team_xgc, team_minutes = team_concession(players, player.get("team"))
        off_minutes = team_minutes - minutes
        if off_minutes > 0:
            on = 90 * _float(player.get("expected_goals_conceded")) / minutes
            off = 90 * (team_xgc - _float(player.get("expected_goals_conceded"))) / off_minutes
            sample = min(minutes, off_minutes)
            defence = (off - on) * sample / (sample + DEFENCE_PRIOR_MINUTES)
    clip = lambda value: max(-VALUE_CAP, min(VALUE_CAP, value * share))
    # Below-replacement splits are noise or a coach's choice; a fit player is never worse than the backup.
    return {"attack": clip(max(attack, 0.0)), "defence": clip(max(defence, 0.0)), "minutes": minutes, "match_share": share}


def availability(player):
    """Chance of playing from the FPL status and its published chance of playing next round."""
    chance = player.get("chance_of_playing_next_round")
    if player.get("status") == "a":
        return 1.0
    if chance is not None:
        return _float(chance) / 100
    return 0.5 if player.get("status") == "d" else 0.0


def played_share(player, matches):
    """Share of the team's league minutes so far that the player was on the pitch."""
    return min(1.0, _float(player.get("minutes")) / (90 * matches)) if matches else 0.0
