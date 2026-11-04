"""Configure, preview or inspect the opt-in daily sports email. No secret arguments."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from services import daily_digest
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("configure", "status", "preview", "send"))
    parser.add_argument("--recipient")
    parser.add_argument("--time", default="13:00", help="Eastern time in HH:MM")
    parser.add_argument("--disabled", action="store_true")
    args = parser.parse_args()
    if args.action == "configure":
        if not args.recipient:
            parser.error("--recipient is required")
        daily_digest.configure(args.recipient, args.time, enabled=not args.disabled)
        print(json.dumps(daily_digest.status(), indent=2))
        return
    if args.action == "status":
        import requests
        try:
            runtime = requests.get("http://127.0.0.1:5001/api/local-runtime", timeout=3).json()
            live = runtime.get("daily_digest") if runtime.get("app") == "ScorPred" and Path(runtime.get("root", "")).resolve() == ROOT else None
        except (requests.RequestException, ValueError):
            live = None
        print(json.dumps(live or daily_digest.status(), indent=2))
        return
    # Use the already running, singleton local app; no research/model flags or
    # duplicate background services are started by this maintenance command.
    import requests
    def api_get(path, params):
        response = requests.get("http://127.0.0.1:5001" + path,
                                params={k: v for k, v in params.items() if v is not None}, timeout=45)
        return response.json(), response.status_code
    def props_for(sport, card, day):
        return api_get(f'/api/{sport}/matches/{card["matchId"]}/props', {"date": day})
    result = daily_digest.run(None, preview=args.action == "preview",
        collector=lambda: daily_digest.collect(api_get, props_for))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
