"""Bounded, metadata-only NBA release/schema inspection. No sports asset IO.

Only exact allowlisted GitHub API release listings and schema/source-code paths
can be requested. Asset URLs/HEAD requests, arbitrary URLs and retries are not
supported. No authentication credentials are read.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from runtime_paths import repo_root

REPOSITORY = "sportsdataverse/sportsdataverse-data"
TAGS = ("espn_nba_team_boxscores", "nba_stats_team_boxscores", "nba_stats_player_boxscores")
DOCUMENTS = (
    "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/README.md",
    "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/docs/datasets/team_boxscores.md",
    "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/docs/datasets/player_boxscores.md",
    "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/python/espn_nba_02_team_box_creation.py",
    "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/python/espn_nba_03_player_box_creation.py",
)
MAX_RESPONSE_BYTES = 2_000_000


def allowed_url(url: str, asset_release_ids: tuple[int, ...] = ()) -> bool:
    if url in DOCUMENTS:
        return True
    if url in {f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{tag}" for tag in TAGS}:
        return True
    return url in {f"https://api.github.com/repos/{REPOSITORY}/releases/{release_id}/assets?per_page=100&page={page}"
                   for release_id in asset_release_ids for page in range(1, 5)}


class DenyRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Metadata inspection does not follow redirects")


def fetch_metadata(url: str, *, release_ids: tuple[int, ...] = ()) -> dict:
    if not allowed_url(url, release_ids):
        raise ValueError("URL is outside the metadata allowlist; sports assets are prohibited")
    record = {"url": url, "observed_at": datetime.now(UTC).isoformat(), "sports_asset": False}
    request = Request(url, headers={"User-Agent": "ScorPred-metadata-rights-review/1",
                                  "Accept": "application/vnd.github+json" if urlsplit(url).netloc == "api.github.com" else "text/plain"})
    try:
        with build_opener(DenyRedirect()).open(request, timeout=25) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("Metadata response exceeds bounded size")
            content_type = response.headers.get("Content-Type", "")
            if "json" not in content_type and "text/" not in content_type:
                raise ValueError("Unexpected metadata content type")
            record.update(status=response.status, sha256=hashlib.sha256(body).hexdigest(),
                          text=body.decode("utf-8"), content_type=content_type,
                          rate_limit_remaining=response.headers.get("X-RateLimit-Remaining"))
    except HTTPError as exc:
        record.update(status=exc.code, error_type="HTTPError",
                      retry_after=exc.headers.get("Retry-After"),
                      rate_limit_remaining=exc.headers.get("X-RateLimit-Remaining"))
    return record


def inspect() -> dict:
    evidence, releases = [], []
    for tag in TAGS:
        result = fetch_metadata(f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{tag}")
        evidence.append(result)
        if result["status"] in (403, 429):
            break  # Honor host restrictions; no retry/auth workaround.
        if result["status"] != 200:
            continue
        release = json.loads(result["text"])
        assets, pages_complete = [], False
        # Always enumerate the paginated asset endpoint; an embedded list alone
        # does not prove that every release asset has been listed.
        for page in range(1, 5):
            url = f"https://api.github.com/repos/{REPOSITORY}/releases/{release['id']}/assets?per_page=100&page={page}"
            listing = fetch_metadata(url, release_ids=(release["id"],))
            evidence.append(listing)
            if listing["status"] != 200:
                break
            rows = json.loads(listing["text"])
            assets.extend({k: a.get(k) for k in ("id", "name", "size", "updated_at", "browser_download_url", "digest")}
                          for a in rows)
            if len(rows) < 100:
                pages_complete = True
                break
        releases.append({"repository": f"https://github.com/{REPOSITORY}", "tag": tag,
                         "release_id": release["id"], "release_created_at": release["created_at"],
                         "release_published_at": release.get("published_at"), "description": release.get("body"),
                         "observed_at": result["observed_at"], "evidence_url": result["url"],
                         "asset_count": len(assets), "asset_listing_complete": pages_complete,
                         "assets": assets, "payload_downloaded": False})
        if not pages_complete:
            break
    for url in DOCUMENTS:
        result = fetch_metadata(url)
        evidence.append(result)
        if result["status"] in (403, 429):
            break
    return {"schema_version": 1, "metadata_only": True, "sports_assets_downloaded": 0,
            "evidence": evidence, "releases": releases}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=repo_root() / "docs/assets/rights_soccer/nba_release_evidence.json")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Existing evidence is immutable; choose a new snapshot output")
    result = inspect()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps({"releases": len(result["releases"]), "requests": len(result["evidence"]),
                      "sports_assets_downloaded": 0, "statuses": [r["status"] for r in result["evidence"]]}))


if __name__ == "__main__":
    main()
