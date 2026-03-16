#!/usr/bin/env python3
"""
Rebuild catalog.json from the actual chart data on disk.

TrueNAS SCALE reads catalog.json as the master index to discover available
apps and their versions. This script updates it to reflect the latest
chart versions found in each app's version folders.

Usage:
  python3 build_catalog.py              # Update catalog.json
  python3 build_catalog.py --dry-run    # Preview changes without writing
"""

import json
import re
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
CATALOG_PATH = REPO_ROOT / "catalog.json"
TRAINS = ["stable", "incubator", "system", "premium"]


def load_chart_yaml_simple(chart_dir):
    """Parse Chart.yaml without requiring PyYAML (simple key: value extraction)."""
    chart_file = chart_dir / "Chart.yaml"
    if not chart_file.exists():
        return {}

    result = {}
    text = chart_file.read_text()

    # Extract simple top-level scalar fields
    for key in ("appVersion", "version", "description", "home", "name", "icon"):
        m = re.search(rf"^{key}:\s*(.+)$", text, re.MULTILINE)
        if m:
            result[key] = m.group(1).strip().strip("'\"")

    # Extract sources list
    sources = []
    in_sources = False
    for line in text.splitlines():
        if line.startswith("sources:"):
            in_sources = True
            continue
        if in_sources:
            if line.startswith("- "):
                sources.append(line[2:].strip().strip("'\""))
            elif not line.startswith(" ") and not line.startswith("-"):
                break
    if sources:
        result["sources"] = sources

    # Extract maintainers
    maintainers = []
    in_maintainers = False
    current = {}
    for line in text.splitlines():
        if line.startswith("maintainers:"):
            in_maintainers = True
            continue
        if in_maintainers:
            stripped = line.strip()
            if stripped.startswith("- name:"):
                if current:
                    maintainers.append(current)
                current = {"name": stripped.split(":", 1)[1].strip().strip("'\""), "email": "", "url": ""}
            elif stripped.startswith("email:") and current:
                current["email"] = stripped.split(":", 1)[1].strip().strip("'\"")
            elif stripped.startswith("url:") and current:
                current["url"] = stripped.split(":", 1)[1].strip().strip("'\"")
            elif not line.startswith(" ") and not line.startswith("-"):
                break
    if current:
        maintainers.append(current)
    if maintainers:
        result["maintainers"] = maintainers

    return result


def load_item_yaml(app_dir):
    """Parse item.yaml for icon_url and categories."""
    item_file = app_dir / "item.yaml"
    if not item_file.exists():
        return {}

    result = {}
    text = item_file.read_text()

    m = re.search(r"^icon_url:\s*(.+)$", text, re.MULTILINE)
    if m:
        result["icon_url"] = m.group(1).strip().strip("'\"")

    categories = []
    in_cats = False
    for line in text.splitlines():
        if line.strip().startswith("categories:"):
            in_cats = True
            continue
        if in_cats:
            stripped = line.strip()
            if stripped.startswith("- "):
                categories.append(stripped[2:].strip().strip("'\""))
            elif stripped and not stripped.startswith("-"):
                break
    if categories:
        result["categories"] = categories

    return result


def get_latest_version_dir(app_dir):
    """Find the highest semver version directory in an app folder."""
    versions = []
    for item in app_dir.iterdir():
        if item.is_dir() and (item / "Chart.yaml").exists():
            try:
                parts = tuple(int(x) for x in item.name.split("."))
                versions.append((parts, item.name, item))
            except ValueError:
                continue

    if not versions:
        return None, None

    versions.sort(reverse=True)
    return versions[0][1], versions[0][2]


def build_app_readme(description, train, app_name):
    """Build the HTML app_readme string matching the existing format."""
    return (
        f"<p>{description}</p>\n"
        f'<p>This App is supplied by TrueCharts, for more information visit the manual: '
        f'<a href="https://truecharts.org/charts/{train}/{app_name}">'
        f"https://truecharts.org/charts/{train}/{app_name}</a></p>\n"
        f"<hr />\n"
        f"<p>TrueCharts can only exist due to the incredible effort of our staff.\n"
        f'Please consider making a <a href="https://truecharts.org/sponsor">donation</a> '
        f"or contributing back to the project any way you can!</p>"
    )


def update_catalog(dry_run=False):
    """Update catalog.json with latest version info from disk."""
    with open(CATALOG_PATH) as f:
        catalog = json.load(f)

    changes = []
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    for train in TRAINS:
        train_dir = REPO_ROOT / train
        if not train_dir.exists():
            continue

        if train not in catalog:
            catalog[train] = {}

        for app_dir in sorted(train_dir.iterdir()):
            if not app_dir.is_dir() or app_dir.name.startswith("."):
                continue

            app_name = app_dir.name
            chart_version, version_dir = get_latest_version_dir(app_dir)
            if not version_dir:
                continue

            chart_data = load_chart_yaml_simple(version_dir)
            item_data = load_item_yaml(app_dir)

            app_version = chart_data.get("appVersion", "0.0.0")
            description = chart_data.get("description", app_name)

            # Check if this app needs updating
            existing = catalog[train].get(app_name, {})
            old_version = existing.get("latest_version")
            old_app_version = existing.get("latest_app_version")

            if old_version == chart_version and old_app_version == app_version:
                continue

            # Build/update entry
            entry = existing.copy() if existing else {}
            entry.update({
                "name": app_name,
                "description": description,
                "healthy": True,
                "healthy_error": None,
                "home": chart_data.get("home", f"https://truecharts.org/charts/{train}/{app_name}"),
                "location": f"/home/runner/_work/catalog/catalog/{train}/{app_name}",
                "latest_version": chart_version,
                "latest_app_version": app_version,
                "latest_human_version": f"{app_version}_{chart_version}",
                "last_update": now,
                "recommended": False,
                "tags": existing.get("tags", []),
                "screenshots": existing.get("screenshots", []),
            })

            # Only set these if not already present (preserve existing) or if new
            if "app_readme" not in entry or not existing:
                entry["app_readme"] = build_app_readme(description, train, app_name)
            if "title" not in entry:
                entry["title"] = app_name.replace("-", " ").title()

            # Update from Chart.yaml
            if "sources" in chart_data:
                entry["sources"] = chart_data["sources"]
            elif "sources" not in entry:
                entry["sources"] = []

            if "maintainers" in chart_data:
                entry["maintainers"] = chart_data["maintainers"]
            elif "maintainers" not in entry:
                entry["maintainers"] = []

            # Update from item.yaml
            if "icon_url" in item_data:
                entry["icon_url"] = item_data["icon_url"]
            elif "icon_url" not in entry:
                entry["icon_url"] = ""

            if "categories" in item_data:
                entry["categories"] = item_data["categories"]
            elif "categories" not in entry:
                entry["categories"] = []

            catalog[train][app_name] = entry
            old_info = f"{old_app_version} ({old_version})" if old_version else "NEW"
            changes.append(f"  {train}/{app_name}: {old_info} -> {app_version} ({chart_version})")

    if changes:
        action = "Would update" if dry_run else "Updated"
        print(f"[INFO] {action} {len(changes)} app(s) in catalog.json:")
        for c in changes:
            print(c)

        if not dry_run:
            with open(CATALOG_PATH, "w") as f:
                json.dump(catalog, f, indent=4)
            print(f"[INFO] catalog.json written.")
    else:
        print("[INFO] catalog.json is already up to date.")

    return len(changes)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Update catalog.json from chart data on disk")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    update_catalog(dry_run=args.dry_run)
