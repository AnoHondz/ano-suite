"""Render reviewed source/gap exports into the three sport reports."""
import json
import re

from scripts.research_github_sources import OUT, ROOT


def readable_markdown(text):
    """Space compact prose counts without changing IDs, links or code blocks."""
    lines, code = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            code = not code
        if not code:
            parts = re.split(r"(`[^`\n]+`|\[[^\]]*\]\([^)]+\)|https?://[^\s)>]+)", line)
            for i in range(0, len(parts), 2):
                part = re.sub(r"(?<=[a-z])(?=\d)", " ", parts[i])
                part = re.sub(r"(?<=\d)(?=[a-z])(?!xxxx)", " ", part)
                part = re.sub(r"(?<=\d)(?=[A-Z]{2,}(?:[^A-Za-z]|$))", " ", part)
                parts[i] = part
            line = "".join(parts)
        lines.append(line)
    return "\n".join(lines) + "\n"


def main():
    sources = json.loads((OUT / "sources.json").read_text(encoding="utf-8"))["sources"]
    gaps = json.loads((OUT / "gaps.json").read_text(encoding="utf-8"))["gaps"]
    for sport in ("nfl", "nba", "soccer"):
        target = ROOT / f"docs/{sport.upper()}_GITHUB_DATA_SOURCES.md"
        text = target.read_text(encoding="utf-8")
        if "<!-- GAP_MATRIX -->" in text:
            table = ["| Dataset | Classification | Existing scope / proposed measurable change |", "|---|---|---|"]
            for row in gaps:
                if row["sport"] == sport:
                    table.append(f"| {row['dataset']} | {row['classification']} | {row['existing']}; {row['measurable_gain_or_test']} |")
            text = text.replace("<!-- GAP_MATRIX -->", "\n".join(table))
        # Replace only the generated section; authored conclusions are retained.
        marker = "<!-- SOURCE_PROFILES -->"
        authored = text.split(marker)[0] + marker
        cards = []
        for row in sources:
            if row["sport"] != sport:
                continue
            update = row["latest_verified_dataset_update"]
            code = row["latest_verified_code_maintenance"]
            date = update.get("at") or "Not independently verified"
            code_date = code.get("at") or "Not separately verified"
            refs = list(dict.fromkeys(row["evidence_urls"]))
            evidence = ", ".join(f"[evidence{i + 1}]({url})" for i, url in enumerate(refs))
            cards.append(f"""
### [{row['repository']}]({row['url']}) — {row['disposition']}

| Assessment | Finding |
|---|---|
| Maintainer /sport /role | {row['maintainer']}; {row['sport']}; {row['kind']} |
| Canonical location | {row['canonical_url']} |
| Datasets | {row['datasets']} |
| Seasons /competitions | {row['seasons_and_competitions']} |
| Latest verified dataset update | {date}; {update['scope']} |
| Latest verified code maintenance | {code_date}; {code['scope']} |
| Repository push /archival status | {row['repository_pushed_at'] or 'Not independently verified'}; archived={row['archived']} (not dataset freshness) |
| Formats /access | {row['formats']}; {row['access_method']} |
| Licence /permitted use | {row['license_and_permitted_use']} |
| Automation restrictions | {row['automation_restrictions']} |
| Rate limits | {row['rate_limits']} |
| Historical availability | {row['historical_availability']} |
| Pregame timestamp reliability | {row['pregame_timestamp_reliability']} |
| Missing-data limitations | {row['missing_data_limitations']} |
| Existing integration /compatibility | {row['existing_integration']}; {row['compatibility']} |
| IDs /overlap | {row['identifiers']}; {row['overlap']} |
| Integration complexity | {row['integration_complexity']} |
| Expected improvement | {row['expected_improvement']} |
| Score /bounded suitability | {row['score_100']}/100; bounded data suitability={row['verified_suitable_bounded_data_source']}; no new production activation |

Verification: {evidence}. Exact retained response snapshots and hashes are linked in [the source inventory](assets/github_data_research/sources.json). Unknown values are explicit, not inferred from stars or a repository push.
""")
        target.write_text(readable_markdown(authored + "\n" + "\n".join(cards)), encoding="utf-8")
    for name in ("SCORPRED_GITHUB_DATA_RESEARCH.md", "SCORPRED_DATA_INTEGRATION_ARCHITECTURE.md", "SCORPRED_GITHUB_INTEGRATION_ROADMAP.md"):
        path = ROOT / "docs" / name
        path.write_text(readable_markdown(path.read_text(encoding="utf-8")), encoding="utf-8")
    print("Sport source profiles and gap matrices rendered")


if __name__ == "__main__":
    main()
