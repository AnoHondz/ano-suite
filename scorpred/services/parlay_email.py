"""Email the Parlay Mode picks to the morning-email recipient (the user's own address).

Reads saved slates only, so sending never spends odds credits.
"""
from __future__ import annotations

from datetime import UTC, datetime
from html import escape

import parlay_engine as pe

SPORT_ORDER = ("nba", "nfl", "soccer")


def todays_slates(store: pe.SlateStore | None = None) -> list[dict]:
    store = store or pe.SlateStore()
    today = pe.today_et()
    return [slate for slate in (store.load(sport, today) for sport in SPORT_ORDER)
            if slate and slate.get("status") == "ready" and slate.get("picks")]


def daily_parlays(app) -> dict:
    """Today's parlay for every sport that has a slate, plus one parlay across all of them.

    Builds a due slate if it isn't saved yet (about 3 odds credits per sport from Parlay Mode's share);
    an already-built slate is reused, so the later scheduled jobs spend nothing.
    """
    import parlay_routes
    today, notes, slates = pe.today_et(), [], []
    for sport in SPORT_ORDER:
        if pe.slate_window(sport, today) is None:
            continue
        try:
            with app.app_context():
                slate = pe.ensure_slate(sport, today, model_provider=parlay_routes.model_provider)
        except Exception as exc:
            notes.append(f"{sport.upper()} parlay unavailable ({exc}).")
            continue
        if slate and slate.get("status") == "ready" and slate.get("picks"):
            slates.append(slate)
        elif slate and slate.get("status") not in ("no_games", "off_day"):
            notes.append(f"{sport.upper()} parlay: {slate.get('status')}.")
    combined = None
    if len(slates) > 1:
        # One pick per game already; re-number so legs from different sports stay distinct.
        picks = [{**pick, "rank": i, "sport_label": slate["label"]}
                 for i, (slate, pick) in enumerate(((s, p) for s in slates for p in s["picks"]), 1)]
        combined = pe.build_parlay(sorted(picks, key=lambda p: -p["prob"])[:8])
    return {"slates": slates, "combined": combined, "notes": notes}


def render_section(parlays: dict | None) -> str:
    """Short email block: one line per sport's parlay, then the all-sports parlay."""
    e = lambda value: escape(str(value))
    if not parlays or not (parlays.get("slates") or parlays.get("notes")):
        return ""
    def line(label, parlay):
        return (f'<p style="margin:6px 0"><b>{e(label)} {e(parlay["american"])}</b>{tag_chip(parlay.get("tag"))} '
                f'<span style="color:#6b7b80">({parlay["decimal_odds"]:.2f}×, about {parlay["hit_prob"] * 100:.0f}% to hit)</span><br>'
                + " + ".join(e(s) for s in parlay["selections"]) + "</p>")
    rows = [line(slate["label"], slate["parlay"]) if slate.get("parlay")
            else f'<p style="margin:6px 0"><b>{e(slate["label"])}</b> no pick combination reached 2×.</p>'
            for slate in parlays["slates"]]
    if parlays.get("combined"):
        rows.append(line("All sports", parlays["combined"]))
    rows += [f'<p style="margin:4px 0;color:#6b7b80;font-size:12px">{e(note)}</p>' for note in parlays.get("notes", [])]
    return (f'<h3 style="margin:18px 0 4px">Parlays</h3>{"".join(rows)}'
            f'<p style="color:#8a9599;font-size:11px;margin:4px 0 0">Each parlay is the best-value mix of that day\'s picks paying at least 2×, '
            f'at the sportsbook price when built (DraftKings via ESPN, no odds credits). Full picks are on ScorPred\'s Parlay Mode page.</p>')


TIERS_NOTE = ('How player-parlay chances are made: each player\'s record at that exact line (last 15 games) blended with the '
              'sportsbook\'s price once its margin (vig) is removed, and with ScorPred\'s model where it covers the prop. Each slip is '
              'the best expected value in its payout range, one leg per game. Until tracked results prove our edges are real, only '
              'half of each estimated edge over the fair price is counted. "+EV" means every leg still beats its price by at least '
              '4% and its record and the projection model agree; "Best available" means no slip did today, so this is the strongest '
              'one, not a value bet. Parlay chances multiply estimates, so small errors compound: treat long slips as long shots.')
SPORT_BUILDERS = ("nba", "soccer", "nfl")


def builder(sport: str):
    from services import tiered_parlays
    return {"nba": tiered_parlays.build_nba, "soccer": tiered_parlays.build_soccer, "nfl": tiered_parlays.build_nfl}[sport]


def tag_chip(tag: str | None) -> str:
    if not tag:
        return ""
    good = tag == "+EV"
    return (f' <span style="font-size:11px;font-weight:bold;padding:1px 6px;border-radius:4px;'
            f'background:{"#e2f1e9" if good else "#fbefd6"};color:{"#1f7a52" if good else "#8a5a00"}">'
            f'{escape(tag if good else "Best available, no edge found")}</span>')


def render_tiers_section(tiers_by_sport: dict, *, heading: str = "Player parlays") -> str:
    """Email block: Low risk / Medium / Fly per sport; '' when no sport has any parlay."""
    if not any(parlay for tiers in tiers_by_sport.values() for parlay in tiers.values()):
        return ""
    body = render_tiers_blocks(tiers_by_sport)
    return (f'<h3 style="margin:18px 0 4px">{escape(heading)}</h3>{body}'
            f'<p style="color:#8a9599;font-size:11px;margin:4px 0 0">{TIERS_NOTE} DraftKings lines via ESPN, no odds credits.</p>')


def render_tiers(tiers_by_sport: dict, *, priced_at: str | None = None, edges_by_sport: dict | None = None) -> str:
    """Standalone email: Low risk / Medium / Fly player-prop parlays per sport, then the matchup edges."""
    body = (f'<!doctype html><html><head><meta charset="utf-8"></head><body style="font:14px Arial;line-height:1.55;color:#172329">'
            f'{render_edges(edges_by_sport or {})}{render_tiers_blocks(tiers_by_sport)}'
            f'<p style="color:#8a9599;font-size:11px;margin-top:16px">{TIERS_NOTE} '
            f'Prices are from when this was built; check your book first. Estimates, not guarantees. If gambling stops being fun, call 1-800-GAMBLER.</p>'
            f'</body></html>')
    from services.ano_email import brand
    count = sum(1 for tiers in tiers_by_sport.values() for parlay in tiers.values() if parlay)
    return brand(body, generated_at=priced_at or datetime.now(UTC).isoformat(), metrics=(("Parlays", count), ("Sports", len(tiers_by_sport))))


def leg_row(leg: dict) -> str:
    """One leg: the pick and its price, the game, then why in plain sentences from the numbers that picked it."""
    from services.tiered_parlays import price_sentence
    e = lambda value: escape(str(value))
    why = [w for w in leg.get("why") or [] if w]
    if leg.get("price") and leg.get("chance"):
        from services.value_policy import value_sentence
        why += [s for s in (price_sentence(leg["chance"], leg["price"], leg.get("fair")), value_sentence(leg)) if s]
    if not leg.get("why"):  # legs built before plain-language reasons: the old compact facts
        why = [" · ".join(part for part in (f'our chance {leg["chance"] * 100:.0f}% vs book {100 / leg["price"]:.0f}%',
                                            f'last 15: {leg["record"]}' if leg.get("record") else "",
                                            leg.get("edge_note") or "") if part)]
    return (f'<tr><td style="padding:7px 0;border-bottom:1px solid #e6ece9"><b>{e(leg["selection"])}</b> '
            f'{e(leg["american"])} <span style="color:#6b7b80">{e(leg.get("book") or "")} · {e(leg["matchup"])}</span>'
            f'<br><span style="color:#33424a;font-size:13px">{e(" ".join(why))}</span></td></tr>')


def render_tiers_blocks(tiers_by_sport: dict) -> str:
    """Low risk / Medium / Fly player-prop parlays per sport, each leg with the stats behind it."""
    from services.tiered_parlays import TIERS, TIER_LABELS
    from services.value_policy import is_value
    e = lambda value: escape(str(value))
    sections = []
    for sport, tiers in tiers_by_sport.items():
        blocks = []
        for tier, (low, high) in TIERS.items():
            parlay = tiers.get(tier)
            head = f'{TIER_LABELS[tier]} <span style="color:#6b7b80;font-weight:normal">({low:g}–{high:g}×)</span>'
            if not parlay:
                blocks.append(f'<p style="margin:8px 0"><b>{head}</b>: not enough priced legs in this range on today\'s board.</p>')
                continue
            games = [leg["game_id"] for leg in parlay["legs"]]
            same_game = len(set(games)) < len(games)
            rows = "".join(leg_row(leg) for leg in parlay["legs"])
            blocks.append(f'<p style="margin:12px 0 2px"><b>{head}: {e(pe.american(parlay["decimal_odds"]))} '
                          f'({parlay["decimal_odds"]:.2f}×) · about {parlay["hit_prob"] * 100:.0f}% to hit</b>{tag_chip(parlay.get("tag"))} '
                          f'<span style="color:#6b7b80">$10 returns ${parlay["payout_per_10"]:.2f}'
                          + (f' · {sum(1 for leg in parlay["legs"] if is_value(leg))} of {len(parlay["legs"])} legs clear the value bar'
                             if parlay.get("tag") and parlay["tag"] != "+EV" else "") + '</span></p>'
                          f'<table style="width:100%;border-collapse:collapse">{rows}</table>'
                          + ('<p style="margin:2px 0;color:#9a6b00;font-size:12px">Includes legs from the same game: books price '
                             'same-game parlays themselves, so the payout may differ.</p>' if same_game else ""))
        sections.append(f'<h2 style="margin:18px 0 4px">{e(sport.upper())}</h2>{"".join(blocks)}')
    return "".join(sections)


EDGES_NOTE = ("Matchup stories: one team's attack at a position (what its own players there produce) meets the other "
              "team's defence at that position (what it gives up), each ranked against its own league over the last 15 "
              "games, and the player's own record at the line agrees. Point guards are each game's starting guard with the "
              "most assists; soccer uses ESPN's listed positions.")


def render_edges(edges_by_sport: dict) -> str:
    """'Matchup stories' block: tonight's strongest scouting stories and the pick each one leads to; '' when none."""
    rows = [leg_row({**edge, "matchup": f'{sport.upper()} · {edge["matchup"]}'}).replace(
                "</b> ", f'</b>{tag_chip(edge.get("tag"))} ', 1)
            for sport, edges in (edges_by_sport or {}).items() for edge in edges or []]
    if not rows:
        return ""
    return (f'<h3 style="margin:18px 0 4px">Matchup stories</h3><table style="width:100%;border-collapse:collapse">{"".join(rows)}</table>'
            f'<p style="color:#8a9599;font-size:11px;margin:4px 0 0">{EDGES_NOTE}</p>')


def send_player_parlays(app, *, builders=None, sender=None) -> dict:
    """Midday player parlays (Low / Medium / Fly) for NBA and soccer, at most once a day, to the morning-email recipient.

    Runs at noon ET with a 3 PM retry: DraftKings often posts props after the 6 AM email. Sends nothing when
    no sport has a parlay in any tier (no games, no posted or priced props). ESPN data, no odds credits.
    """
    import json
    from services import daily_digest
    config = daily_digest.settings()
    if not config["enabled"] or not config["recipient"]:
        return {"state": "disabled"}
    day = pe.today_et().isoformat()
    receipt = daily_digest.root() / f"player-parlays-{day}.json"
    if receipt.exists():
        return {"state": "already_sent"}
    if builders is None:
        builders = {sport: builder(sport) for sport in SPORT_BUILDERS}
    tiers_by_sport, edges, generated = {}, {}, None
    for sport, build in builders.items():
        try:
            result = build(app)
        except Exception:
            continue
        if any((result.get("tiers") or {}).values()):
            tiers_by_sport[sport] = result["tiers"]
            edges[sport] = result.get("edges") or []
            generated = generated or result.get("generated_at")
    if not tiers_by_sport:
        return {"state": "nothing_to_send"}
    labels = " / ".join(sport.upper() for sport in tiers_by_sport)
    message_id = (sender or daily_digest._send)(f"ScorPred {labels} player parlays: Ladder / Easy / Medium / Fly / 5-leg — {day}",
                                                render_tiers(tiers_by_sport, priced_at=generated, edges_by_sport=edges),
                                                config["recipient"], lambda: None)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({"day": day, "message_id": message_id, "sent_at": datetime.now(UTC).isoformat()}), encoding="utf-8")
    from services.pick_tracker import record_sent
    record_sent(day, "midday", tiers_by_sport=tiers_by_sport)
    return {"state": "sent", "sports": list(tiers_by_sport)}


def render_record_picks(result: dict) -> str:
    """Record-only soccer picks at DraftKings' posted lines, for when no prices exist yet (no payouts shown)."""
    from services.tiered_parlays import record_groups
    e = lambda value: escape(str(value))
    picks = result.get("picks") or []
    line = lambda p: (f'<b>{e(p["selection"])}</b> ({e(p["team"])}) · last 10: {e(p["last10"])} · last 15: {e(p["record"])} · '
                      f'avg {e(p["average"])} · <span style="color:#6b7b80">{e(p["matchup"])}</span>')
    groups = "".join(f'<p style="margin:12px 0 2px"><b>{name} · {len(legs)} legs · about {hit * 100:.0f}% to hit by their records</b></p>'
                     f'<ul>{"".join(f"<li>{line(p)}</li>" for p in legs)}</ul>' for name, hit, legs in record_groups(picks))
    body = (f'<!doctype html><html><head><meta charset="utf-8"></head><body style="font:14px Arial;line-height:1.55;color:#172329">'
            f'<h2 style="margin:0 0 6px">Soccer player picks</h2>'
            f'<p style="padding:8px 10px;background:#fff6e5;border-radius:6px">DraftKings has posted these lines but <b>not their prices</b>, '
            f'so payouts are not shown. Check each price before betting; priced Low / Medium / Fly parlays follow once prices are posted.</p>'
            f'{groups or "<p>No strong picks from different games yet.</p>"}'
            f'<h3 style="margin:16px 0 4px">Strongest single picks</h3><ul>{"".join(f"<li>{line(p)}</li>" for p in picks[:12])}</ul>'
            f'<p style="color:#8a9599;font-size:11px">Chances are each player\'s record at that exact line over his last 15 games (domestic '
            f'league plus European games, regular starters only), not a price. Check lineups. Estimates, not guarantees. '
            f'If gambling stops being fun, call 1-800-GAMBLER.</p></body></html>')
    from services.ano_email import brand
    return brand(body, generated_at=result.get("generated_at") or datetime.now(UTC).isoformat(),
                 metrics=(("Picks", len(picks)), ("Games", result.get("games", 0))))


COMMANDS = ("player_parlays", "nba_player_parlays", "soccer_player_parlays", "nfl_player_parlays", "parlays", "morning")


def run_command(app, kind: str, *, sender=None) -> dict:
    """One emailed report on the user's typed request (via Ano), to the configured recipient only.

    player_parlays / nba_player_parlays / soccer_player_parlays: Low / Medium / Fly (soccer falls back to record-only
    picks when DraftKings has not priced its props); parlays: today's game-line parlays; morning: the full morning
    email now. A typed request may repeat the day's automatic email; no other recipient is possible."""
    from datetime import timedelta
    from services import daily_digest, tiered_parlays
    if kind not in COMMANDS:
        raise ValueError("Unknown report")
    config = daily_digest.settings()
    if not config["recipient"]:
        return {"state": "no_recipient"}
    send = sender or daily_digest._send
    to, day = config["recipient"], pe.today_et()
    if kind == "parlays":
        found = daily_parlays(app)
        if not found["slates"]:
            return {"state": "nothing_to_send", "notes": found["notes"]}
        send(f"ScorPred parlays — {day}", render(found["slates"]), to, lambda: None)
        from services.pick_tracker import record_sent
        record_sent(day, "on_request", parlays=found)
        return {"state": "sent", "sports": [s["sport"] for s in found["slates"]]}
    if kind == "morning":
        from services.pick_tracker import recap_for_email, record_sent
        api_get, props_for = daily_digest.flask_readers(app)
        recap = recap_for_email()
        report = daily_digest.collect(api_get, props_for)
        report["recap"] = recap
        report["parlays"] = daily_parlays(app)
        report["player_parlays"], report["edges"] = {}, {}
        for sport in SPORT_BUILDERS:
            try:
                result = builder(sport)(app)
                if not any((result.get("tiers") or {}).values()):
                    continue
                report["player_parlays"][sport], report["edges"][sport] = result["tiers"], result.get("edges") or []
            except Exception:
                pass
        send(f"ScorPred NBA / Soccer / NFL — {day} (on request)", daily_digest.render(report), to, lambda: None)
        record_sent(day, "on_request", report=report)
        return {"state": "sent", "picks": sum(len(v) for v in report["props"].values())}
    sports = {"player_parlays": SPORT_BUILDERS, "nba_player_parlays": ("nba",), "soccer_player_parlays": ("soccer",),
              "nfl_player_parlays": ("nfl",)}[kind]
    tiers_by_sport, edges, generated, sent = {}, {}, None, []
    from zoneinfo import ZoneInfo
    tomorrow_morning = datetime.combine(day + timedelta(days=1), datetime.min.time(), ZoneInfo("America/New_York")).replace(hour=6)
    for sport in sports:
        build = builder(sport)
        try:
            result = build(app)
            if sport == "soccer" and not result.get("games"):  # today's games are over or none: tomorrow's
                result = tiered_parlays.build_soccer(app, now=tomorrow_morning.astimezone(UTC))
        except Exception:
            continue
        if any((result.get("tiers") or {}).values()):
            tiers_by_sport[sport], generated = result["tiers"], result.get("generated_at")
            edges[sport] = result.get("edges") or []
    if tiers_by_sport:
        labels = " / ".join(s.upper() for s in tiers_by_sport)
        send(f"ScorPred {labels} player parlays: Ladder / Easy / Medium / Fly / 5-leg — {day}",
             render_tiers(tiers_by_sport, priced_at=generated, edges_by_sport=edges), to, lambda: None)
        from services.pick_tracker import record_sent
        record_sent(day, "on_request", tiers_by_sport=tiers_by_sport)
        sent += list(tiers_by_sport)
    if "soccer" in sports and "soccer" not in tiers_by_sport:
        # No priced soccer props: send record-backed picks at the posted lines, today's games or else tomorrow's.
        result = tiered_parlays.build_soccer(unpriced=True)
        if not result["picks"]:
            result = tiered_parlays.build_soccer(now=tomorrow_morning.astimezone(UTC), unpriced=True)
        if result["picks"]:
            send(f"ScorPred soccer player picks (no prices posted yet) — {day}", render_record_picks(result), to, lambda: None)
            sent.append("soccer picks")
    return {"state": "sent", "sent": sent} if sent else {"state": "nothing_to_send"}


def player_parlay(report: dict, *, min_odds: float = pe.PARLAY_MIN_ODDS, max_legs: int = pe.PARLAY_MAX_LEGS) -> dict | None:
    """Most-likely player-prop parlay from the morning email's sportsbook-priced picks, one leg per game.

    Same-game legs are skipped: books price those together as same-game parlays, usually for less.
    Legs are treated as independent, so the hit chance is the product of the model chances.
    """
    import itertools
    legs = [{"selection": f'{p["name"]} {str(p["side"]).capitalize()} {p["line"]:g} {p["label"]}', "team": p.get("team"),
             "price": p["market"]["decimal_odds"], "american": pe.american(p["market"]["decimal_odds"]),
             "book": p["market"]["bookmaker"], "prob": p["chance"], "match_id": p["match_id"], "kickoff": p["kickoff"],
             "sport": p.get("sport", "")}
            for rows in report.get("props", {}).values() for p in rows]
    best = None
    for size in range(2, min(max_legs, len(legs)) + 1):
        for combo in itertools.combinations(legs, size):
            if len({leg["match_id"] for leg in combo}) < size:
                continue
            odds = pe.combine_odds(leg["price"] for leg in combo)
            hit = pe.combine_odds(leg["prob"] for leg in combo)
            if odds >= min_odds and (best is None or (hit, -size) > (best["hit_prob"], -len(best["legs"]))):
                best = {"legs": list(combo), "decimal_odds": round(odds, 2), "american": pe.american(odds),
                        "hit_prob": round(hit, 4), "ev": round(hit * odds - 1, 4), "payout_per_10": round(10 * odds, 2)}
    if best:
        best["priced_at"] = report.get("generated_at")
    return best


def render_player_parlay(parlay: dict | None) -> str:
    e = lambda value: escape(str(value))
    if not parlay:
        body = "<p>No player-prop parlay today: the morning picks didn't cover two different games.</p>"
    else:
        rows = "".join(f'<tr><td style="padding:7px 0;border-bottom:1px solid #e6ece9"><b>{e(leg["selection"])}</b> '
                       f'<span style="color:#6b7b80">{e(leg["team"])}</span><br>{e(leg["american"])} {e(leg["book"])} · '
                       f'<span style="color:#6b7b80;font-size:12px">model {leg["prob"] * 100:.0f}% · {e(str(leg["sport"]).upper())} · '
                       f'{_kickoff(leg["kickoff"])}</span></td></tr>' for leg in parlay["legs"])
        body = (f'<h2 style="margin:0 0 6px">Player prop parlay · {len(parlay["legs"])} legs</h2>'
                f'<table style="width:100%;border-collapse:collapse">{rows}</table>'
                f'<p style="margin:10px 0 0;padding:10px 12px;background:#eef6f2;border-radius:6px"><b>{e(parlay["american"])} '
                f'({parlay["decimal_odds"]:.2f}×) · about {parlay["hit_prob"] * 100:.0f}% to hit</b><br>'
                f'<span style="color:#6b7b80;font-size:12px">$10 returns ${parlay["payout_per_10"]:.2f} if every leg hits. '
                f'Legs are from different games; each price is that book\'s own line.</span></p>')
    html = (f'<!doctype html><html><head><meta charset="utf-8"></head><body style="font:14px Arial;line-height:1.55;color:#172329">{body}'
            f'<p style="color:#8a9599;font-size:11px;margin-top:16px">Lines and prices are from this morning\'s sportsbook check and may have moved; '
            f'legs can sit at different books. Chances are model estimates, not a proven edge, and assume the legs are independent. '
            f'Bet only what you can afford to lose. If gambling stops being fun, call 1-800-GAMBLER.</p></body></html>')
    from services.ano_email import brand
    return brand(html, generated_at=(parlay or {}).get("priced_at") or datetime.now(UTC).isoformat(),
                 metrics=(("Legs", len(parlay["legs"]) if parlay else 0), ("Odds", parlay["american"] if parlay else "—")))


def _kickoff(iso: str | None) -> str:
    try:
        moment = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(pe.ET)
    except ValueError:
        return ""
    return f"{moment:%a} {moment.hour % 12 or 12}:{moment:%M %p} ET"


def render(slates: list[dict]) -> str:
    e = lambda value: escape(str(value))
    sections = []
    for slate in slates:
        legs = set((slate.get("parlay") or {}).get("legs") or [])
        rows = "".join(
            f'<tr><td style="padding:7px 0;border-bottom:1px solid #e6ece9">{"★ " if p["rank"] in legs else ""}<b>{e(p["selection"])}</b> '
            f'&nbsp;·&nbsp; {e(p["american"])} {e(p["book"])}<br>'
            f'<span style="color:#6b7b80;font-size:12px">{p["prob"] * 100:.0f}% to hit · '
            f'{"+%.1f%% EV" % (p["ev"] * 100) if p.get("tier") == "value" else "best available, no edge"} · {e(p["matchup"])} · {_kickoff(p.get("commence_time"))}</span></td></tr>'
            for p in slate["picks"])
        parlay = slate.get("parlay")
        parlay_html = (
            f'<p style="margin:10px 0 0;padding:10px 12px;background:#eef6f2;border-radius:6px"><b>Parlay of the day: {e(parlay["american"])} '
            f'({parlay["decimal_odds"]:.2f}×, about {parlay["hit_prob"] * 100:.0f}% to hit)</b>{tag_chip(parlay.get("tag"))}<br>'
            + "<br>".join(f"★ {e(s)}" for s in parlay["selections"])
            + f'<br><span style="color:#6b7b80;font-size:12px">$10 returns ${parlay["payout_per_10"]:.2f}</span></p>'
        ) if parlay else '<p style="color:#6b7b80">No combination of picks reached 2× odds.</p>'
        sections.append(f'<h2 style="margin:16px 0 6px">{e(slate.get("title") or slate.get("label"))}</h2>'
                        f'<table style="width:100%;border-collapse:collapse">{rows}</table>{parlay_html}')
    body = (f'<!doctype html><html><head><meta charset="utf-8"></head><body style="font:14px Arial;line-height:1.55;color:#172329">'
            f'{"".join(sections) or "<p>No Parlay Mode slate is ready today.</p>"}'
            f'<p style="color:#8a9599;font-size:11px;margin-top:16px">Prices as of when the picks were built; odds move, so check your book first. '
            f'Chances are estimates, not guarantees; "best available" picks fill the card but are not value at their price. Bet only what you can afford to lose. '
            f'If gambling stops being fun, call 1-800-GAMBLER.</p></body></html>')
    from services.ano_email import brand
    picks = sum(len(slate["picks"]) for slate in slates)
    return brand(body, generated_at=datetime.now(UTC).isoformat(),
                 metrics=(("Picks", picks), ("Parlays", sum(1 for slate in slates if slate.get("parlay")))))


def send(slates: list[dict] | None = None, *, sender=None) -> dict:
    """Send to the configured morning-email recipient only. Returns what was sent."""
    from services import daily_digest
    recipient = daily_digest.settings()["recipient"]
    if not recipient:
        raise ValueError("No email recipient is configured for ScorPred.")
    slates = todays_slates() if slates is None else slates
    labels = " / ".join(slate.get("label", "") for slate in slates) or "no slate"
    message_id = (sender or daily_digest._send)(f"ScorPred Parlay Mode — {labels} — {pe.today_et()}", render(slates),
                                                recipient, lambda: None)
    return {"recipient": recipient, "slates": [slate["sport"] for slate in slates], "message_id": message_id}
