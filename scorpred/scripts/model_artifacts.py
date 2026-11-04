"""Create or verify a checksum-pinned model release before starting the application."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from urllib.request import urlopen
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import joblib
import sklearn
from ml_service import validate_model_bundle
from runtime_paths import model_dir
from utils.atomic import atomic_write_json


def checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def create_manifest(directory, *, release_tag=None):
    directory = Path(directory)
    model = directory / "soccer_random_forest_clean.pkl"
    bundle = joblib.load(model)
    names = validate_model_bundle(bundle)
    entry = {"sha256": checksum(model), "feature_count": len(names), "schema_version": bundle.get("schema_version")}
    if release_tag:
        if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in release_tag):
            raise ValueError("Invalid release tag")
        entry["url"] = f"https://github.com/AnoHondz/scorpred/releases/download/{release_tag}/{model.name}"
    manifest = {"version": release_tag or bundle.get("trained_at", "bundled"),
                "sklearn_version": bundle.get("sklearn_version", sklearn.__version__), "files": {model.name: entry}}
    atomic_write_json(directory / "manifest.json", manifest)
    return manifest


def ensure_models(manifest_path, directory=None, *, download=True):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("sklearn_version") != sklearn.__version__:
        raise ValueError("Model release and runtime scikit-learn versions differ")
    destination = Path(directory or manifest_path.parent)
    destination.mkdir(parents=True, exist_ok=True)
    for name, entry in manifest["files"].items():
        if Path(name).name != name or "/" in name or "\\" in name or not name.endswith(".pkl"):
            raise ValueError("Model manifest contains an unsafe filename")
        path = destination / name
        if not path.exists() or checksum(path) != entry["sha256"]:
            url = entry.get("url", "")
            if not download or not url or urlparse(url).scheme != "https":
                raise ValueError(f"Missing or corrupt model artifact: {name}")
            with tempfile.TemporaryDirectory(dir=destination) as temporary:
                candidate = Path(temporary) / name
                with urlopen(url, timeout=60) as response, candidate.open("wb") as output:
                    received = 0
                    while chunk := response.read(1024 * 1024):
                        received += len(chunk)
                        if received > 128 * 1024 * 1024:
                            raise ValueError("Model artifact exceeds download limit")
                        output.write(chunk)
                if checksum(candidate) != entry["sha256"]:
                    raise ValueError("Downloaded model checksum mismatch")
                candidate.replace(path)
        bundle = joblib.load(path)
        names = validate_model_bundle(bundle)
        if len(names) != entry["feature_count"] or bundle.get("schema_version") != entry.get("schema_version"):
            raise ValueError("Model release metadata does not match its manifest")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--release-tag")
    parser.add_argument("--directory", type=Path, default=model_dir())
    args = parser.parse_args()
    if args.create:
        create_manifest(args.directory, release_tag=args.release_tag)
    manifest = ensure_models(args.directory / "manifest.json", args.directory)
    print(f"Verified model release {manifest['version']} ({len(manifest['files'])} artifact)")
