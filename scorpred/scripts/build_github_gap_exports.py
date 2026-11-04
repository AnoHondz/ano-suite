"""Research-only gap matrices; no collection, model, or application changes."""
import json

from scripts.build_github_research_exports import write_csv
from scripts.research_github_sources import OUT

CLASSIFICATIONS = ["Already available and reliable", "Already available but incomplete",
    "Available from a verified GitHub source", "Available but licensing or provenance is uncertain",
    "Unavailable from verified free sources"]


def main():
    rows = []

    def add(sport, dataset, state, existing, source, gain, overlap, ids, use, issue, evidence):
        rows.append({"sport": sport, "dataset": dataset, "classification": CLASSIFICATIONS[state], "existing": existing,
            "candidate_source": source, "measurable_gain_or_test": gain, "net_new_records": None, "overlap": overlap,
            "identity_reconciliation": ids, "historical_or_prospective": use, "audited_issue": issue, "evidence": evidence})

    nfl = "https://github.com/nflverse/nflverse-data"
    local = "docs/assets/github_data_research/existing_inventory.json"
    nfl_ids = "Existing nflverse game_id and explicit GSIS/PFR/ESPN mapping; no heuristic player-name joins"
    nba_ids = "ESPN IDs when same lineage; official NBA10-character game IDs and161061xxxx team/person IDs need independently sourced crosswalk"
    soc_ids = "League/season plus validated team aliases and source record locator; UTC kickoff only when timezone evidenced"
    add("nfl", "Historical schedules/results and core PBP", 0, "2022–2026 stored;284/285/285/285/272 schedule rows, PBP all5seasons", nfl,
        "Do not reimport valid core history; quantify schema/receipt gaps only", "High: existing core already imported", nfl_ids, "Historical results trusted within audited scope; source availability remains Class B", "NFL-002", local)
    add("nfl", "EPA/success, offensive/defensive team and QB aggregates", 1, "PBP EPA/success and derived team/QB records exist;2026 team stats88vs93 audited completed fixtures", nfl,
        "Refresh/derive missing5feed games after source eligibility checks; report changed missingness, not assumed accuracy gain", "Mostly existing rows; derived records not independent data", nfl_ids, "Prior games only; target-game EPA forbidden; historical revisions Class B", "NFL-002 /VAL-001", "docs/NFL_DATA_DIAGNOSIS.md")
    add("nfl", "PBP projection/schema completeness", 1, "Current sync projection includes no_play; CPOE not projected. Audit stored projection predated this source check", nfl,
        "Compare allowed historical schema keys against current source dictionaries before proposing a versioned extension", "Same PBP plays; new column is not a new game", nfl_ids, "No new predictor automatically justified", "NFL-002", "scripts/sync_nflverse.py:PBP_COLUMNS")
    for dataset in ("Rosters /weekly rosters", "Depth charts", "Injuries /availability", "Snap counts", "NGS passing/rushing/receiving", "PFR advanced weeks", "FTN charting"):
        add("nfl", dataset, 1, "Existing loaders and2022/2024/2026 partitions; most auxiliary feeds lack2023/2025 and retainOct18observations", nfl,
            "Enumerate and reconcile missing2023/2025 partitions; assign one periodic refresh owner; FTN requires share-alike decision", "Existing2022/24/26 values overlap; do not import wholesale", nfl_ids,
            "Postgame stats from prior games only; timestamped depth changes potentially support prospective context; injury publication proof still missing", "NFL-002 /VAL-001", local)
    add("nfl", "Historical participation2023/2025", 2, "Only2022/2024 participation partitions present", nfl,
        "Release manifest confirms files through2025; quantify missing historical games after FTN licence review", "2022/2024 overlap;2023/2025 candidates new to local partition list", nfl_ids,
        "After-season release cannot be a same-season historical pregame input; later-season use still needs availability proof", "NFL-002", "https://github.com/nflverse/nflverse-data/releases/tag/pbp_participation")
    add("nfl", "In-season2026 participation", 4, "Intentionally unavailable in current importer and verified release manifest", "None verified",
        "Zero current-season usable games established; do not scrape a substitute", "Not applicable", nfl_ids, "No prospective feature until legitimately published", "NFL-002", "https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html")
    add("nfl", "Original historical pregame availability for advanced statistics", 4, "54/100 historical columns masked; current receipts cannot backdate them", "None of the researched repositories establishes this archive",
        "Collect genuine receipts prospectively; keep final109sealed and existing masks", "New historical rows cannot manufacture original receipt evidence", nfl_ids, "Prospective only for Class A", "VAL-001 /NFL-001", "docs/NFL_PHASE2_2_RELIABILITY.md")
    add("nfl", "Canonical game/player identity", 1, "24,854player entries and existing explicit crosswalk; missing GSIS links observed", "nflverse/nflverse-players outputs via existing loader",
        "Measure crosswalk match rates and retain unresolved queue; no second directory import", "Same directory and ID lineage", nfl_ids, "Effective-date mappings required", "NFL-002", "services/nfl_catalog.py")

    for dataset, existing, gain in (
        ("Historical schedule/results universe", "6,048eligible monthly ESPN games, ending2022–2026;2022partial", "Reconcile2002–2027release manifests first;20earlier ending-year seasons potentially available, net games unknown"),
        ("Player box scores /minutes", "Legacy real-ID props logs empty; public overviews descriptive only", "Verify per-game real-player IDs, minutes and required count components; do not infer logs from averages"),
        ("Team box scores /shooting /rebounds /turnovers", "Score/form available, no complete possession-based boxscore table", "Measure fraction of historical games with both teams'FGA/3PM/FTA/OREB/DREB/TOV/minutes"),
        ("Play-by-play", "Not a durable NBA event store", "Inspect2002+ESPNrelease schema and same-game completeness; qualify provider event semantics"),
        ("Possessions /pace /offensive and defensive ratings", "PPG proxies are not verified possessions or efficiency", "Possession definition and overtime-aware minute contract first; compare reconstructed vs published counts"),
        ("eFG /TS /rebound and turnover percentages", "Not demonstrated durable current inputs", "Derive only named versioned formula families from verified prior-game denominators; never silently substitute official fields"),
    ):
        add("nba", dataset, 3, existing, "SportsDataverse ESPN/NBA Stats release families; swar/nba_api schemas", gain,
            "ESPN historical results overlap extensively; NBA Stats another publication pipeline, underlying NBA still shared", nba_ids,
            "Chronological reconstruction Class B; previous games plus conservative lag, never target-game boxscore", "NBA-001 /VAL-001", "https://github.com/sportsdataverse/hoopR-nba-data")
    add("nba", "Postgame actual starting lineups /on-court stints", 3, "No validated lineup archive", "NBA Stats release docs; dblackrun/pbpstats parser",
        "Verify actual lineup/minute coverage after permission; keep actual and announced lineups separate", "Multiple PBP sources may describe same events", nba_ids, "Actual target-game starters cannot be used as pregame announced starters", "NBA-001 /VAL-001", "https://github.com/dblackrun/pbpstats")
    add("nba", "Historical pregame announced lineups", 4, "No receipt-backed announcement ledger", "None verified",
        "Record permitted prospective announcements with actual receipt and confirmation status", "Postgame lineup archives do not fill this gap", nba_ids, "Prospective collection only unless an independently timestamped archive is proven", "VAL-001", "docs/NBA_DATA_DIAGNOSIS.md")
    add("nba", "Injury /availability and roster changes", 1, "Audited87injury entries/29roster payloads; not league-wide historical publication archive", "Existing ESPN adapter; no verified free GitHub pregame injury/transaction replacement",
        "Distinguish unknown from healthy; validate effective dates and source publication; enumerate coverage", "Roster snapshots can overlap and cannot prove historical transactions", nba_ids, "No retroactive injury assumptions", "NBA-001 /VAL-001", "docs/NBA_DATA_DIAGNOSIS.md")
    add("nba", "Rest days /back-to-backs /home-away form", 1, "Existing schedule-derived logic and season checks", "Existing data plus conditional broader schedule manifests",
        "Add only games demonstrably missing; prove timezone/cutoff parity on identical histories", "High overlap; no need for another rest feed", nba_ids, "Prior fixture chronology; correct season-type scope", "VAL-001", "nba_live_client.py")
    add("nba", "Timestamped pregame probability/input archive", 4, "Ordinary tracking is insufficient;35audited NBA rows lack required binding", "No researched archive validates ScorPred's own predictions",
        "Add immutable receipts and exact current-rule policy/input identity; compare existing methods only later", "External probabilities are not current-model evidence", nba_ids, "Prospective Class A", "VAL-001", "docs/SCORPRED_MODEL_VALIDATION_AUDIT.md")
    add("nba", "Broadcast tracking sample", 3, "No NBA tracking integration", "SkillCorner/opendata-basketball",
        "Zero NBA games: sample is Spanish ACB; reject for requested NBA gap", "No NBA overlap because wrong competition", "SkillCornerIDs not NBAIDs", "Not eligible for NBA validation", "NBA-001", "https://github.com/SkillCorner/opendata-basketball")

    add("soccer", "Existing valid fixtures and result statistics", 0, "7,888unique classifier fixtures; existing2019–24EPL raw seasons complete in audit", "Existing source files retained",
        "Do not reimport known complete seasons; reliable fixture/result checks do not establish league lineage", "High overlap with proposed results repositories", soc_ids,
        "Historical Class B, standalone dataset end2026-04-19", "SOC-004", "docs/SOCCER_DATA_DIAGNOSIS.md")
    add("soccer", "Genuine2018EPL /missing result fixtures", 2, "Mislabeled2018file quarantined; newStage1derived2599rows; oldoriginals preserved", "openfootball/football.json; openfootball/england; jfjelstul/englishfootball",
        "Reconcile genuine2018season;380is league capacity, not a verified number of new rows; inspect18cached previous-league gaps and61missing2025rawEPL fixtures", "2019–24mostly already available;208028Fjelstulrows mostly outside immediate scope", soc_ids,
        "Historical results with verified league/season; no original kickoff availability claim", "SOC-001 /SOC-004", "https://github.com/openfootball/football.json")
    add("soccer", "League/season lineage for existing classifier data", 1, "All7,888rows lack league/source-availability lineage; cannot infer from team names", "Results manifests may support exact verified joins, otherwise unresolved",
        "Report mapping and ambiguity rates per league/season; never assign by nearest name alone", "A new source does not automatically fix identity of old rows", soc_ids, "Versioned research copy only; no retraining or production artifact change", "SOC-004", "docs/SOCCER_DATA_DIAGNOSIS.md")
    add("soccer", "Fixtures /competition calendars", 1, "Three supported current leagues, lazy incomplete cache; source receipts mostly ephemeral", "openfootballresults are a historical cross-check only",
        "Retain current provider unless a permitted source is demonstrably fresher; en.1currentfilelastchangedSep22", "Same games across result exports", soc_ids, "No stale volunteer fixture file as live fallback", "OPS-001 /SOC-004", "https://github.com/openfootball/football.json/commits/master/2026-27/en.1.json")
    add("soccer", "Home-away goals/form /team strength /congestion /standings", 1, "Existing rolling goals/results/Elo/rest;7of58teamnames absent from savedElo", "Permitted results archives; FjelstulteamIDs and standings",
        "Resolve promoted-club history and complete schedules; compute standings causally, never import end-season table as pregame strength", "Metrics are derived from overlapping games, not independent sources", soc_ids,
        "Prior90-minute results and kickoff evidence; no automatically imported external rating model", "SOC-004 /VAL-001", "https://github.com/jfjelstul/englishfootball")
    for dataset in ("xG /shots and shots on target", "Passes /passing accuracy /possession", "Corners /fouls /cards", "Player events /lineups /substitutions"):
        add("soccer", dataset, 3, "Optional current fields incomplete; xG/market/referee classifier columns unsupported or constant", "StatsBombselectedhistoricalevents; no unrestricted current3-league replacement verified",
            "80competition-seasonentries are an index, not80completeleagues; zero2026/27supported-league coverage proved", "Event games overlap some results but are a selected sample", "StatsBombIDs; provider-specific semantics, not API-Footballaliases",
            "Previous-game events potentially Class B under licence; target events/lineups not pregame facts", "SOC-002 /SOC-004 /VAL-001", "https://github.com/hudl/open-data")
    add("soccer", "Injuries /suspensions /pregame announcements", 4, "31rosters/932players and0cachedinjuries in audit; unknown is not healthy", "No verified free GitHub receipt-backed archive for all supported leagues",
        "Explicit unavailable status; future permitted source review before collection", "Postgame lineups do not prove pregame availability", soc_ids, "Prospective receipts required", "SOC-002 /VAL-001", "docs/SOCCER_DATA_DIAGNOSIS.md")
    add("soccer", "Unsupported player-prop field definitions", 4, "Stage1blocks clearances<-blocks, aerialwins<-allduels, passescompleted<-totalpasses, chancescreated<-keypasses", "No validated complete current-player source among researched repositories",
        "Zero invented markets; validate exact source field meanings and minutes before any restoration", "Similar names do not establish matching quantities", soc_ids, "No cross-provider silent coalesce or zero fill", "SOC-002", "docs/SCORPRED_STAGE1_REMEDIATION.md")
    add("soccer", "Current club-league tracking data", 3, "No matching tracked league histories", "SkillCornerA-Leaguesample; Metricasamples",
        "Zero verified EPL/Bundesliga/LaLiga tracking coverage; do not expand model scope", "Samples do not represent existing league universe", "Sample/anonymizedIDs", "Not valid population for current H/D/A validation", "SOC-004", "https://github.com/SkillCorner/opendata")
    add("soccer", "Durable pregame source/input/probability archive", 4, "12auditedsoccertrackingrows lack adequate bindings", "No external source supplies ScorPred's own frozen historical forecasts",
        "Archive future receipts and complete H/D/A vectors, league/season and model/policy hashes", "New rows alone cannot repair old prediction timestamps", soc_ids, "Prospective Class A; preserve untouched final period", "VAL-001", "docs/SCORPRED_MODEL_VALIDATION_AUDIT.md")
    (OUT / "gaps.json").write_text(json.dumps({"classifications": CLASSIFICATIONS, "net_new_records_measured": False,
        "reason": "No sports datasets imported; gains are testable scope hypotheses, not measured new rows", "gaps": rows}, indent=2), encoding="utf-8")
    write_csv("gaps.csv", rows)
    for sport in ("nfl", "nba", "soccer"):
        write_csv(f"{sport}_gaps.csv", [r for r in rows if r["sport"] == sport])
    print("Gap rows:", len(rows))


if __name__ == "__main__":
    main()
