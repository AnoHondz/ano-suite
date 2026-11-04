# Soccer lineage sidecar and research views v1

Implemented October 22, 2026. **7,888 historical fixtures are represented without changing any original row.** The separate legacy research view represents all 2,918 observations, including 319 duplicate groups, with 2,599 justified analytical representatives. Production queries, predictions, trained models, frozen NFL artifacts and the disabled NBA registry are unchanged.

## Versions and evidence boundary

The [offline builder](../scripts/build_soccer_research.py) creates [the immutable v1 bundle](assets/soccer_foundation/v1/manifest.json). Its versions are `soccer-lineage-v1`, `soccer-stats-v1`, and `soccer-research-v1`. Input hashes bind the existing diagnostics, accepted raw CSVs, original historical source and retained Stage 1 preparation. No source fetch, model fitting or database migration occurs.

[The sidecar](assets/soccer_foundation/v1/lineage_sidecar.json) includes these fields per fixture:

| Field | Meaning |
|---|---|
| `original_fixture_identifier` | Verified **internal** file/SHA-256/physical CSV line/row fingerprint; not a recovered provider event ID |
| `canonical_fixture_identifier` | Deterministic local reference ID for a unique original date/oriented-team identity, scoped to this exact source version |
| `status`, `matching_method`, `confidence_classification` | Candidate, ambiguous or unresolved decision and exact matching rule; no invented probability/confidence score |
| `league`, `season` | Separate nullable verified-original values and evidence-backed candidate values/namespaces |
| `source_identifier`, `import_batch` | Internal source identifier; original provider/import batch remain null where unproven |
| `fixture_metadata_candidates`, `raw_csv_candidates` | Original candidate provider IDs or raw row locators, explicit competition/season evidence and source fingerprints |
| `original_observation_reference`, `historical_availability` | Null original receipt and explicit unverified historical availability |
| `internal_derived_feature_csv_lines` | Retained local relationship to the existing derived feature file |
| `sidecar_version`, `audit_timestamp` | Contract version and actual audit time; the audit time is never an event observation time |

Canonical IDs are local analytical identifiers, not universal cross-provider identities. Original provider IDs stay in their source namespace, preserving string form and leading zeros. An internal verified row locator does not make its provider lineage verified.

## Reconciled population and mapping decisions

Before mapping, the builder checks that physical rows 2–7,889 occur exactly once, identities match the unchanged CSV, classification totals match the diagnostic manifest and the fixed **895 + 291 + 6,702 = 7,888** audit population. Duplicate row locators, omitted rows, mismatched identities, changed input hashes or unsupported original-provenance claims fail closed before writing.

| Original-provider lineage state | Fixtures |
|---|---:|
| Verified | **0** |
| Candidate: unique source fixture metadata | **895** |
| Candidate: local raw CSV only | **291** |
| Ambiguous in this retained population | **0** |
| Explicitly unresolved | **6,702** |
| **Total** | **7,888** |

The 895 source-metadata candidates retain exact date, oriented names, both scores, one source event ID and explicit source competition, without conflicting cached versions. They remain candidates: neither a later cache receipt nor a Git commit proves the original import. Source hashes are pinned; missing hashes or conflicting identities produce ambiguity in tested paths. The 291 raw-only candidates are checked against their actual accepted raw-file row/date/names/scores and explicit division. Their local competition and season labels are candidates for the classifier, not verified importer provenance. No name-only, fuzzy-name or calendar-season assignment occurs.

All 7,888 rows have verified internal source locators; all 7,586 retained derived feature rows remain linked through the existing diagnostic references. None of the 7,888 receives a fabricated import timestamp, original provider ID, original batch or Class A pregame designation. The 6,702 unresolved fixtures remain explicit records.

## Duplicate observations and canonical analytical counting

[The duplicate view](assets/soccer_foundation/v1/duplicate_view.json) retains both original members of every two-row group, including physical row numbers, row/file fingerprints, declared season/source identifier and group ID. All **319 groups** have identical observation payloads and conflicting `Season` labels; originals remain separate.

The deterministic selection rule is **the unique existing Stage 1 accepted row whose entire observation payload equals the legacy observations**, normalizing only date representation and excluding metadata fields `Season`/`League`. It does not silently choose a source label, smallest score or latest timestamp. Every representative is bound to the unchanged Stage 1 file hash and row fingerprint. Conflicting observations, absent accepted rows or nonunique accepted rows have no representative and enter quarantine. The retained population has zero such unresolved selections.

Consequently the research view counts **2,599 fixtures once**, rather than 2,918 legacy rows. It retains all original observations and all 319 alias groups; no destructive deduplication or source deletion occurs. [Canonical fixture collections](assets/soccer_foundation/v1/canonical_fixtures.json) expose the 7,888-fixture historical reference and 2,599-fixture E0 reference separately, without adding their overlapping games into a false combined total.

## Explicit research access

```python
from services.soccer_research_views import ResearchViews

# The default raises PermissionError before reading files.
views = ResearchViews.open("docs/assets/soccer_foundation/v1", enabled=True)
views.lineage("candidate")
views.canonical_fixtures("legacy_e0")
views.duplicate_groups()
views.corrected_statistics()
views.quality()
```

This is offline research opt-in. No Flask route, model trainer, production selector or frontend imports the new modules. Bundle versions, filenames, disabled production/training flags and artifact fingerprints are validated on open. Returned values are copies. Unsupported versions or tampering fail closed. Views do not train models or grant data rights.

Reproduction uses the fixed actual audit timestamp from the bundle, preserving deterministic bytes:

```powershell
.venv/Scripts/python.exe -m scripts.build_soccer_research --audit-timestamp 2026-10-22T13:24:25.580469+00:00
```

An identical replay leaves existing bytes unchanged. Changed evidence/output requires a new version/directory; the builder refuses to overwrite v1 or write into protected source/cache directories.

## Preservation, verification and limits

[Verification evidence](assets/soccer_foundation/verification/verification.json) records before/after counts and ordered row fingerprints for 13 CSVs, hashes for 416 protected original files, original model/frozen forecast/NBA registry preservation, byte-identical research replay, backend/frontend checks and six fixed-input production prediction outputs. The prediction comparison uses the existing classifier on three saved feature vectors and primary rule engine on three fixed contexts, with fixed clock and isolated empty tracking. It does not claim live predictions across changing external inputs are constant.

The first comparison incorrectly treated integer label keys and their JSON string representation as unequal. Read-only inspection showed identical serialized outputs and unchanged source/artifact hashes. The probe was corrected, a regression test added, and the six predictions compare unchanged. No production discrepancy was found or bypassed.

Remaining limits: original per-row provider provenance and pregame availability remain unverified; source rights are not changed; candidate mappings require human evidence review; model-specific train/test membership is not inferred from feature-file links. [Statistic contracts](SOCCER_STAT_CONTRACT_REPAIRS.md) are corrected only in the disabled research path. The next phase is reviewed candidate crosswalk validation and an explicitly approved compatibility assessment for corrected input semantics, preserving the existing model and held outcomes.
