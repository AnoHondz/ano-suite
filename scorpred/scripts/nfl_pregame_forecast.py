"""Offline frozen NFL research registration, forecasting, inspection and replay."""
import argparse
import json

from services import nfl_frozen_models as frozen
from services import nfl_pregame_forecast as forecast


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--register", action="store_true", help="Reproduce and freeze the existing Elo/logistic study once")
    action.add_argument("--registration-dry-run", action="store_true", help="Verify original models and mapping without registration")
    action.add_argument("--status", action="store_true")
    action.add_argument("--forecasts", action="store_true", help="List immutable pregame predictions, never outcomes")
    action.add_argument("--dry-run", action="store_true", help="Validate due games and probabilities without archiving forecasts")
    action.add_argument("--execute", action="store_true", help="Archive genuine forecasts at actual UTC time")
    action.add_argument("--validate", action="store_true", help="Verify frozen artifacts, code, protocol and every saved probability")
    action.add_argument("--replay", action="store_true", help="Replay stored inputs without reading outcomes or creating forecasts")
    action.add_argument("--audit-note", help="Append an operator note; never alter a forecast")
    parser.add_argument("--game-id", help="Optional game identifier for an audit note")
    parser.add_argument("--database")
    parser.add_argument("--source-run", help="Existing frozen Phase 2.2 source directory for registration")
    args = parser.parse_args()
    try:
        if args.register or args.registration_dry_run:
            result = frozen.register(path=args.database, source=args.source_run, dry_run=args.registration_dry_run)
            if args.registration_dry_run:
                # Do not flood operator output with coefficients or historical scores.
                result = {"registration_dry_run": True, "study": {k: v for k, v in result["study"].items() if k != "source_bundle"},
                          "mapping_validation": result["models"]["logistic"]["mapping_validation"]}
            else:
                result = {k: v for k, v in result.items() if k != "source_bundle"}
        elif args.status:
            result = forecast.status(path=args.database)
        elif args.forecasts:
            result = {"forecasts": forecast.records(path=args.database, game_id=args.game_id), "outcomes_included": False}
        elif args.validate or args.replay:
            result = forecast.replay(path=args.database, verify_sources=args.validate)
        elif args.audit_note:
            forecast.audit("operator_note", path=args.database, game_id=args.game_id, payload={"note": args.audit_note})
            result = {"audit_note_appended": True, "previous_forecasts_changed": False}
        else:
            result = forecast.cycle(path=args.database, dry_run=args.dry_run)
        print(json.dumps(result, indent=2, allow_nan=False))
        if result.get("status") in {"degraded", "not_registered"}:
            raise SystemExit(2)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f"NFL research operation refused: {exc}\n")


if __name__ == "__main__":
    main()
