"""Build a disposable index over existing local NBA records; no network or model fitting."""
import json
from services.nba_matchup_context import build_index

if __name__ == "__main__":
    print(json.dumps(build_index()))
