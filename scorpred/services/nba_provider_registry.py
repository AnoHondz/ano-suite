"""Offline NBA research catalog. This module has no collection/serving adapter.

Public repository visibility is never a permission grant. Even a future verified
permission record cannot activate asset access in this metadata-only phase.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from runtime_paths import repo_root

REGISTRY_PATH = repo_root() / "data/providers/nba_registry.json"
PERMITTED = "Verified permitted for intended use"
RESTRICTED = "Restricted"
UNCLEAR = "Unclear or unverified"
PERMISSION_STATUSES = frozenset((PERMITTED, RESTRICTED, UNCLEAR))
PURPOSES = ("collection", "research", "training", "serving", "redistribution")
FLAGS = ("enabled", "collection_enabled", "training_enabled", "serving_enabled")


def canonical_repository(value: str) -> str:
    value = value.strip().rstrip("/")
    if "://" not in value:
        value = "https://github.com/" + value
    url = urlsplit(value)
    path = url.path.removesuffix(".git")
    if (url.scheme != "https" or url.netloc.casefold() != "github.com"
            or url.query or url.fragment
            or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path)):
        raise ValueError("Expected a GitHub repository URL, not an asset URL")
    return "https://github.com" + path.casefold()


def validate_registry(document: dict) -> dict:
    if document.get("schema_version") != 1 or document.get("mode") != "metadata_only":
        raise ValueError("Unsupported NBA registry mode/schema")
    providers = document.get("providers")
    if not isinstance(providers, list) or not providers:
        raise ValueError("A nonempty provider list is required")
    repositories, identifiers = set(), set()
    for provider in providers:
        repository = canonical_repository(provider["repository_url"])
        if repository in repositories or provider["provider_id"] in identifiers:
            raise ValueError("Duplicate NBA provider candidate")
        repositories.add(repository)
        identifiers.add(provider["provider_id"])
        # Do not allow missing, truthy strings, environment overrides or manual
        # JSON toggles to turn B1 into an importer.
        if any(provider.get(flag) is not False for flag in FLAGS):
            raise ValueError("NBA metadata-only candidates must remain disabled")
        permission = provider["permission"]
        if permission.get("status") not in PERMISSION_STATUSES:
            raise ValueError("Unknown permission status")
        if not permission.get("evidence") or not permission.get("intended_use"):
            raise ValueError("Purpose-specific permission evidence is required")
        if permission["status"] == PERMITTED:
            approval = permission.get("approval") or {}
            if (approval.get("verified") is not True or not approval.get("evidence_url")
                    or not approval.get("reviewed_at") or not approval.get("purposes")
                    or not set(approval["purposes"]).issubset(PURPOSES)):
                raise ValueError("A repository license alone is not verified intended-use approval")
        datasets = provider.get("datasets", [])
        keys = [dataset["dataset_id"] for dataset in datasets]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("Missing or duplicate dataset candidates")
    return document


def load_registry(path: Path | str = REGISTRY_PATH) -> dict:
    return validate_registry(json.loads(Path(path).read_text(encoding="utf-8")))


def asset_access_decision(provider: dict, purpose: str = "collection") -> dict:
    if purpose not in PURPOSES:
        raise ValueError("Unknown intended use")
    reasons = ["metadata_only_phase_has_no_asset_adapter"]
    if provider.get("enabled") is not True:
        reasons.append("provider_disabled")
    if provider.get(f"{purpose}_enabled") is not True:
        reasons.append("purpose_disabled")
    permission = provider.get("permission", {})
    approval = permission.get("approval") or {}
    if (permission.get("status") != PERMITTED or approval.get("verified") is not True
            or not approval.get("evidence_url") or not approval.get("reviewed_at")
            or purpose not in approval.get("purposes", [])):
        reasons.append("intended_use_permission_not_verified")
    return {"allowed": False, "purpose": purpose, "reasons": reasons}


def require_asset_access(provider: dict, purpose: str = "collection") -> None:
    raise PermissionError(", ".join(asset_access_decision(provider, purpose)["reasons"]))
