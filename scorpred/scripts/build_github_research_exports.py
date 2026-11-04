"""Build Phase 3 review exports from retained evidence; no network or ingestion."""
import csv
import json
import re
from datetime import UTC, datetime

from scripts.research_github_sources import OUT

WEIGHTS = {"coverage": 15, "reliability": 15, "freshness": 10, "depth": 10, "provenance": 15,
           "licensing": 15, "ease": 5, "gap_value": 15}


def load(name):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def write_csv(name, rows):
    with (OUT / name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})


def main():
    observed = load("repository_evidence.json")
    supplemental = load("supplemental_evidence.json")
    extra = load("additional_candidates_evidence.json")
    originals = observed["repositories"] + [supplemental["additional_repository"]] + extra["repositories"]
    evidence = {r["requested_repository"]: r for r in originals}
    # Each assessment is an engineering judgement for a bounded use, not an
    # assertion that every upstream row, right, or publication time is verified.
    rows = []

    def add(repo, sport, kind, datasets, coverage, licence, disposition, suitable, score,
            improvement, limits, integration="not integrated", complexity="medium", ids="provider IDs need explicit crosswalk"):
        original = evidence[repo]
        meta = original["metadata"]
        api = original.get("metadata_format", "api") == "api"
        value = json.loads(meta["text"]) if api and meta.get("status") == 200 else {}
        canonical = value.get("full_name") or original.get("canonical_repository") or repo
        branch = value.get("default_branch") or original.get("branch") or "main"
        latest = original.get("maintenance_commit", {})
        commits = json.loads(latest.get("text", "[]")) if latest.get("status") == 200 else []
        code = ({"at": commits[0]["commit"]["committer"]["date"], "sha": commits[0]["sha"],
                 "path": original.get("code_path_checked"), "url": commits[0]["html_url"],
                 "scope": "path commit" if original.get("code_path_checked") else "repository commit; not proof of code maintenance"}
                if isinstance(commits, list) and commits else {"at": None, "scope": "Not verified at checked path"})
        docs = [d["url"] for d in original.get("documents", []) if d.get("status") == 200]
        s = dict(zip(WEIGHTS, score, strict=True))
        rows.append({"repository": repo, "url": "https://github.com/" + repo,
            "canonical_url": "https://github.com/" + canonical, "maintainer": canonical.split("/")[0],
            "sport": sport, "kind": kind, "accessible": meta.get("status") == 200,
            "datasets": datasets, "seasons_and_competitions": coverage,
            "latest_verified_dataset_update": {"at": None, "scope": "No independent dataset update established; see source notes"},
            "latest_verified_code_maintenance": code, "repository_pushed_at": value.get("pushed_at"),
            "archived": value.get("archived"), "default_branch": branch,
            "formats": "CSV/Parquet/RDS" if sport in ("nfl", "nba") and kind == "data" else "See dataset and access notes",
            "access_method": "GitHub release assets or commit-pinned raw files; inspect manifests before any later download",
            "license_and_permitted_use": licence, "automation_restrictions": "GitHub-hosted access subject to host limits; upstream collection permission is a separate gate. No scraper or upstream API query is approved by this inventory.",
            "rate_limits": "GitHub REST:60 unauthenticated requests/hour/IP; authenticated baseline5000/hour, secondary limits apply. No universal raw/CDN quota or upstream NBA/ESPN authorized collection quota verified.",
            "historical_availability": "File/asset existence and declared season scope only; per-game completeness not measured without ingestion",
            "pregame_timestamp_reliability": "Historical Class B at best: release/commit/mtime is not original pregame observation. New genuine receipts can establish prospective availability only.",
            "missing_data_limitations": limits, "existing_integration": integration,
            "compatibility": "Source-specific adapter and isolated sport namespace; never replace an existing model input implicitly",
            "identifiers": ids, "integration_complexity": complexity, "expected_improvement": improvement,
            "net_new_usable_records": None, "overlap": "Not measured; reconciliation required before import, no novelty assumed",
            "disposition": disposition, "verified_suitable_bounded_data_source": suitable,
            "unconditional_production_recommendation": False, "score_components": s,
            "score_100": round(sum(WEIGHTS[k] * s[k] / 5 for k in WEIGHTS), 1),
            "evidence_urls": [meta["url"], *docs],
            "evidence_snapshot": ("supplemental_evidence.json" if repo == "greerreNFL/nfelo" else
                                  "additional_candidates_evidence.json" if repo in {r["requested_repository"] for r in extra["repositories"]}
                                  else "repository_evidence.json")})

    add("nflverse/nflverse-data", "nfl", "data", "Schedules/results, PBP, EPA/success, team/player stats, NGS, rosters, depth, injuries, snap counts, participation and FTN",
        "PBP assets1999–2026; roster support1920+; auxiliary eras differ; participation2016–2025, no2026 asset in inspected manifest",
        "CC-BY4.0 for ordinary data according to current nflverse policy; FTN charting and FTN-era participation CC-BY-SA4.0. Check each release exception and attribution. No blanket permission for independent upstream scraping.",
        "retain existing integration; selectively repair gaps", True, [5,4,5,5,4,4,5,4],
        "Fill missing2023/2025 auxiliary partitions through existing loaders, refresh stale auxiliaries, review a versioned CPOE projection only if justified. No duplicate core import.",
        "Mutable release assets, later corrections and postgame aggregates. Publication lag unknown for many historical fields. Current participation intentionally absent.",
        "already integrated:90 local dataset/season partitions", "low", "nflverse game_id, GSIS, PFR and ESPN namespaces; existing explicit player crosswalk")
    add("nflverse/nflreadpy", "nfl", "client", "Python loaders and cache/download policies for nflverse releases", "Loader-specific support; not an independent dataset",
        "MIT client; dataset licences remain source-specific", "retain existing client", False, [5,4,4,4,4,4,5,3],
        "Already solves Python access; use existing pinned0.1.5 rather than add another ingestion stack", "An installed loader does not establish historical availability or completeness", "already integrated", "low")
    add("nflverse/nflfastR", "nfl", "pipeline", "PBP parsing, EPA/WPA and derived statistics", "NFL PBP era1999+; outputs distributed by nflverse-data",
        "MIT code per DESCRIPTION; distributed data policy belongs to nflverse releases", "reference only; no new parser/model", False, [5,4,4,5,4,4,2,2],
        "Stat-definition reference and schema audit; existing PBP already supplies EPA/success", "Running or refitting EPA models is outside this task; same underlying data", "existing outputs indirectly consumed", "high")
    add("nflverse/nflverse-rosters", "nfl", "pipeline", "Roster, depth-chart and injury generation", "Rosters1920+, weekly2002+, injuries2009+, depth2001+ loader support;2025+ depth timestamps",
        "MIT code per DESCRIPTION; released ordinary data CC-BY4.0 subject to exceptions", "reference existing producer; repair refresh ownership", False, [4,4,4,4,4,4,4,4],
        "Identify current temporal depth schema and refresh schedule; consume existing release host rather than scrape", "No dedicated ScorPred auxiliary scheduler owner; historical injury publication unknown", "roster/depth/injury loaders and data already present", "low")
    add("nflverse/nfldata", "nfl", "reference", "NFL schedule/helper datasets and reference IDs", "Historical and current NFL files; exact standalone file ranges not inventoried",
        "No standalone licence found in inspected README/LICENSE paths; org release policy supports nflverse-data, not an automatic relicence of every repository file", "do not add independent import", False, [4,3,4,2,2,1,4,1],
        "No established extra coverage over existing schedules", "Very recent repository activity is not an independently verified dataset update", "same lineage as existing schedule source", "low")
    add("nflverse/nflverse-players", "nfl", "pipeline", "GSIS/PFR/ESPN/OTC/PFF ID reconciliation and manual overrides", "Global historical/current player directory; exact per-source completeness not established",
        "MIT code per DESCRIPTION; consume licensed players release through current loader", "reference only; do not install internal package", False, [4,4,4,3,4,4,4,3],
        "Investigate measured missing GSIS links using released explicit IDs;24,854 entries already local", "Maintainer explicitly labels internal package; V2 renamed/removed variables; some links unresolved", "load_players and explicit crosswalk already present", "low")
    add("nfeloapp/nfelo", "nfl", "unresolved", "Unverified URL", "Not verified:HTTP404", "Not verified", "unresolved URL; reject as supplied", False, [0]*8, "None", "No repository verified")
    add("greerreNFL/nfelo", "nfl", "model", "NFL power-rating and prediction implementation", "See project; no independent raw-data coverage established",
        "No grant to use third-party data inferred from model repository", "reject integration: new model and overlapping data", False, [3,2,2,3,2,1,1,0],
        "Methodology context only; no need to replace current Elo or frozen models", "A different Elo implementation cannot repair provenance or establish incremental accuracy", complexity="high")

    add("sportsdataverse/hoopR", "nba", "client", "R NBA/ESPN PBP, boxscore and bulk loaders; other providers out of scope", "NBA loaders document2002+ ending years; no single shared earliest year for every field",
        "MIT code; underlying data and provider rights separate", "candidate documentation/client; no R installation needed", False, [5,4,4,5,3,2,2,4],
        "Identify release tags/columns; Python can consume permitted Parquet directly", "Current upstream code does not prove source collection permission or same-day pipeline health", complexity="medium", ids="ESPN and NBA Stats identities differ")
    add("sportsdataverse/sportsdataverse-data", "nba", "data", "Central ESPN NBA releases: schedules, PBP, team/player boxes, game rosters; separate NBA Stats tags",
        "Inspected ESPN release asset names2002–2027 ending years; per-tag manifest is authoritative, no uniform completeness assumption",
        "README labels data CC-BY4.0 while root LICENSE is MIT software text; producer LICENSE.md says CC-BY4.0. Upstream rights/redistribution for ScorPred remain unverified.",
        "priority conditional candidate; permission gate before sports-data import", False, [5,4,5,4,3,2,4,5],
        "Potential20 earlier ending-year seasons versus2022–26 local scoring archive; player/team boxes and historical ESPN IDs. Same ESPN lineage, not an independent schedule truth.",
        "Mutable files; publication timestamps mostly postgame. Host/producer licence discrepancy and source permission remain gates.", complexity="medium", ids="ESPN game/player/team IDs align most closely with current adapters")
    add("sportsdataverse/hoopR-data", "nba", "archive", "Legacy NBA game index and older PBP data", "Historical legacy archive; precise row scope not verified", "No root licence verified in inspected files",
        "reject as current feed; archive reference only", False, [3,2,0,3,2,0,3,1], "No advantage over the maintained release host demonstrated", "GitHub archived=true; last push2023-04-05. Some old loader links still point here.")
    add("sportsdataverse/hoopR-nba-data", "nba", "pipeline", "Current ESPN NBA release producer: schedules, PBP, boxes, rosters, crosswalks, shots", "Declared2002–present; release assets independently checked through2027 ending year",
        "LICENSE.md expressly CC-BY4.0 for data/code/docs; root LICENSE stub and release-store MIT text do not settle upstream rights", "best producer to investigate behind hosted-release candidate", False, [5,4,5,5,3,2,3,5],
        "Historical boxscore/minute coverage with existing ESPN IDs; use releases rather than replicate scraping pipeline", "Do not count this producer and its release store as independent datasets; no original pregame injury/lineup ledger", complexity="medium", ids="ESPN IDs; producer publishes crosswalks needing audit")
    add("sportsdataverse/hoopR-nba-stats-data", "nba", "pipeline", "NBA Stats boxes, schedules, possession/lineup and advanced-data processing", "Documented traditional player/team boxes1997–2026 ending years,30 seasons; other releases vary",
        "Producer LICENSE.md CC-BY4.0; NBA source terms need affirmative resolution for proposed use", "conditional historical candidate; current refresh not established", False, [5,3,2,5,3,2,2,5],
        "Potential25 earlier seasons versus existing archive; official numeric IDs and shot/rebound/turnover counts for defined efficiency metrics", "Release-hub dormant notice conflicts with recent code/docs publication. Documentation says2026-10-01 boxes; do not assume live readiness. Some2026 games absent in producer coverage table.", complexity="high", ids="NBA game_id string preserves leading zeros;161061xxxx teams/person_id distinct from ESPN")
    add("swar/nba_api", "nba", "client", "NBA Stats and live API schema/client; player/team static IDs; AdvancedV3 and player logs", "Endpoint-specific history; no downloaded historical dataset and no universal support range",
        "MIT client only; no NBA data-collection/reuse grant", "conditional schema/client reference; direct collection blocked pending permission", False, [5,4,4,5,3,1,3,4],
        "Explicit endpoint field contracts for minutes/ratings/pace/eFG/TS/rebounding/turnovers, and NBA IDs", "No published authorized numeric request quota verified; throttling/cloud timeouts documented. Do not use proxies to evade blocks.", complexity="high", ids="Official NBA IDs; require sourced ESPN crosswalk")
    add("dblackrun/pbpstats", "nba", "parser", "Possession and on-court lineup reconstruction from PBP", "Provider-dependent history; no independently hosted complete archive",
        "MIT parser; source data rights separate", "defer: parser only, maintenance/schema risk", False, [3,2,1,5,3,1,1,2],
        "Potential consistency checks after permitted PBP acquisition, not a current data source", "Last checked pbpstats-path code commit2024; possession boundaries/substitution corrections differ from estimated possession formulas", complexity="high")
    add("shufinskiy/nba_data", "nba", "data", "Bulk PBP, shots, matchups and pbpstats-derived possession files", "README1996/97–2024/25; manifest includes2025-start keys; per-type ranges differ",
        "Apache2.0 repository; underlying NBA/pbpstats collection and redistribution permission not established", "conditional archive; no unrestricted recommendation", False, [5,3,2,5,2,1,3,4],
        "Historical events older than local NBA archive; no new source-independence from underlying NBA feeds", "README/manifest horizon mismatch; exact dataset last update and row completeness not established", complexity="high")

    add("statsbomb/open-data", "soccer", "data", "Selected events, shots/xG, passes, lineups, substitutions and360 subsets", "80 competition-season entries; EPL2003/04 and2015/16; Bundesliga2015/16 and2023/24; selected LaLiga seasons; no supported-league2026/27 entry",
        "Custom StatsBomb Public Data User Agreement, updated2023-09-08; noncommercial research scope, distribution/commercial restrictions, registration request and publication/logo attribution. Not CC-BY or unrestricted production data.",
        "conditional isolated noncommercial research only", False, [2,4,2,5,4,2,2,2],
        "Research event semantics and provider-specific xG on selected historical games; does not fill current3-league coverage", "Competition entry does not imply full-season matches. match_updated/available are revision metadata, not historical prekickoff receipts. Canonical repo redirects to hudl/open-data.", complexity="high", ids="StatsBomb competition_id/season_id/match_id/team/player IDs need explicit mapping")
    add("openfootball/football.json", "soccer", "data", "Multi-league fixtures/results in JSON", "Season directories2010/11–2026/27; current folder has en.1,de.1,es.1 plus other leagues; exact per-file completeness unmeasured",
        "CC0/public-domain dedication explicitly covers schema, data and scripts", "verified suitable for bounded historical results/lineage reconciliation", True, [4,3,2,1,3,5,5,5],
        "Recover genuinely labelled2018/19 EPL, cross-check18 audited missing cached fixtures and three-league season lineage; import only unmatched permitted games", "Inspected2026/27 EPL file last changed2026-09-22, about30 days old; volunteer updates have no SLA. Names/date/round lack original provider IDs and often timezones.", complexity="medium", ids="No shared canonical game/player IDs; verified team aliases plus competition/season/date/home/away keys")
    add("openfootball/england", "soccer", "data", "English top-four-tier results/calendars in Football.TXT", "Historical seasons, maintained through2026/27 folder; exact complete season list not enumerated",
        "CC0/public-domain dedication", "verified suitable historical cross-check; prefer to stale mirrors", True, [4,3,2,1,3,5,3,4],
        "Recover EPL season identity and promoted-team history without replacing already complete2019–24 results", "2026/27 path last changed2026-09-21 although repo pushedOct15; custom text parsing and timezones require validation. Same family as football.json.", complexity="medium", ids="Team-name aliases, season-specific competition and original row locator")
    for repo, coverage, scope in (
        ("footballcsv/england", "Decade folders1990s–2020s, latest folder2020/21; EPL and lower divisions", "English"),
        ("footballcsv/deutschland", "Decade folders1960s–2020s, latest folder2020/21; Bundesliga and lower divisions", "German"),
        ("footballcsv/espana", "2010s and2020/21 folders; LaLiga and Segunda", "Spanish"),
    ):
        add(repo, "soccer", "data", f"{scope} historical match-result CSV mirror", coverage,
            "CC0-1.0 file and public-domain README; explicitly a1:1 export of openfootball source", "verified suitable historical archive only", True,
            [3,3,0,1,3,5,5,2], "Convenient historical format for a measured missing-season gap; zero assumed novel records over upstream openfootball",
            "Stops at2020/21 folder in inspected tree; some seasons partial; not a current feed or independent confirmation of its own upstream", complexity="low", ids="Names/dates; no guaranteed game ID or UTC kickoff")
    add("andrewbaisden/football-data", "soccer", "unresolved", "Unverified requested repository", "HTTP404 at requested URL", "Not verified",
        "requested URL unavailable; do not substitute another project silently", False, [0]*8, "None established", "No accessible repository or licence verified")
    add("metrica-sports/sample-data", "soccer", "sample", "Anonymized tracking and events for3 sample games", "Two original CSV samples plus Game3 added2021-04-15; identities/competitions anonymized",
        "README requests acknowledgement; no explicit comprehensive licence grant verified", "reject for league coverage; rights review for optional examples", False, [0,2,0,4,2,0,1,0],
        "No measurable gain for supported leagues or H/D/A validation", "Not maintained as a current league dataset;3 examples cannot validate winner reliability", complexity="high")
    add("jfjelstul/englishfootball", "soccer", "data", "Seasons/teams/matches, team appearances, end-season standings", "1888/89–2023/24 English top4 tiers; author reports208,028 matches; files not imported/recounted",
        "CC-BY-SA4.0 explicitly covers database and R package; attribution and adapted-work share-alike required", "verified suitable isolated historical research with share-alike compliance", True,
        [4,4,1,2,4,3,4,4], "Independent historical structure/IDs for missing2018 EPL and promoted clubs; quantify exact overlap first",
        "Not a current feed; old lower-tier populations/phoenix-team conventions require explicit policy; end-season standings leak if used within season", complexity="medium", ids="Author team_id across name changes; phoenix lineage not automatically adopted")
    add("SkillCorner/opendata", "soccer", "sample", "Broadcast tracking and derived events; season physical aggregates", "10 Australian A-League2024/25 sample games; not EPL/Bundesliga/LaLiga",
        "MIT file and open-data README; preserve attribution; file-specific scope must be checked before reuse", "reject for current ScorPred league gaps; optional isolated format sample", False,
        [1,3,3,5,3,3,1,0], "Zero verified supported-league coverage increase", "Recent repository activity does not turn10 sample games into broad current league coverage; inferred tracking positions and definitions differ", complexity="high")
    add("SkillCorner/opendata-basketball", "nba", "sample", "Tracking, dynamic events and offensive aggregates", "10 Spanish ACB2025/26 sample games; aggregates293/327 games, not NBA",
        "MIT file and open-data README; preserve attribution and verify scope", "reject for NBA integration: wrong competition", False,
        [0,3,3,5,3,3,1,0], "Zero additional NBA games", "A basketball dataset is not necessarily NBA; sample/full-aggregate scopes differ and possession definitions are proprietary-derived", complexity="high")

    by_repo = {r["repository"]: r for r in rows}
    # Asset updated_at measures the inspected file version, not release creation,
    # code activity or the latest game contained within the file.
    releases = []
    for e in supplemental["evidence"]:
        if e.get("status") != 200 or "/releases/tags/" not in e["url"]:
            continue
        release = json.loads(e["text"])
        assets = release.get("assets", [])
        releases.append({"repository": release["html_url"].split("/releases/")[0], "tag": release["tag_name"],
            "release_created_at": release["published_at"], "observed_at": e["observed_at"],
            "asset_count": len(assets), "newest_asset_updated_at": max((a["updated_at"] for a in assets), default=None),
            "assets": [{k: a.get(k) for k in ("id", "name", "size", "updated_at", "browser_download_url", "digest")} for a in assets],
            "evidence_url": e["url"], "payload_downloaded": False})
    nfl = next(r for r in releases if r["tag"] == "pbp")
    for repo in ("nflverse/nflverse-data", "nflverse/nflreadpy", "nflverse/nflfastR"):
        by_repo[repo]["latest_verified_dataset_update"] = {"at": nfl["newest_asset_updated_at"], "scope": "PBP release asset update; same data lineage, not latest played game", "url": nfl["evidence_url"]}
    for repo in ("nflverse/nflverse-rosters", "nflverse/nflverse-players"):
        r = next(r for r in releases if r["tag"] == "rosters")
        by_repo[repo]["latest_verified_dataset_update"] = {"at": r["newest_asset_updated_at"] if repo.endswith("rosters") else None,
            "scope": "rosters release only; players release not independently checked", "url": r["evidence_url"]}
    espn = next(r for r in releases if r["tag"] == "espn_nba_player_boxscores")
    for repo in ("sportsdataverse/hoopR", "sportsdataverse/sportsdataverse-data", "sportsdataverse/hoopR-nba-data"):
        by_repo[repo]["latest_verified_dataset_update"] = {"at": espn["newest_asset_updated_at"], "scope": "ESPN player-box release asset update; same source family", "url": espn["evidence_url"]}
    by_repo["sportsdataverse/hoopR-nba-stats-data"]["latest_verified_dataset_update"] = {"at": "2026-10-01", "scope": "Producer documentation claims boxscore publication; independent release timestamp not fetched", "url": "https://github.com/sportsdataverse/hoopR-nba-stats-data/blob/main/docs/datasets/player_boxscores.md"}
    tech = load("technical_evidence.json")["evidence"]
    paths = {"openfootball/football.json": "/2026-27/en.1.json", "openfootball/england": "/2026-27",
             "footballcsv/england": "/2020s", "footballcsv/deutschland": "/2020s", "footballcsv/espana": "/2020s", "statsbomb/open-data": "/data/competitions.json"}
    for repo, path in paths.items():
        match = next((e for e in tech if "/commits/" in e["url"] and path in e["url"] and (repo in e["url"] or repo == "statsbomb/open-data" and "hudl/open-data" in e["url"])), None)
        dates = re.findall(r'"committedDate"\s*:\s*"([^"]+)"', match.get("text", "")) if match else []
        if dates:
            by_repo[repo]["latest_verified_dataset_update"] = {"at": dates[0], "scope": "Latest inspected data-path commit; not a completeness or original-publication claim", "url": match["url"]}
    for original in extra["repositories"]:
        repo = original["requested_repository"]
        for suffix, field in (("/data", "latest_verified_dataset_update"), ("/R", "latest_verified_code_maintenance")):
            entry = next((e for e in original["documents"] if "/commits/" in e["url"] and e["url"].endswith(suffix)), {})
            dates = re.findall(r'"committedDate"\s*:\s*"([^"]+)"', entry.get("text", ""))
            if dates:
                by_repo[repo][field] = {"at": dates[0], "scope": "Latest inspected path commit", "url": entry["url"]}
    for repo in ("openfootball/football.json", "openfootball/england", "footballcsv/england", "footballcsv/deutschland", "footballcsv/espana", "statsbomb/open-data", "metrica-sports/sample-data"):
        by_repo[repo]["latest_verified_code_maintenance"] = {"at": None, "scope": "Data-oriented repository; no separately verified code-maintenance date; repository activity is recorded separately"}
    by_repo["openfootball/football.json"]["formats"] = "JSON"
    by_repo["openfootball/england"]["formats"] = "Football.TXT"
    by_repo["statsbomb/open-data"]["formats"] = "JSON; selected360 JSON"
    by_repo["jfjelstul/englishfootball"]["formats"] = "CSV/SQLite/RData"
    for repo in ("footballcsv/england", "footballcsv/deutschland", "footballcsv/espana"):
        by_repo[repo]["formats"] = "CSV"
    for repo, formats in {
        "nflverse/nflreadpy": "Python client; Polars DataFrames; release format varies by loader",
        "nflverse/nflfastR": "R code/data frames; outputs delivered as CSV/Parquet/RDS releases",
        "nflverse/nflverse-rosters": "R producer code; CSV/Parquet/RDS release outputs",
        "nflverse/nflverse-players": "R producer code; released player directory/crosswalk files",
        "sportsdataverse/hoopR": "R client/data frames; CSV/Parquet/RDS bulk releases and JSON APIs",
        "sportsdataverse/hoopR-nba-data": "Python/R producer code; CSV/Parquet/RDS release outputs",
        "sportsdataverse/hoopR-nba-stats-data": "Python/R producer code; CSV/Parquet/RDS release outputs",
        "swar/nba_api": "Python client; JSON API responses; bundled reference records",
        "dblackrun/pbpstats": "Python parser; source-specific JSON PBP and parsed possessions",
        "shufinskiy/nba_data": "CSV in tar.xz archives; text file index",
        "metrica-sports/sample-data": "CSV; FIFA EPTS tracking/metadata; JSON events",
        "SkillCorner/opendata": "JSON tracking/events; CSV season aggregates; sample formats vary",
        "SkillCorner/opendata-basketball": "JSON; compressed JSONL tracking; CSV aggregates",
    }.items():
        by_repo[repo]["formats"] = formats
    for row in rows:
        if row["latest_verified_dataset_update"].get("url"):
            row["evidence_urls"].append(row["latest_verified_dataset_update"]["url"])
        if row["latest_verified_code_maintenance"].get("url"):
            row["evidence_urls"].append(row["latest_verified_code_maintenance"]["url"])
    for repo, urls in {
        "nflverse/nflverse-data": ["https://github.com/nflverse#licensing", "https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html"],
        "nflverse/nflfastR": ["https://github.com/nflverse/nflfastR/blob/master/DESCRIPTION"],
        "nflverse/nflverse-rosters": ["https://github.com/nflverse/nflverse-rosters/blob/main/DESCRIPTION"],
        "nflverse/nflverse-players": ["https://github.com/nflverse/nflverse-players/blob/master/DESCRIPTION"],
        "sportsdataverse/hoopR": ["https://github.com/sportsdataverse/hoopR/blob/main/DESCRIPTION", "https://github.com/sportsdataverse/hoopR/blob/main/R/load_nba.R"],
        "sportsdataverse/hoopR-nba-data": ["https://github.com/sportsdataverse/hoopR-nba-data/blob/main/LICENSE.md"],
        "sportsdataverse/hoopR-nba-stats-data": ["https://github.com/sportsdataverse/hoopR-nba-stats-data/blob/main/LICENSE.md", "https://github.com/sportsdataverse/hoopR-nba-stats-data/blob/main/docs/datasets/player_boxscores.md"],
        "swar/nba_api": ["https://github.com/swar/nba_api/blob/master/src/nba_api/stats/endpoints/boxscoreadvancedv3.py"],
        "shufinskiy/nba_data": ["https://github.com/shufinskiy/nba_data/blob/main/list_data.txt"],
        "statsbomb/open-data": ["https://github.com/hudl/open-data/blob/master/LICENSE.pdf", "https://github.com/hudl/open-data/blob/master/data/competitions.json"],
    }.items():
        by_repo[repo]["evidence_urls"].extend(urls)
    summary = {"generated_at": datetime.now(UTC).isoformat(), "candidate_urls_checked": len(rows),
        "accessible_repositories": sum(r["accessible"] for r in rows),
        "verified_suitable_bounded_data_repositories": sum(r["verified_suitable_bounded_data_source"] for r in rows),
        "unconditional_new_production_integrations": 0, "new_sports_records_imported": 0,
        "interpretation": "Suitable means identified bounded historical/data reuse under documented conditions; not continuous freshness, independent source count, row completeness, legal warranty, or production-model approval",
        "scoring": {"scale": "0–5; weighted score /100; unknowns penalized", "weights": WEIGHTS,
                    "hard_gate": "Numeric score never overrides rights, competition, availability or sealed-outcome restrictions"}, "sources": rows}
    (OUT / "sources.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv("sources.csv", rows)
    (OUT / "release_manifests.json").write_text(json.dumps(releases, indent=2), encoding="utf-8")
    competition = next(e for e in supplemental["evidence"] if e.get("status") == 200 and e["url"].endswith("data/competitions.json"))
    write_csv("statsbomb_competition_seasons.csv", json.loads(competition["text"]))
    print(json.dumps({k: v for k, v in summary.items() if k not in ("sources", "scoring")}, indent=2))


if __name__ == "__main__":
    main()
