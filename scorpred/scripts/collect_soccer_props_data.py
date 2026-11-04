"""Reproducible, provider-isolated soccer player-game intake from public datasets."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import UTC, datetime
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import zipfile

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RAW = ROOT / "data/raw/soccer_props_v2"
GAMES = ROOT / "data/processed/soccer_props_v2/games"
MANIFEST = ROOT / "data/validation/soccer_props_sources_v2.json"
SB = "https://raw.githubusercontent.com/statsbomb/open-data/master"
SOURCES = []


def fetch(url, name):
    path = RAW / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        for attempt in range(3):
            try:
                result = requests.get(url, timeout=(10, 90))
                result.raise_for_status()
                temp = path.with_suffix(path.suffix + ".part")
                temp.write_bytes(result.content)
                temp.replace(path)
                break
            except requests.RequestException:
                if attempt == 2: raise
                time.sleep(attempt + 1)
    return path


def provenance(path, url, **extra):
    return {"url": url, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size, **extra}


def save(game):
    if len(game["teams"]) != 2: return False
    teams = list(game["teams"])
    for tid, other in zip(teams, reversed(teams)):
        game["teams"][tid]["opponent"] = other
        if not game["teams"][tid]["players"]: return False
    target = GAMES / game["source"] / (str(game["id"]) + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(game, ensure_ascii=False), encoding="utf-8")
    return True


def espn():
    count = 0
    digests = []
    for slug in ("eng.1", "esp.1", "ger.1"):
        for path in sorted((ROOT / "cache/box_scores").glob(f"soccer-{slug}-*.json")):
            game = json.loads(path.read_text(encoding="utf-8"))
            game.update(source="espn", league=slug)
            digests.append((path.name, hashlib.sha256(path.read_bytes()).hexdigest()))
            game["id"] = f"{slug}-{game['id']}"
            count += save(game)
    SOURCES.append({"provider": "ESPN", "status": "used", "games": count,
        "fingerprint": hashlib.sha256(json.dumps(digests).encode()).hexdigest(),
        "limitations": "Existing compact caches may contain legacy zeros for originally omitted fields; new parsing preserves missing values."})
    print(f"ESPN: {count} games", flush=True)


def fpl():
    for year in range(2018, 2026):
        season = f"{year}-{str(year + 1)[-2:]}"
        url = f"https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/{season}/gws/merged_gw.csv"
        try:
            path = fetch(url, f"fpl/{season}.csv")
            fixtures_url = url.replace("gws/merged_gw.csv", "fixtures.csv")
            fixture_path = fetch(fixtures_url, f"fpl/{season}-fixtures.csv")
            fixtures = {r["id"]: r for r in csv.DictReader(fixture_path.open(encoding="utf-8-sig"))}
            players_url = url.replace("gws/merged_gw.csv", "players_raw.csv")
            player_path = fetch(players_url, f"fpl/{season}-players.csv")
            positions = {r["id"]: r.get("element_type") for r in csv.DictReader(player_path.open(encoding="utf-8-sig"))}
            by_game = {}
            try: text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError: text = path.read_text(encoding="latin-1")
            reader = csv.DictReader(io.StringIO(text))
            fields = set(reader.fieldnames or [])
            for row in reader:
                date = row.get("kickoff_time", "")
                # ESPN already covers these English fixtures. Never double-count labels.
                if not date: continue
                overlapping = date >= "2024-08-01"
                if overlapping and "tackles" not in fields: continue
                fixture = row.get("fixture")
                if not fixture or not row.get("element"): continue
                fixture_info = fixtures.get(fixture) or {}
                home = row.get("was_home", "").lower() == "true"
                team = fixture_info.get("team_h" if home else "team_a")
                if not team: continue
                game = by_game.setdefault(fixture, {"id": f"{season}-{fixture}", "source": "fpl", "league": "eng.1",
                    "date": date, "teams": {}})
                side = game["teams"].setdefault(team, {"home": row.get("was_home", "").lower() == "true", "players": []})
                minutes = float(row.get("minutes") or 0)
                if minutes <= 0: continue
                player = {"id": f"{season}:{row['element']}", "name": row["name"], "minutes": minutes,
                    "position": {"1": "G", "2": "D", "3": "M", "4": "F"}.get(positions.get(row["element"]), "M"),
                    "starter": row.get("starts") == "1" if "starts" in fields else minutes >= 60}
                # Fantasy assists differ from official goal assists, so never pool them.
                markets = (("tackles", "tackles"),) if overlapping else (("goals", "goals_scored"), ("saves", "saves"))
                for market, field in markets:
                    if field in fields and row.get(field) not in (None, ""):
                        player[market] = float(row[field])
                side["players"].append(player)
            count = sum(save(game) for game in by_game.values())
            SOURCES.append(provenance(path, url, provider="FPL archive", season=season, status="used" if count else "excluded_overlap",
                games=count, limitations="No xP or post-match season aggregates used. FPL assists are provider-specific."))
            print(f"FPL {season}: {count} nonoverlapping games", flush=True)
        except (requests.RequestException, ValueError) as exc:
            SOURCES.append({"provider": "FPL archive", "season": season, "url": url, "status": "unavailable", "reason": str(exc)})


def statsbomb_game(match, events, league):
    teams, starts, ends = {}, {}, {}
    for event in events:
        typ = (event.get("type") or {}).get("name")
        if typ == "Starting XI":
            tid = str(event["team"]["id"])
            team = teams.setdefault(tid, {"home": tid == str(match["home_team"]["home_team_id"]), "players": []})
            for entry in event["tactics"]["lineup"]:
                pid = str(entry["player"]["id"])
                position = entry["position"]["id"]
                team["players"].append({"id": pid, "name": entry["player"]["name"], "starter": True,
                    "position": "G" if position == 1 else "D" if position <= 8 else "M" if position <= 21 else "F"})
                starts[pid], ends[pid] = 0, 90
        elif typ == "Substitution" and event.get("period", 1) <= 2:
            tid, pid = str(event["team"]["id"]), str(event["player"]["id"])
            minute = min(90, event["minute"] + event.get("second", 0) / 60)
            ends[pid] = minute
            replacement = event["substitution"]["replacement"]
            new_id = str(replacement["id"])
            starts[new_id], ends[new_id] = minute, 90
            # Replacement inherits the outgoing player's position unless tactics specify otherwise.
            old = next((p for p in teams[tid]["players"] if p["id"] == pid), {})
            teams[tid]["players"].append({"id": new_id, "name": replacement["name"], "position": old.get("position", "M"), "starter": False})
    players = {p["id"]: p for team in teams.values() for p in team["players"]}
    for p in players.values(): p.update(shots=0, shots_on_target=0, goals=0, assists=0, fouls=0)
    for event in events:
        if event.get("period", 1) > 2: continue
        pid = str((event.get("player") or {}).get("id"))
        if pid not in players: continue
        p, typ = players[pid], (event.get("type") or {}).get("name")
        if typ == "Shot":
            p["shots"] += 1
            outcome = (event.get("shot", {}).get("outcome") or {}).get("name")
            p["goals"] += outcome == "Goal"
            p["shots_on_target"] += outcome in {"Goal", "Saved", "Saved to Post"}
        if typ == "Pass" and event.get("pass", {}).get("goal_assist"): p["assists"] += 1
        if typ == "Foul Committed": p["fouls"] += 1
        card = (event.get("bad_behaviour", {}).get("card") or event.get("foul_committed", {}).get("card") or {}).get("name")
        if card in {"Red Card", "Second Yellow"}: ends[pid] = min(ends[pid], event["minute"])
    for team in teams.values():
        team["players"] = [dict(p, minutes=max(1, ends[p["id"]] - starts[p["id"]])) for p in team["players"]]
    return {"id": str(match["match_id"]), "source": "statsbomb", "league": league,
            "date": match["match_date"] + "T" + match.get("kick_off", "15:00:00.000") + "Z", "teams": teams}


def statsbomb():
    catalog_path = fetch(f"{SB}/data/competitions.json", "statsbomb/competitions.json")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    for cid, sid, league in ((2, 27, "eng.1"), (9, 281, "ger.1")):
        url = f"{SB}/data/matches/{cid}/{sid}.json"
        path = fetch(url, f"statsbomb/matches-{cid}-{sid}.json")
        matches = json.loads(path.read_text(encoding="utf-8"))
        if len(matches) < 200:
            SOURCES.append(provenance(path, url, provider="StatsBomb", status="excluded_sparse_season", games=0))
            continue
        count, failures, digests = 0, [], []
        def one(match):
            mid = match["match_id"]
            target = GAMES / "statsbomb" / f"{mid}.json"
            if target.exists():
                raw = RAW / f"statsbomb/events/{mid}.json"
                return True, hashlib.sha256((raw if raw.exists() else target).read_bytes()).hexdigest()
            event_url = f"{SB}/data/events/{mid}.json"
            event_path = fetch(event_url, f"statsbomb/events/{mid}.json")
            events = json.loads(event_path.read_text(encoding="utf-8"))
            return save(statsbomb_game(match, events, league)), hashlib.sha256(event_path.read_bytes()).hexdigest()
        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = {pool.submit(one, match): match for match in matches}
            for future in as_completed(pending):
                try:
                    good, digest = future.result(); count += good; digests.append(digest)
                except Exception as exc: failures.append({"match": pending[future]["match_id"], "error": str(exc)})
                if (count + len(failures)) % 50 == 0: print(f"StatsBomb {league}: {count}/{len(matches)}", flush=True)
        SOURCES.append(provenance(path, url, provider="StatsBomb", status="used", games=count, failures=failures,
            event_fingerprint=hashlib.sha256(json.dumps(sorted(digests)).encode()).hexdigest(),
            attribution="StatsBomb Open Data; https://github.com/statsbomb/open-data; research use under publisher terms",
            limitations="Regular time only; event-derived assists and shots definitions; no keeper saves inferred from shot outcomes."))


def wyscout():
    catalog_url = "https://api.figshare.com/v2/collections/4415000/articles?page_size=100"
    articles = requests.get(catalog_url, timeout=30).json()
    files = {}
    for article in articles:
        if article["title"] in {"Events", "Matches", "Players"}:
            data = requests.get(article["url"], timeout=30).json()
            file = data["files"][0]
            path = fetch(file["download_url"], "wyscout/" + file["name"])
            if file.get("computed_md5") and hashlib.md5(path.read_bytes()).hexdigest() != file["computed_md5"]:
                raise ValueError("Wyscout checksum mismatch")
            files[article["title"]] = path
            SOURCES.append(provenance(path, file["download_url"], provider="Wyscout " + article["title"],
                status="downloaded", license=data.get("license"), attribution="Pappalardo et al. 2019, doi:10.1038/s41597-019-0247-7"))
    player_directory = {str(p["wyId"]): p for p in json.loads(files["Players"].read_text(encoding="utf-8"))}
    with zipfile.ZipFile(files["Matches"]) as mz, zipfile.ZipFile(files["Events"]) as ez:
        for country, league in (("England", "eng.1"), ("Spain", "esp.1"), ("Germany", "ger.1"), ("France", "fra.1"), ("Italy", "ita.1")):
            mn = next(n for n in mz.namelist() if n.endswith(f"matches_{country}.json"))
            en = next(n for n in ez.namelist() if n.endswith(f"events_{country}.json"))
            matches = json.loads(mz.read(mn))
            grouped = defaultdict(list)
            for event in json.loads(ez.read(en)): grouped[event["matchId"]].append(event)
            count = 0
            for match in matches:
                teams = {}; events = grouped.get(match["wyId"], [])
                for tid, detail in match["teamsData"].items():
                    lineup = detail.get("formation", {}).get("lineup") or []
                    entries = {str(p["playerId"]): (p, 0, 90) for p in lineup}
                    substitutions = detail.get("formation", {}).get("substitutions") or []
                    for sub in substitutions if isinstance(substitutions, list) else []:
                        old, new, minute = str(sub["playerOut"]), str(sub["playerIn"]), min(90, sub["minute"])
                        if old in entries: entries[old] = (entries[old][0], entries[old][1], minute)
                        entries[new] = ({"playerId": int(new)}, minute, 90)
                    players = []
                    for pid, (entry, start, end) in entries.items():
                        red = str(entry.get("redCards") or "0")
                        if red not in {"0", "null"}:
                            end = min(end, float(red))  # This source records dismissal minute, often as text.
                        info = player_directory.get(pid, {})
                        pos = (info.get("role") or {}).get("code", "")
                        players.append({"id": pid, "name": info.get("shortName") or pid,
                            "position": {"GK": "G", "DF": "D", "MD": "M", "FW": "F"}.get(pos, "M"),
                            "minutes": max(1, end - start), "starter": start == 0,
                            "shots": 0, "shots_on_target": 0, "goals": 0, "assists": 0, "fouls": 0})
                    teams[str(tid)] = {"home": detail.get("side") == "home", "players": players}
                players = {p["id"]: p for t in teams.values() for p in t["players"]}
                for event in events:
                    pid = str(event.get("playerId"))
                    if pid not in players or event.get("matchPeriod") not in {"1H", "2H"}: continue
                    p = players[pid]; tags = {t["id"] for t in event.get("tags", [])}
                    if event.get("eventName") == "Shot" or event.get("subEventName") in {"Free kick shot", "Penalty"}:
                        p["shots"] += 1; p["goals"] += 101 in tags
                        p["shots_on_target"] += 101 in tags or (1801 in tags and 2101 not in tags)
                    if 301 in tags: p["assists"] += 1
                    if event.get("eventName") == "Foul": p["fouls"] += 1
                game = {"id": str(match["wyId"]), "source": "wyscout", "league": league,
                        "date": match["dateutc"].replace(" ", "T") + "Z", "teams": teams}
                count += save(game)
            print(f"Wyscout {country}: {count} games", flush=True)
            SOURCES.append({"provider": "Wyscout", "league": league, "status": "used", "games": count,
                "limitations": "2017/18 research archive; no invented saves or tackles; provider-specific assist definition."})


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    for name, function in (("ESPN", espn), ("FPL", fpl), ("Wyscout", wyscout), ("StatsBomb", statsbomb)):
        try: function()
        except Exception as exc: SOURCES.append({"provider": name, "status": "unavailable", "reason": str(exc)})
        MANIFEST.write_text(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "sources": SOURCES,
            "policy": "Provider IDs never merged by names; same-match FPL/ESPN labels excluded; no current rosters in training",
            "not_available": [
                {"provider": "BALLDONTLIE", "reason": "No BALLDONTLIE credential configured; player stats/props require entitled access"},
                {"provider": "Opta / Sportradar commercial feeds", "reason": "No licensed feed configured"},
                {"provider": "FBref / Understat", "reason": "Not ingested: no verified permitted bulk player-game feed; no access-control bypass"},
                {"provider": "Football-data.org", "reason": "Current team/schedule verification; no comparable historical player count-stat labels"},
                {"provider": "The Odds API", "reason": "Live quote comparison only; no timestamped historical soccer prop-line archive configured"}]}, indent=2), encoding="utf-8")
    print("Soccer source intake complete: " + str(MANIFEST), flush=True)


if __name__ == "__main__": main()
