# Conditional NBA ending-2023 reconciliation pilot

**Status: blocked; not executed.** The [machine-readable plan](assets/rights_soccer/nba_2023_pilot.json) has all execution/collection/training/serving flags false and no rights approval. [The rights review](NBA_SOURCE_RIGHTS_RESOLUTION.md) did not establish a source-specific permission. This document authorizes no download, importer, model experiment, production fallback or deployment.

## Entry conditions and fixed scope

Prefer one SportsDataverse **ESPN** source family: schedules, team boxes and player boxes. Obtain authoritative permission for the exact asset versions and limited local reconciliation purpose, resolve producer/host license scope and underlying ESPN rights, and record attribution, adaptation, retention and publication restrictions. A separately approved NBA Stats source would additionally require an evidenced crosswalk; it is not an automatic fallback. Do not contact providers or use authentication/network workarounds under this plan alone.

Predeclare ending year **2023 = 2022–23**. The eligibility window is **2022-07-01 00:00 UTC inclusive through 2023-07-01 00:00 UTC exclusive**, further restricted to provider ending season 2023, completed games, NBA participants and the existing type-2/type-3 policy. This deliberately broad date window does not admit preseason, All-Star or exhibitions. Preserve play-in/playoff classification and enumerate differences instead of forcing another provider's competition totals into the reference denominator.

Freeze the existing **1,314 reference games: 1,230 type 2 and 84 type 3**. Their first/last retained tips are October 18, 2022 23:30 UTC and June 13, 2023 00:30 UTC. Reference identities and original monthly source hashes come from [the retained local index](assets/nba_registry/local_game_index.json). These counts are the existing eligibility universe, not proof of an independently complete season.

Only ending-2023 assets enter the later pilot. For example, the inspected ESPN team-box Parquet is asset **530363701**, `team_box_2023.parquet`, 136,082 bytes, updated August 26, 2026 06:14:10 UTC; its published digest is retained in [the manifest](assets/rights_soccer/nba_release_summary.json). Metadata may change: bind the reviewed version/digest and stop on drift. Do not select other seasons after looking at prediction performance.

## Identity, fields and deduplication

Require provider namespace, original game/competition ID, ending season, stage, status, timezone-aware tipoff and oriented team IDs. ESPN game/team/player IDs remain strings. NBA Stats game IDs remain ten-character strings with leading zeros; 161061xxxx team IDs and person IDs must not be treated as ESPN IDs. Date, both teams and competition may nominate a cross-source match; names alone or numeric resemblance cannot establish one. Every ambiguous mapping is quarantined.

Each team box requires a game/team key and declared definitions for points, FGM/FGA, 3PM/3PA, FTM/FTA, offensive/defensive/total rebounds, assists, steals, blocks, turnovers, fouls, minutes and overtime. Canonical requirements are proposals; the precise ESPN column/unit mapping remains an entry gate. Check finite nonnegative counts, made ≤ attempted, point arithmetic and agreement with each reconciled final score. Review team/dead-ball rebound conventions before enforcing an equality such as total = offensive + defensive. Do not substitute possession estimates or PPG for reported efficiency.

Each player row requires explicit game/team/player IDs, minutes and appearance/DNP status. Confirm whether the source supplies M:SS or decimal minutes and its precision. Null DNP minutes remain null; genuine zero is valid; a missing player ID is not repaired with a name guess. Check player minutes against **48 + 5 × overtime periods** and team participant-minute sums against **240 + 25 × overtime periods**. For second-resolution values, permit at most one second per participant of rounding discrepancy. Whole-minute-rounded values cannot pass exact-duration validation without a separately declared rule. Missing overtime, status or precision makes that validation unavailable, not successful.

Within a fixed provider version, natural keys are `(game, team)` for team boxes and `(game, team, player)` for player boxes. Retain every original row locator; collapse only identical payloads in the research canonical view. Conflicting identity, status, score, minute or count versions stop automatic acceptance. No averaging or newest-wins rule. Pick one permitted format per dataset/version; three formats are not three observations. Cross-provider evidence remains separately namespaced.

## Provenance and measurements

If entry is later authorized, stage raw bytes into a new isolated research version with the rights-decision ID, release/asset identity, published and locally computed digests, actual receipt time, parser/schema/stat-definition versions and original row locators. Record original effective/publication/revision times only where evidenced, otherwise null. Never backdate receipt to tipoff or call final-game lineups pregame announcements. Historical reconstruction remains **Class B at best**, with revision/publication-lag caveats; target-game boxes cannot enter a pregame prediction.

Publish these measurements before considering any experiment:

| Measure | Fixed denominator / acceptance |
|---|---|
| Reference identity matches | Matched / 1,314, by stage; proposed target ≥99% (**1,301 games**), with every unmatched/ambiguous reference enumerated |
| Source-only games | Full identifiers, stage/status and exclusion reason; net new usable games only after eligibility review |
| Paired team boxes | Coverage over 1,314 games / 2,628 expected sides; required-field denominators reported separately |
| Player identity/minutes | All explicit participant rows; separate DNP, unknown IDs, unknown minutes, format and duration-check counts |
| Duplicate/conflict rates | All raw keys and versions; **zero silent conflicts**, every quarantine retained |
| Result consistency | Both team points versus reference result; unresolved disagreement blocks acceptance |
| Provenance | Receipt/version coverage and lag assumptions; historical pregame proof remains a separate unavailable measure |
| Preservation | Original game/source/model/protocol hashes unchanged; no production selector or NFL outcome-store access |

These are **targets, not measured pilot results**. A 99% match rate does not prove completeness or authorize promotion; the remaining cases must still be accounted for. No accuracy, calibration or model-fitting objective belongs in this pilot.

## Rejection and rollback

Do not create a production pointer. If rights, schema, identity or field checks fail, mark the new research version rejected/quarantined and leave it unselected. Retain permitted evidence under its retention terms; never delete shared/raw historical data to undo the pilot. Restore only a research selector if one was separately introduced later, preserving the prior version and reason. Compare original hashes and stop on unexpected drift rather than rewriting originals. No destructive migration or model rollback is required because neither is changed by the proposed pilot.
