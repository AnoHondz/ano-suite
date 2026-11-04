"""Email today's props using the running local app and configured SMTP sender."""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import requests
    from services import daily_digest, props_email
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", choices=("all", *props_email.SPORTS), default="all")
    parser.add_argument("--date", default=datetime.now(daily_digest.ZONE).date().isoformat())
    parser.add_argument("--recipient", default=os.getenv("SMTP_USER", ""))
    args = parser.parse_args()
    if not daily_digest.re.fullmatch(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+", args.recipient):
        parser.error("Supply one --recipient email address.")
    sports = list(props_email.SPORTS) if args.sport == "all" else [args.sport]

    def api_get(path, params):
        response = requests.get("http://127.0.0.1:5001" + path, params=params, timeout=120)
        return response.json(), response.status_code

    def props_for(sport, card, day):
        return api_get(f'/api/{sport}/matches/{card["matchId"]}/props', {"date": day})

    # Synchronous command; shares the same persistent duplicate journal as UI.
    job = props_email.start(args.recipient.lower(), args.recipient, args.date, sports,
        lambda: props_email.collect(args.date, sports, api_get, props_for),
        submit=lambda fn, *values: fn(*values))
    print(json.dumps(job, indent=2))
    return 0 if job["state"] == "sent" else 1


if __name__ == "__main__":
    raise SystemExit(main())
