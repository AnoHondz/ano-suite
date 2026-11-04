"""Download full ESPN NBA box scores (services.nba_history) for prop-model training.

Usage: python scripts/fetch_nba_history.py 2022 2023 2024 2025 2026 2027
       (ESPN season ids are ending years: 2022 is 2021-22)
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import nba_history  # noqa: E402


def main(seasons):
    for season in seasons:
        events = nba_history.season_events(season)
        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(lambda item: nba_history.fetch(*item), events.items()))
        counts = {key: outcomes.count(key) for key in ("cached", "fetched", "failed")}
        print(f"season {season}: {len(events)} events {counts}", flush=True)


if __name__ == "__main__":
    main([int(arg) for arg in sys.argv[1:]])
