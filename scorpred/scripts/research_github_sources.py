"""Phase 3 research: bounded public repository metadata/docs, never sports rows.

No application imports, database writes, dataset imports, or upstream scraping.
GitHub API limits are respected; failed verifications are retained as failures.
"""
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/assets/github_data_research"
REPOS = {
    "nflverse/nflverse-data": ("nfl", ".github/workflows"),
    "nflverse/nflreadpy": ("nfl", "src/nflreadpy"),
    "nflverse/nflfastR": ("nfl", "R"),
    "nflverse/nflverse-rosters": ("nfl", "R"),
    "nflverse/nfldata": ("nfl", "R"),
    "nflverse/nflverse-players": ("nfl", "R"),
    "nfeloapp/nfelo": ("nfl", "nfelo"),
    "sportsdataverse/hoopR": ("nba", "R"),
    "sportsdataverse/sportsdataverse-data": ("nba", ".github/workflows"),
    "sportsdataverse/hoopR-data": ("nba", ".github/workflows"),
    "sportsdataverse/hoopR-nba-data": ("nba", "python"),
    "sportsdataverse/hoopR-nba-stats-data": ("nba", ".github/workflows"),
    "swar/nba_api": ("nba", "src/nba_api"),
    "dblackrun/pbpstats": ("nba", "pbpstats"),
    "shufinskiy/nba_data": ("nba", "load_nba_data.R"),
    "statsbomb/open-data": ("soccer", "doc"),
    "openfootball/football.json": ("soccer", None),
    "openfootball/england": ("soccer", None),
    "footballcsv/england": ("soccer", None),
    "footballcsv/deutschland": ("soccer", None),
    "footballcsv/espana": ("soccer", None),
    "andrewbaisden/football-data": ("soccer", None),
    "metrica-sports/sample-data": ("soccer", None),
}


def fetch(url, *, limit=1_500_000):
    result = {"url": url, "observed_at": datetime.now(UTC).isoformat()}
    try:
        request = Request(url, headers={"User-Agent": "ScorPred-public-source-research", "Accept": "application/vnd.github+json" if "api.github.com" in url else "*/*"})
        with urlopen(request, timeout=20) as response:
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("Research response exceeds bound; no partial evidence accepted")
            result.update(status=response.status, resolved_url=response.url,
                          etag=response.headers.get("ETag"), last_modified=response.headers.get("Last-Modified"),
                          rate_limit_remaining=response.headers.get("X-RateLimit-Remaining"),
                          sha256=hashlib.sha256(raw).hexdigest(), text=raw.decode("utf-8"))
    except (HTTPError, URLError, OSError, ValueError) as exc:
        result.update(status=getattr(exc, "code", None), error_type=type(exc).__name__)
    return result


def inspect(repo, spec):
    sport, code_path = spec
    meta = fetch("https://api.github.com/repos/" + repo)
    result = {"requested_repository": repo, "sport": sport, "metadata": meta}
    if meta.get("status") != 200:
        return result
    value = json.loads(meta["text"])
    canonical, branch = value["full_name"], value["default_branch"]
    # One bounded commit-path query separates code maintenance from data churn.
    suffix = "?per_page=1" + ("&path=" + code_path if code_path else "")
    commit = fetch("https://api.github.com/repos/" + canonical + "/commits" + suffix)
    result.update(canonical_repository=canonical, code_path_checked=code_path, maintenance_commit=commit)
    base = "https://raw.githubusercontent.com/" + canonical + "/" + branch + "/"
    docs = []
    for names in (("README.md", "README.rst", "README.Rmd"), ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENSE.pdf")):
        for name in names:
            if name.endswith(".pdf"):
                # A licence PDF needs separate inspection; never mis-decode it.
                continue
            doc = fetch(base + name)
            docs.append(doc)
            if doc.get("status") == 200:
                break
    result["documents"] = docs
    return result


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "repository_evidence.json"
    if target.exists():
        raise ValueError("Existing research evidence retained; use a new output run")
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda item: inspect(*item), REPOS.items()))
    target.write_text(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "sports_payloads_downloaded": 0,
        "seconds": round(time.monotonic() - started, 2), "repositories": results}, indent=2), encoding="utf-8")
    print(json.dumps([{"repo": r["requested_repository"], "status": r["metadata"].get("status"),
        "canonical": r.get("canonical_repository"), "docs": [d["url"].rsplit("/", 1)[-1] for d in r.get("documents", []) if d.get("status") == 200]}
        for r in results], indent=2))


if __name__ == "__main__":
    main()
