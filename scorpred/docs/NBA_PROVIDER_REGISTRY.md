# Disabled NBA provider registry

Implemented NBA roadmap B1 on October 22, 2026 UTC (October 21 Eastern). This is an offline research catalog and reconciliation tool. **All three providers remain disabled. No sports assets were downloaded, no games imported and no production integration activated.**

The versioned source of truth is [data/providers/nba_registry.json](../data/providers/nba_registry.json). [The exported registry](assets/nba_registry/provider_registry.json), [normalized candidate manifests](assets/nba_registry/candidate_manifests.json) and [permission matrix](NBA_SOURCE_PERMISSION_MATRIX.md) retain dataset names, formats, evidence dates, license scope, limitations and identity requirements.

| Candidate | Dataset scope | Available season evidence, ending years | Integration state |
|---|---|---|---|
| [sportsdataverse/sportsdataverse-data](https://github.com/sportsdataverse/sportsdataverse-data) | ESPN schedules, PBP, player/team boxes and game rosters | Actual retained asset names: schedules/player boxes **1994–2027**; PBP **2002–2027**. Team-box and game-roster manifests absent | Disabled, metadata only |
| [sportsdataverse/hoopR-nba-stats-data](https://github.com/sportsdataverse/hoopR-nba-stats-data) | NBA Stats traditional boxes, PBP, possessions, game lineups, player logs, rosters, schedules | Producer documentation claims traditional boxes **1997–2026**. Independent release manifests absent; other dataset ranges unknown | Disabled, metadata only |
| [shufinskiy/nba_data](https://github.com/shufinskiy/nba_data) | Seven PBP/shot/matchup archive variants, regular/playoff separately | Index-derived ending years differ by variant, **1997–2026** overall; see reconciliation | Disabled, metadata only |

The earlier roadmap summarized inspected ESPN scope as 2002–2027. B1's exact filename parser finds 1994–2001 schedule/player-box assets too. This corrects the metadata inventory; it does **not** establish usable games in those files. The original research evidence is preserved.

## Boundaries and behavior

[Registry validation](../services/nba_provider_registry.py) requires schema version 1 and `mode: metadata_only`. Each candidate explicitly has `enabled`, `collection_enabled`, `training_enabled` and `serving_enabled` set to `false`. Loading rejects missing/nonboolean/true flags, duplicate canonical repository URLs, duplicate provider IDs, duplicate dataset IDs and invalid permission states. Repository aliases differing only in case, trailing slash or `.git` cannot create duplicate candidates.

Public GitHub visibility is not a rights grant. A future `Verified permitted for intended use` record must identify reviewed purpose-specific approval evidence. That record still cannot enable asset access in B1: `asset_access_decision` always denies, and `require_asset_access` always raises. Environment variables do not override this boundary. There is no downloader, refresh job, migration, provider client or production fallback in this integration.

[The reconciliation service](../services/nba_historical_reconciliation.py) reads only the existing NBA archive, existing NBA public-response cache and already retained metadata snapshots. Snapshot SHA-256 mismatches fail closed. Absent manifests produce explicit unknown game coverage, with no network fallback. Master files, timestamp sidecars and alternative formats do not inflate season counts. Shufinskiy's README establishes start-year naming; index keys are converted to ending years, while WNBA and regular/playoff scopes remain distinct.

ESPN identifiers are the closest namespace fit, but no candidate game ID was inspected. NBA Stats game IDs must remain strings, preserving ten-character leading zeros; team IDs in the 161061xxxx namespace and person IDs are separate from ESPN IDs. No crosswalk has been verified. Name similarity alone is insufficient.

## Freshness and provenance

The retained ESPN metadata was observed around 01:53 UTC on October 22. Latest recognized season-asset updates are October 21: schedules **11:30:57 UTC**, player boxes **11:30:16 UTC**, PBP **11:29:41 UTC**. Individual asset IDs, URLs, sizes, timestamps and published digests are preserved as inert metadata. No URL is followed. Release creation in March 2023 is not data freshness.

NBA Stats' October 1 box publication is a documentation claim, not an independently checked release update. Recent producer commits and repository push dates do not verify the dataset's current operation. Shufinskiy's repository push was July 17; original dataset update dates/digests are unknown, and the README horizon trails some index entries.

Historical files are revised postgame data. None proves original pregame receipt, announced starters or injury availability. A later permitted reconstruction would be Class B at best and could use only eligible prior games under an explicit publication-lag policy. No target-game box score or final season aggregate becomes a pregame input.

## Reproduction and verification

From the repository root:

```powershell
.venv/Scripts/python.exe -m scripts.reconcile_nba_history
.venv/Scripts/python.exe -m pytest tests/test_nba_provider_registry.py -q
```

The CLI respects existing runtime cache paths. Defaults write only [the separate research export directory](assets/nba_registry/). It rejects output overlapping input/protected directories and refuses to overwrite an unowned directory. Existing atomic JSON writes are reused. There is no download, sync, activation or training switch. Identical inputs produce identical artifacts; source hashes are checked again before output.

Read [the historical inventory](NBA_HISTORICAL_RECONCILIATION.md) for counts and limitations. [Verification evidence](assets/nba_registry/verification.json) records test, replay and preservation results.

The next step is a dataset-specific rights resolution for the ESPN schedule/player/team-box family, plus inspection of missing **release metadata only**. A sports-asset pilot needs a separate permitted scope; this registry does not authorize it.
