"""Assemble editorial issue register and measured summary from audit exports.

No collection, fitting, application imports or database outcome reads occur here.
The register records observed mechanisms separately from unmeasured causal harm.
"""
import hashlib
from datetime import UTC, datetime

from scripts.audit_scorp import DEFAULT_OUTPUT, ROOT, fingerprint, load, write


def main():
    out = DEFAULT_OUTPUT
    issues = []

    def issue(identifier, sport, component, status, description, evidence, severity,
              impact, cause, fix, resource, complexity, acceptance, confidence="High"):
        issues.append({"rank": len(issues) + 1, "id": identifier, "sport": sport,
            "component": component, "diagnosis_status": status, "description": description,
            "evidence_and_measurement": evidence, "severity": severity,
            "prediction_impact": impact, "established_root_cause": cause,
            "diagnosis_confidence": confidence, "recommended_fix": fix,
            "free_source_or_existing_resource": resource,
            "implementation_complexity": complexity, "acceptance_test": acceptance})

    issue("NFL-001", "NFL", "Generic history/snapshot and legacy training readers",
          "Confirmed future access risk; no actual breach established",
          "Reserved-result exclusion is not shared by all readers, including collector full-feature snapshots",
          "109 future held games; FeatureBuilder/prepare_snapshots and legacy nfl_features/nfl_model/scoring trainer lack equivalent guard",
          "P0 before November 26", "Future holdout results could enter unrelated histories before permitted review",
          "Seal enforcement is local to registered paths, not a shared outcome boundary",
          "Design pinned-v1 isolation/version transition, then guard all applicable readers and training entries",
          "Existing sealed protocol, frozen study fingerprints and synthetic fixtures", "High: 2-4 days design and guarded implementation",
          "Synthetic held-result sentinels never reach any aggregate; legacy trainers fail closed; v1 independent replay/hashes unchanged")
    issue("OPS-001", "All", "Refresh workers and source networking", "Confirmed degradation; complete cause unknown",
          "Operational refresh is not continuously healthy", "02:56 UTC health503; NBA524min and soccer/ML539min since success; NFL PBP ConnectionError/backoff; PBP HEAD200",
          "P1", "Stale or unavailable pregame inputs, suppressed cards or unknown availability",
          "ConnectionError recorded; collector log includes GitHub DNS failure; whole failure duration/cause unproved",
          "Trace failures and recovery, preserve original ages, verify successful scheduled receipt after failure",
          "Existing logs, source health and receipt ledger", "Medium: 1-2 days",
          "Real scheduled recovery with correct original receipt times, stale suppression and one job owner")
    issue("VAL-001", "NBA/Soccer", "Pregame prediction tracking", "Confirmed evidence gap",
          "Current-method prospective quality cannot be established", "47 rows:35NBA/12soccer,28completed/19pending; missing kickoff/model/source binding",
          "P1", "Winner accuracy and live-blend calibration are Unknown",
          "Ordinary tracking lacks an immutable model/input/receipt contract",
          "Archive full pregame probability vectors and bound receipts/model/policy identities",
          "Existing NFL archive design and NBA/soccer source adapters", "Medium: 2-4 days",
          "Every scored record proves archive and input receive before kickoff and replays without collection")
    issue("SOC-001", "Soccer", "Raw season files and legacy processed concat", "Confirmed defect",
          "Mislabeled duplicate season propagates into legacy derived data", "E0_2018 and E0_2025 byte-identical319rows; processed2918/2599unique,319excess10.93%",
          "P1", "Legacy training could overweight 2025; current clean classifier unaffected and score trainer excludes it",
          "Raw year label incorrect and legacy concatenator lacks season/duplicate validation",
          "Preserve originals, quarantine in new manifest and create validated deduplicated derived version",
          "Existing CSV hashes and permitted public results cross-check", "Low/Medium: 1 day",
          "Date-season contracts pass and derived fixture duplicates zero; current artifact hashes unchanged")
    issue("SOC-002", "Soccer", "Player-prop statistic mappings", "Confirmed semantic mismatch /unsupported definition",
          "Stat names do not reliably match source quantities", "clearances->tackles.blocks; aerial_duels_won->duels.won; passes_completed->passes.total; chances_created duplicates passes.key",
          "P1", "Markets may describe another quantity; performance effect unmeasured",
          "Source-stat aliases lack validated semantic contracts", "Map only verified definitions and mark unsupported data null",
          "Existing fixture-stat adapter and original permitted provider definitions", "Medium: 1-2 days",
          "Known fixture boxscore maps exact quantities and unsupported markets stay unavailable")
    issue("NBA-001", "NBA", "Props numeric player interface /overview", "Confirmed availability/default/stale limitations",
          "Real numeric-ID logs are empty; props season default and stale semantics are inconsistent",
          "Pure real-ID interfaces returnedNone/[]; nba_routes props default2024; separate overview cache fallback unbounded",
          "P1", "Real player props unavailable or stale; no demonstrated synthetic data contamination",
          "Legacy mock-only player functions wired to numeric route, hardcoded props default, uncapped fallback",
          "Use existing real-ID provider adapter with explicit timestamps, selected season and stale ceiling",
          "Existing public NBA overview/schedule/summary adapters", "Medium: 2-3 days",
          "Real ID samples link to verified games/season; expired source never gets fresh provenance")
    issue("SOC-003", "Soccer", "Route feature context", "Original defect; corrected on disk outside audit, runtime validation pending",
          "Original five-match context collapsed ten-match and trend fields; rest used wall date",
          "Original sensitivity39/1518argmax changes,mean1.26pp,max12.88pp; app.py/scormastermind.py changed18:20UTC to ML10/target-date",
          "P1", "Original probability semantics differed; aggregate sensitivity mixed; current loaded behavior unknown",
          "Original route truncated input before longer-window ML features and omitted target date",
          "Verify current-source route parity and separately bind loaded version without retraining",
          "Existing offline builder, current corrected context and historical replay", "Low/Medium: 1 day validation",
          "Each route's feature vector equals offline reference at same cutoff/target; both rule5 and ML10 verified")
    issue("SOC-004", "Soccer", "Historical classifier lineage", "Confirmed coverage evidence gap",
          "Per-league training suitability cannot be measured", "7888rows lack league/season/source receipts; zero2026-27trainingrows; lastfixtureApril19",
          "P1", "League-specific quality and current-season representativeness Unknown",
          "Flat input schema omits provenance keys", "Version identities and source manifests; do not infer leagues from names",
          "Permitted free results sources /existing provider IDs", "Medium: 2-4 days",
          "Every new derived row has evidenced league/season/provider identity; per-league denominators reconcile")
    issue("OPS-002", "All", "Pregame freshness compatibility gate", "Confirmed behavior; live harm unproved",
          "Absent freshness metadata accepted for future games", "Pureprobe missingmetadata->allowed, explicitunknown->rejected, postkickoff->rejected",
          "P1", "Unknown provenance can bypass this compatibility check",
          "Legacy absence is allowed rather than explicitly unavailable", "Require explicit freshness evidence on prediction contracts with controlled migration",
          "Existing lifecycle tests and service metadata", "Medium: 1-2 days",
          "No metadata and expired metadata fail closed; observed-before-kickoff fresh source accepted")
    issue("NFL-002", "NFL", "Auxiliary feed ownership", "Confirmed coverage/freshness gap",
          "Auxiliary feeds are not refreshed with the main worker cadence", "176teamrows/88games vs93initialpastPBP; advancedteams80games; mostreceiptsOctober18 (~52h at finalcheck)",
          "P2", "Current advanced analysis incomplete; frozen three-input predictions do not use these feeds",
          "Observed collector cadence covers schedules/PBP/snapshots, not all auxiliary feeds",
          "Assign auxiliary refresh ownership, rate limits and publication-aware receipt freshness",
          "Existing nflverse loaders and official availability schedule", "Medium: 1-3 days",
          "Eligible fixture coverage reconciles; each feed has owned actual successful/failed receipt")
    issue("VAL-002", "Soccer", "Validation profile lineage", "Confirmed mismatched evidence",
          "Older walk-forward profile is not current artifact validation", "April47feature/3790test report versus October60featureartifact and1518currenttest",
          "P2", "Current selector/blend cannot inherit old metrics", "Report/profile is not bound to current artifact and policy identity",
          "Bind schema/model/policy/report hashes and evaluate actual existing blend",
          "Existing frozen artifacts and chronological evaluation code", "Medium: 2 days",
          "Mismatched report rejected and current-method paired baseline results reproducible")
    issue("SOC-005", "Soccer", "Optional feature columns", "Confirmed unsupported signal",
          "13 optional columns have no varying historical training signal", "5market+5xG+3referee constant/all-imputed over7586eligible rows",
          "P2", "Feature count overstates available information; model impact not isolated",
          "Schema extended beyond source coverage", "Disclose coverage, preserve schema until versioned training decision",
          "Existing feature manifest and missingness export", "Low: 0.5 day",
          "Schema availability report distinguishes real zero, missing and constant; artifact hash unchanged")
    issue("SOC-006", "Soccer", "Elo identity/coverage", "Confirmed fallback; spelling cause unproved",
          "Seven current clubs lack exact Elo history", "51/58matched87.93%;7neutral12.07%;zeroadditionalnormalized matches",
          "P2", "Neutral prior can reduce team-specific discrimination; harm unmeasured",
          "Historical Elo state lacks these exact club names; promotion versus identity cause unresolved",
          "Build explicit provider-ID crosswalk and document new-team prior",
          "Existing team directory/Elo state and permitted results", "Medium: 1 day",
          "All58identities resolved or explicitly classified new; no guessed name merges")
    issue("SOC-007", "Soccer", "Hard draw selection", "Confirmed limitation; not proven probability defect",
          "Classifier never chooses draw by argmax", "0/1518drawpredictions,383actualdraws; meandrawp24.88% vs25.23%actual; drawECE0.015201",
          "P2", "Zero draw recall; pooled draw probabilities need separate assessment",
          "Estimated draw probability never largest on this window; causal model deficiency unproved",
          "Evaluate classwise discrimination/calibration and decision thresholds prospectively before tuning",
          "Existing classifier replay", "Medium: 1-2 days analysis plus future data",
          "Perclass/bin counts and fixed decision policy evaluated on untouched chronological/prospective period")
    issue("NBA-002", "NBA", "Advanced metric semantics", "Confirmed proxy naming limitation",
          "net_rtg is PPG difference, not verified per-possession efficiency", "Derived return lacks verified pace/off_rtg/def_rtg; unused25featureMLschema is not evidence",
          "P2", "Consumers may interpret proxy as advanced statistic; predictive harm unmeasured",
          "Proxy naming and schema exceed demonstrated source semantics",
          "Name/document proxy honestly or compute only from verified possession inputs",
          "Existing boxscores and official NBA metric definition", "Low/Medium: 1 day",
          "Units/formula/source fields explicit; no advanced label backed by unverified proxy")
    issue("NBA-003", "NBA", "Injury missingness", "Confirmed empty-data behavior; live accuracy harm unproved",
          "No injury records resembles no injuries", "Emptylistcomponent10 versusoneOutPG8.8; upcoming contexts can pass[]",
          "P2", "Unknown health can appear healthy", "Empty list lacks availability-versus-absence distinction",
          "Carry coverage state and source age separately from injury list",
          "Existing roster feed and source metadata", "Medium: 1 day",
          "Unavailable roster differs from confirmed zero injuries and receives documented decision treatment")
    issue("NBA-004", "NBA", "Offense rule saturation", "Confirmed mechanism; suspected predictive weakness",
          "High offense levels collapse to identical component", "110PPG->8.33;115/120/130PPG->10",
          "P2", "Potential lost discrimination; no measured accuracy effect", "Hardcoded bounded normalization",
          "Assess sensitivity and paired historical effect before changing normalization",
          "Existing completed results and current rule function", "Medium: 1 day plus valid evaluation",
          "Any proposal beats unchanged rule on predeclared paired proper loss without provenance compromise")
    issue("OPS-003", "All", "Monitor history/thresholds", "Confirmed design limitations",
          "In-memory health history and broad thresholds limit provenance", "Never-success path skips stale-age check; processrestart resets memory; monitor TTL differs from card TTL",
          "P2", "Healthok alone does not guarantee every card fresh; alert delivery unverified",
          "Ephemeral process state and multiple freshness contracts", "Persist attempts and align source-age alerts with prediction expiry",
          "Existing monitor/cache helpers and receipt model", "Medium: 2-3 days",
          "Never-success/restart/expired-source cases alert correctly; authorized delivery test observed")
    issue("NFL-003", "NFL", "Incremental learned-model signal", "Suspected cause, not diagnosed",
          "Reason learned models fail to beat Elo is not identified",
          "385testgames/33weekblocks/twoseasons;54/100masked;bothpairedLLintervalscrosszero",
          "P2", "No supported production-superiority claim", None,
          "Keep existing models frozen; collect genuine prospective inputs and predeclare paired evaluation",
          "Existing frozen Elo/logistic and archive", "Future observation period; 1-2 days analysis",
          "Adequate registered prospective sample, paired intervals/season consistency, no finalholdout reuse", confidence="Low for causal attribution; high for inconclusive comparison")
    write(out, "issue_register.json", {"generated_at": datetime.now(UTC), "scope": "Editorial diagnoses grounded in linked reports and audit exports; no fixes implemented", "issues": issues})

    dimensions = [
        ("historical_coverage", (3, "five schedule/PBP seasons"), (3, "2023-26 broad monthly history plus partial2022"), (2, "7888fixtures without league lineage; rawyearerror")),
        ("current_season_coverage", (3, "272schedules/93pastPBPinitial"), (2, "1265cached2027events,universeunverified"), (2, "212completed/88scheduled across3leagues")),
        ("data_completeness", (2, "auxiliarygaps,participationnotinseason"), (2, "player/lineup/possessiongaps"), (1, "no league lineage,13unsupportedcolumns")),
        ("data_freshness", (2, "currentschedules,PBPbackoff,oldaux"), (1, "appsuccess524minutesoldat02:56UTC"), (1, "appsuccess539minutesoldat02:56UTC")),
        ("data_accuracy", (3, "DBchecksok,nonsealedresultsconsistent"), (2, "scoresconsistent,metricproxysemantics"), (1, "rawyearerror/propsmappings")),
        ("pregame_evidence", (2, "actualarchiveonefixture,trainingClassB"), (1, "noimmutablecurrentwinnerarchive"), (1, "noimmutablecurrentblendarchive")),
        ("feature_availability", (2, "54of100historicallymasked"), (2, "partialrules,optionalclassifierabsent"), (2, "47varyingof60columns")),
        ("winner_model_validation", (3, "385chronologicalpairedgames"), (None, "currentwinnerperformancenotmeasurable"), (2, "classifier1518replay,blendUnknown")),
        ("winner_calibration", (2, "historicalECE,noprospectiveoutcome"), (None, "currentwinnercalibrationUnknown"), (2, "pooledclassifieronly,noleagueproof")),
        ("operational_reliability", (2, "singletonreceipts,failures/guardgap"), (1, "verifieddegradedrefresh"), (1, "verifieddegradedrefresh"))]
    write(out, "comparison_matrix.json", {"rubric": {"1": "Measured material deficiency", "2": "Useful but incomplete/reconstructed", "3": "Broad evidence with bounded gaps and controls", "4": "Independent reconciliation or durable prospective proof", "null": "Unknown, not zero"},
          "not_an_accuracy_percentage": True, "aggregation_permitted": False,
          "dimensions": [{"dimension": name, **{sport: {"score": value[0], "evidence": value[1]} for sport, value in zip(("NFL", "NBA", "Soccer"), values, strict=True)}} for name, *values in dimensions]})
    replay, integrity = load(out / "soccer_model_replay.json"), load(out / "integrity_recheck.json")
    before = load(out / "preservation_before.json")["files"]
    delta = [{"file": name, "snapshot_sha256": expected, "current_sha256": fingerprint(ROOT / name),
              "attribution": "Changed outside audit edits; do not assume loaded runtime matches disk"}
             for name, expected in before.items() if fingerprint(ROOT / name) != expected]
    write(out, "source_snapshot_delta.json", {"checked_at": datetime.now(UTC), "files_checked": len(before), "changed": delta,
          "interpretation": "Original soccer route mismatch corrected on disk; validation and loaded runtime identity separate"})
    tracking = load(out / "tracking_audit.json")
    missing = {k: sum(row.get(k) is None for row in tracking["safe_rows"]) for k in ("kickoff", "model_version", "source", "event_date")}
    summary = {"generated_at": datetime.now(UTC), "feature_entries": len(load(out / "feature_inventory.json")["entries"]),
        "nfl_evaluated_games": 385, "nfl_reconstructed_games": 888,
        "nfl_reserved_games": 109, "sealed_outcomes_accessed": 0,
        "soccer_classifier_replay": replay["model"], "soccer_train_prior": replay["training_prior_baseline"],
        "soccer_paired_exploratory_interval": replay["exploratory_paired_log_loss_difference"],
        "scoring_reports": {sport: load(ROOT / ("data/validation/scoring_" + sport + ".json")) for sport in ("soccer", "nba", "nfl")},
        "tracking_missing_fields": {"denominator": tracking["rows"], "missing": missing},
        "latest_operational_evidence": integrity["public_metadata_and_health"],
        "preservation": integrity["artifact_preservation"],
        "scope": "Audit only; no deployment/retraining/worker restart/production algorithm edits by audit"}
    write(out, "audit_summary.json", summary)
    files = sorted(p for p in out.iterdir() if p.is_file() and p.name != "export_manifest.json")
    docs = sorted((ROOT / "docs").glob("*DIAGNOS*.md")) + [ROOT / "docs" / n for n in (
        "SCORPRED_DATA_QUALITY_AUDIT.md", "SCORPRED_MODEL_VALIDATION_AUDIT.md", "SCORPRED_REMEDIATION_ROADMAP.md")]
    write(out, "export_manifest.json", {"generated_at": datetime.now(UTC), "algorithm": "sha256",
          "files": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [*files, *docs]}})
    print(f"Wrote {len(issues)} issues, 10 comparison dimensions and audit summary/manifest")


if __name__ == "__main__":
    main()
