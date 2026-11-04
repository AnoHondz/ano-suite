"""Explicitly capture an immutable research preview before tipoff; no outcome collection."""
import argparse
from datetime import UTC, datetime
import json
from urllib.request import urlopen

from services import nba_early_season as research
from utils.atomic import atomic_write_json, file_transaction


def capture(context, artifact, path, *, now=None):
    now = now or datetime.now(UTC)
    with file_transaction(path):
        if path.exists():
            return {"game_id": context["game"]["id"], "status": "existing_snapshot_preserved"}
        prediction = research.forecast(context, artifact, now=now)
        if context["game"].get("result") is not None:
            raise ValueError("A prospective snapshot must never include this game's outcome.")
        snapshot = {"version": research.VERSION, "captured_at": now.isoformat(), "role": "research_only", "outcomes": "not_collected",
                    "artifact_sha256": research.digest(artifact), "protocol_sha256": artifact["protocol_sha256"],
                    "prediction": prediction, "inputs": context,
                    "provenance": "Inputs were actually captured at captured_at before this game. Older archives' original receipt timestamps remain unverified."}
        atomic_write_json(path, snapshot)
        return {"game_id": context["game"]["id"], "captured_at": now.isoformat(), "home_probability": prediction["home_probability"], "status": "research_snapshot_saved"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-id", action="append", required=True)
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    # This command is the explicit research opt-in. It does not activate any web feature.
    artifact = research.load_artifact()
    for identifier in args.game_id:
        if not identifier.isdigit() or len(identifier) > 12:
            raise ValueError("Use a valid NBA game identifier.")
        datetime.strptime(args.date, "%Y-%m-%d")
        path = research.DIRECTORY / "prospective" / f"{identifier}-{artifact['protocol_sha256'][:12]}.json"
        if path.exists():
            print(json.dumps({"game_id": identifier, "status": "existing_snapshot_preserved"}))
            continue
        # The decision endpoint has schedule metadata, never this game's final score.
        with urlopen(f"http://127.0.0.1:5001/api/matches/{identifier}?sport=nba&date={args.date}", timeout=20) as response:
            decision = json.load(response)["decision"]
        if datetime.fromisoformat(decision["matchDate"]) <= datetime.now(UTC):
            print(json.dumps({"game_id": identifier, "status": "skipped_after_tipoff"}))
            continue
        with urlopen(f"http://127.0.0.1:5001/api/nba/matches/{identifier}/context?date={args.date}", timeout=20) as response:
            context = json.load(response)["result"]
        print(json.dumps(capture(context, artifact, path)))


if __name__ == "__main__":
    main()
