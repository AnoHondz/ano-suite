# NBA source permission matrix

Review date: October 22, 2026 UTC. The three permitted labels are exactly **Verified permitted for intended use**, **Restricted**, and **Unclear or unverified**. None currently qualifies for the first label. These are conservative ScorPred integration decisions based on the available evidence, not a blanket determination about every possible private statistical use.

| Provider | License text actually verified | Permission classification | What prevents asset use |
|---|---|---|---|
| `sportsdataverse/sportsdataverse-data` | MIT root LICENSE; host README data claim CC-BY-4.0; ESPN producer LICENSE.md explicitly CC-BY-4.0 for data/code/docs | **Unclear or unverified** | Host/producer scope discrepancy; underlying rights and purpose-specific permission unresolved |
| `sportsdataverse/hoopR-nba-stats-data` | Producer LICENSE.md explicitly CC-BY-4.0 for data/code/docs; root file is a stub | **Restricted** | Documented upstream restrictions, no intended-use exception; independent dataset manifests also absent |
| `shufinskiy/nba_data` | Apache-2.0 repository license | **Unclear or unverified** | NBA/data.nba/pbpstats rights chain and derivative applicability unresolved; repository grant alone is insufficient |

## Authoritative evidence and retained proof

The [machine-readable matrix](assets/nba_registry/permission_matrix.json) records URLs, scope, observation/check time, retained snapshot path and exact response SHA-256 for each license document. Original research snapshots are preserved, not replaced with a newer license assumption.

- SportsDataverse host [LICENSE](https://github.com/sportsdataverse/sportsdataverse-data/blob/main/LICENSE) and [README](https://github.com/sportsdataverse/sportsdataverse-data/blob/main/README.md), observed 01:52 UTC; [retained repository evidence](assets/github_data_research/repository_evidence.json).
- ESPN producer [LICENSE.md](https://github.com/sportsdataverse/hoopR-nba-data/blob/main/LICENSE.md) and NBA Stats producer [LICENSE.md](https://github.com/sportsdataverse/hoopR-nba-stats-data/blob/main/LICENSE.md), observed 01:53 UTC; [retained supplemental evidence](assets/github_data_research/supplemental_evidence.json).
- Shufinskiy [LICENSE](https://github.com/shufinskiy/nba_data/blob/main/LICENSE), observed 01:52 UTC; retained repository evidence above.
- [NBA terms, section 9](https://www.nba.com/termsofuse), rechecked in this task. Dataset-specific applicability/exception remains unresolved.
- [Disney terms, sections 2B(viii) and 2B(x)](https://disneytermsofuse.com/english/), rechecked in this task: ESPN is covered; automated extraction, dataset building and business use require express permission. The page states May 24, 2024 as its update date. Full terms text was not copied into the export.

CC-BY-4.0 requires applicable attribution and change notices; Apache-2.0 and MIT contain their own notice conditions. Verifying those repository texts does not verify that every underlying data right is licensed for ScorPred. No written permission, registration-based entitlement or purpose-specific exception was found. No provider was contacted on the user's behalf.

`Restricted` is the gate used for the NBA Stats producer's known upstream restriction without an applicable exception. `Unclear or unverified` records unresolved scope/rights chains for the host and multi-source archive; their underlying collection restrictions are recorded separately. None of these states licenses scraping or bypassing upstream conditions.

## Purpose-specific gate

The intended scope includes possible historical research ingestion, automated updates, training, prediction display and redistribution. No such asset purpose is approved here. Metadata inspection of already retained public documents/file listings is the only implemented capability.

An intended-use approval must identify the exact dataset/source version, rights holder or authoritative terms, permitted purposes, reviewer/date, required notices, redistribution/adaptation scope and remaining conditions. Simply changing a license label, repository accessibility or an environment flag does not qualify. A verified record still leaves all B1 flags disabled, and the hard metadata-only capability gate still denies assets.

No authentication bypass, proxy workaround or rate-limit retry was attempted. This implementation makes zero network requests, including asset HEAD requests and remote sidecar requests. Fresh legal-document checks used the browser separately; no sports dataset was requested. Future permitted metadata collection must honor host limits; no authorized numeric NBA/ESPN request quota has been established.

**Safe use today:** the disabled registry and its local metadata reports. **Blocked:** third-party sports downloads/imports, training, production prediction integration and redistribution. Resolve the ESPN schedule/player/team-box rights chain and missing manifest evidence first; then separately scope a completed-season research pilot. There is no deployment or automatic activation step.
