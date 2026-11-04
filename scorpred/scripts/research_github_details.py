"""Bounded release/file-manifest research. No match/player/stat payload imports."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from scripts.research_github_sources import OUT, fetch, inspect


def main():
    target = OUT / "supplemental_evidence.json"
    if target.exists():
        raise ValueError("Existing evidence retained")
    urls = []
    for tag in ("pbp", "rosters", "depth_charts", "injuries", "pbp_participation", "nextgen_stats", "ftn_charting"):
        urls.append(f"https://api.github.com/repos/nflverse/nflverse-data/releases/tags/{tag}")
    for tag in ("espn_nba_schedules", "espn_nba_pbp", "espn_nba_player_boxscores"):
        urls.append(f"https://api.github.com/repos/sportsdataverse/sportsdataverse-data/releases/tags/{tag}")
    urls.extend([
        "https://api.github.com/repos/openfootball/football.json/git/trees/master?recursive=1",
        "https://api.github.com/repos/hudl/open-data/git/trees/master",
        "https://raw.githubusercontent.com/hudl/open-data/master/data/competitions.json",
        "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/LICENSE.md",
        "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-stats-data/main/LICENSE.md",
        "https://raw.githubusercontent.com/sportsdataverse/hoopR/main/DESCRIPTION",
        "https://raw.githubusercontent.com/nflverse/nflfastR/master/DESCRIPTION",
        "https://raw.githubusercontent.com/nflverse/.github/master/profile/README.md",
        "https://raw.githubusercontent.com/nflverse/nfldata/master/LICENSE.md",
        "https://raw.githubusercontent.com/shufinskiy/nba_data/main/list_data.txt",
        "https://raw.githubusercontent.com/sportsdataverse/hoopR/main/R/load_nba.R",
        "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-stats-data/main/docs/datasets/player_boxscores.md",
        "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-stats-data/main/docs/datasets/team_boxscores.md",
        "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-stats-data/main/docs/datasets/game_lineups.md",
        "https://raw.githubusercontent.com/swar/nba_api/master/docs/endpoints/boxscoreadvancedv3.md",
        "https://raw.githubusercontent.com/swar/nba_api/master/docs/endpoints/playergamelog.md",
        "https://raw.githubusercontent.com/openfootball/help/master/README.md",
    ])
    with ThreadPoolExecutor(max_workers=2) as pool:
        evidence = list(pool.map(fetch, urls))
    extra = inspect("greerreNFL/nfelo", ("nfl", "nfelo"))
    target.write_text(json.dumps({"observed_at": datetime.now(UTC).isoformat(),
        "sports_payloads_downloaded": 0, "competition_index_only": True,
        "evidence": evidence, "additional_repository": extra}, indent=2), encoding="utf-8")
    print(json.dumps([{"url": r["url"], "status": r.get("status"), "remaining": r.get("rate_limit_remaining")} for r in evidence], indent=2))


if __name__ == "__main__":
    main()
