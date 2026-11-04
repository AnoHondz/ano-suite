"""Explicit opt-in file views. No Flask route or production selector uses v1."""
from copy import deepcopy
import json
from pathlib import Path

from services.soccer_lineage import VERSION as LINEAGE_VERSION, fingerprint
from services.soccer_stat_contracts import VERSION as STAT_VERSION

VERSION = "soccer-research-v1"


class ResearchViews:
    def __init__(self, artifacts, *, enabled=False, version=VERSION):
        if enabled is not True:
            raise PermissionError("Soccer research views are disabled by default")
        if version != VERSION or artifacts["manifest.json"].get("view_version") != VERSION:
            raise ValueError("Incompatible soccer research view version")
        manifest = artifacts["manifest.json"]
        if manifest.get("production_enabled") is not False or manifest.get("training_enabled") is not False:
            raise ValueError("Research bundle cannot activate production or training")
        if manifest.get("lineage_version") != LINEAGE_VERSION or manifest.get("stat_contract_version") != STAT_VERSION:
            raise ValueError("Incompatible lineage/statistic contract version")
        for name, expected in manifest["artifact_fingerprints"].items():
            if name not in artifacts or fingerprint(artifacts[name]) != expected:
                raise ValueError("Research artifact fingerprint mismatch")
        self._artifacts = deepcopy(artifacts)

    @classmethod
    def open(cls, directory, *, enabled=False, version=VERSION):
        if enabled is not True:
            raise PermissionError("Soccer research views are disabled by default")
        directory = Path(directory)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        allowed = {"lineage_sidecar.json", "duplicate_view.json", "canonical_fixtures.json",
                   "corrected_statistics.json", "quality_report.json", "input_manifest.json"}
        if set(manifest.get("artifact_fingerprints", {})) != allowed:
            raise ValueError("Unexpected research artifact names")
        artifacts = {name: json.loads((directory / name).read_text(encoding="utf-8")) for name in allowed}
        return cls(artifacts | {"manifest.json": manifest}, enabled=enabled, version=version)

    def lineage(self, status=None):
        rows = self._artifacts["lineage_sidecar.json"]["fixtures"]
        if status is not None and status not in {"verified", "candidate", "ambiguous", "unresolved"}:
            raise ValueError("Unknown lineage state")
        return deepcopy([r for r in rows if status is None or r["status"] == status])

    def canonical_fixtures(self, collection):
        if collection not in {"historical_reference", "legacy_e0"}:
            raise ValueError("Select an explicit research fixture collection")
        return deepcopy(self._artifacts["canonical_fixtures.json"][collection])

    def duplicate_groups(self):
        return deepcopy(self._artifacts["duplicate_view.json"]["duplicate_groups"])

    def corrected_statistics(self):
        return deepcopy(self._artifacts["corrected_statistics.json"])

    def quality(self):
        return deepcopy(self._artifacts["quality_report.json"])
