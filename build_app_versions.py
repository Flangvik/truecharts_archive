#!/usr/bin/env python3
"""
TrueCharts app_versions.json Builder

This script updates app_versions.json for each app to include any version
directories that exist on disk but are missing from the JSON.

When you add a new chart version (e.g. jackett/21.8.9), run this script to
add the corresponding entry to app_versions.json so TrueNAS SCALE can
discover and offer the new version in the Apps UI.

Usage:
  python build_app_versions.py [--repo-root PATH] [--dry-run]
  python build_app_versions.py stable/jackett stable/ombi  # Update specific apps only
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Trains to process (subdirectories of repo root)
TRAINS = ["stable", "incubator", "system", "dependency", "core", "enterprise", "games", "dev"]


def load_chart_yaml(chart_path: Path) -> Dict[str, Any]:
    """Load Chart.yaml and extract appVersion and version."""
    chart_file = chart_path / "Chart.yaml"
    if not chart_file.exists():
        return {}

    try:
        import yaml
        with open(chart_file) as f:
            data = yaml.safe_load(f)
        return data or {}
    except ImportError:
        # Fallback: simple regex extraction
        text = chart_file.read_text()
        result = {}
        for key in ("appVersion", "version"):
            m = re.search(rf"^{key}:\s*(.+)$", text, re.MULTILINE)
            if m:
                result[key] = m.group(1).strip().strip('"\'')
        return result


def get_version_directories(app_path: Path) -> List[str]:
    """Get version directory names that contain Chart.yaml."""
    versions = []
    for item in app_path.iterdir():
        if item.is_dir() and item.name not in (".git", "__pycache__", "charts"):
            if (item / "Chart.yaml").exists():
                versions.append(item.name)
    return versions


def deep_copy_omit(obj: Any, omit_keys: Optional[set] = None) -> Any:
    """Deep copy a JSON-serializable object, optionally omitting certain keys."""
    omit_keys = omit_keys or set()
    if isinstance(obj, dict):
        return {k: deep_copy_omit(v, omit_keys) for k, v in obj.items() if k not in omit_keys}
    if isinstance(obj, list):
        return [deep_copy_omit(x, omit_keys) for x in obj]
    return obj


def build_version_entry(
    template: Dict[str, Any],
    train: str,
    app_name: str,
    chart_version: str,
    app_version: str,
    location_base: str = "",
) -> Dict[str, Any]:
    """Create a new version entry from a template, updating version-specific fields."""
    entry = deep_copy_omit(template)

    entry["version"] = chart_version
    entry["human_version"] = f"{app_version}_{chart_version}"

    if location_base:
        entry["location"] = f"{location_base}/{train}/{app_name}/{chart_version}"
    else:
        entry["location"] = f"{train}/{app_name}/{chart_version}"

    entry["last_update"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    if "chart_metadata" in entry:
        entry["chart_metadata"]["version"] = chart_version
        entry["chart_metadata"]["appVersion"] = app_version

    return entry


def process_app(
    app_path: Path,
    train: str,
    location_base: str = "",
    dry_run: bool = False,
) -> Tuple[bool, List[str]]:
    """
    Update app_versions.json for one app.
    Returns (success, list of added versions).
    """
    app_name = app_path.name
    json_path = app_path / "app_versions.json"

    version_dirs = get_version_directories(app_path)
    if not version_dirs:
        logger.debug(f"No version directories in {app_path}")
        return True, []

    if not json_path.exists():
        logger.warning(f"No app_versions.json at {json_path} - cannot add new versions")
        return False, []

    try:
        with open(json_path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {json_path}: {e}")
        return False, []

    if not isinstance(data, dict):
        logger.error(f"app_versions.json in {app_path} is not a dictionary")
        return False, []

    # Find versions on disk that are missing from JSON
    json_versions = set(data.keys())
    missing = [v for v in version_dirs if v not in json_versions]

    if not missing:
        return True, []

    # Get template from latest existing version (prefer highest version)
    def sort_key(v: str) -> Tuple[int, ...]:
        try:
            return tuple(int(x) for x in v.split("."))
        except (ValueError, AttributeError):
            return (0, 0, 0)

    existing_sorted = sorted(json_versions, key=sort_key, reverse=True)
    if not existing_sorted:
        logger.warning(f"No existing versions in app_versions.json for {app_name}")
        return False, []

    template_key = existing_sorted[0]
    template = data[template_key]

    added = []
    for ver in sorted(missing, key=sort_key):
        chart_path = app_path / ver
        chart_data = load_chart_yaml(chart_path)
        app_version = chart_data.get("appVersion", "0.0.0")
        chart_version_from_yaml = chart_data.get("version", ver)

        if chart_version_from_yaml != ver:
            logger.warning(f"Chart.yaml version {chart_version_from_yaml} != dir name {ver} for {app_name}/{ver}")

        entry = build_version_entry(
            template,
            train=train,
            app_name=app_name,
            chart_version=ver,
            app_version=app_version,
            location_base=location_base,
        )

        if dry_run:
            logger.info(f"[DRY RUN] Would add {app_name}/{ver} (appVersion={app_version})")
        else:
            data[ver] = entry
            logger.info(f"Added {app_name}/{ver} (appVersion={app_version})")
        added.append(ver)

    if added and not dry_run:
        # Re-sort keys: existing first (by version), then new
        all_versions = sorted(data.keys(), key=sort_key, reverse=True)
        data = {k: data[k] for k in all_versions}

        with open(json_path, "w") as f:
            json.dump(data, f, indent=4)

    return True, added


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Update app_versions.json to include new chart version directories"
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Specific app paths to process (e.g. stable/jackett). If empty, process all trains.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Repository root (default: script directory)",
    )
    parser.add_argument(
        "--location-base",
        default="",
        help="Base path for 'location' field (e.g. /home/runner/_work/catalog/catalog). Leave empty for relative paths.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write files")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    repo_root = Path(args.repo_root)
    if not repo_root.exists():
        logger.error(f"Repo root does not exist: {repo_root}")
        raise SystemExit(1)

    total_added = 0

    if args.paths:
        for path_str in args.paths:
            path = repo_root / path_str
            if not path.exists():
                logger.warning(f"Path does not exist: {path}")
                continue
            parts = path.relative_to(repo_root).parts
            if len(parts) < 2:
                logger.warning(f"Path must be train/app (e.g. stable/jackett): {path_str}")
                continue
            train, app_name = parts[0], parts[1]
            success, added = process_app(
                path,
                train=train,
                location_base=args.location_base,
                dry_run=args.dry_run,
            )
            total_added += len(added)
    else:
        for train in TRAINS:
            train_path = repo_root / train
            if not train_path.exists() or not train_path.is_dir():
                continue
            for app_path in sorted(train_path.iterdir()):
                if app_path.is_dir() and not app_path.name.startswith("."):
                    if (app_path / "app_versions.json").exists():
                        success, added = process_app(
                            app_path,
                            train=train,
                            location_base=args.location_base,
                            dry_run=args.dry_run,
                        )
                        total_added += len(added)

    logger.info(f"Done. Added {total_added} version(s) to app_versions.json.")


if __name__ == "__main__":
    main()
