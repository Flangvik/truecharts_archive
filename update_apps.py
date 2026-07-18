#!/usr/bin/env python3
"""
Automated TrueCharts app updater.

Checks upstream releases for each configured app, creates new chart versions
with updated image tags and digests, and runs build_app_versions.py.

Usage:
  python3 update_apps.py              # Update all apps
  python3 update_apps.py --dry-run    # Check without making changes
  python3 update_apps.py --apps plex jackett  # Update specific apps only
"""

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

# ── App configurations ───────────────────────────────────────────────────────
# version_source: how to find the latest upstream version
#   "github_release" - GitHub Releases API (most common)
#   "github_tags"    - GitHub Tags API (for repos without releases)
#   "ghcr_tags"      - GHCR container registry tag listing
#
# tag_format: how the Docker image tag relates to the app version
#   "{version}"  -> tag is just the version (e.g. "1.5.6")
#   "v{version}" -> tag has a v prefix (e.g. "v2.16.1")
#
# skip_digest: if True, don't attempt to fetch an image digest

APPS = [
    {
        "name": "plex",
        "path": "stable/plex",
        "image_repo": "ghcr.io/home-operations/plex",
        "tag_format": "{version}",
        "version_source": "ghcr_tags",
        "ghcr_repo": "home-operations/plex",
        "tag_pattern": r"^\d+\.\d+\.\d+\.\d+$",
    },
    {
        "name": "jackett",
        "path": "stable/jackett",
        "image_repo": "linuxserver/jackett",
        "tag_format": "{version}",
        "version_source": "github_release",
        "github_repo": "Jackett/Jackett",
    },
    {
        "name": "syncthing",
        "path": "stable/syncthing",
        "image_repo": "docker.io/syncthing/syncthing",
        "tag_format": "{version}",
        "version_source": "github_release",
        "github_repo": "syncthing/syncthing",
    },
    {
        "name": "tautulli",
        "path": "stable/tautulli",
        "image_repo": "ghcr.io/tautulli/tautulli",
        "tag_format": "v{version}",
        "version_source": "github_release",
        "github_repo": "Tautulli/Tautulli",
    },
    {
        "name": "tailscale",
        "path": "stable/tailscale",
        "image_repo": "docker.io/tailscale/tailscale",
        "tag_format": "v{version}",
        "version_source": "github_release",
        "github_repo": "tailscale/tailscale",
    },
    {
        "name": "overseerr",
        "path": "stable/overseerr",
        "image_repo": "ghcr.io/sct/overseerr",
        "tag_format": "{version}",
        "version_source": "github_release",
        "github_repo": "sct/overseerr",
    },
    {
        "name": "bazarr",
        "path": "stable/bazarr",
        "image_repo": "ghcr.io/home-operations/bazarr",
        "tag_format": "{version}",
        "version_source": "github_release",
        "github_repo": "morpheus65535/bazarr",
    },
    {
        "name": "ombi",
        "path": "stable/ombi",
        "image_repo": "linuxserver/ombi",
        "tag_format": "{version}",
        "version_source": "github_release",
        "github_repo": "Ombi-app/Ombi",
    },
    {
        # home-operations publishes its own sabnzbd tags; query the registry
        # directly (same as sonarr/qbittorrent) instead of the GitHub release.
        "name": "sabnzbd",
        "path": "stable/sabnzbd",
        "image_repo": "ghcr.io/home-operations/sabnzbd",
        "tag_format": "{version}",
        "version_source": "ghcr_tags",
        "ghcr_repo": "home-operations/sabnzbd",
        "tag_pattern": r"^\d+\.\d+\.\d+$",
    },
    {
        "name": "flaresolverr",
        "path": "stable/flaresolverr",
        "image_repo": "ghcr.io/flaresolverr/flaresolverr",
        "tag_format": "v{version}",
        "version_source": "github_release",
        "github_repo": "FlareSolverr/FlareSolverr",
    },
    {
        # Sonarr's chart is the rootless generation (uses SONARR__* env), so it
        # needs the rootless ghcr.io/home-operations image, NOT linuxserver (which
        # must start as root and breaks the chart's non-root securityContext).
        # home-operations tags by its own build number, so query the registry
        # directly (same approach as plex) instead of the GitHub release version.
        "name": "sonarr",
        "path": "stable/sonarr",
        "image_repo": "ghcr.io/home-operations/sonarr",
        "tag_format": "{version}",
        "version_source": "ghcr_tags",
        "ghcr_repo": "home-operations/sonarr",
        "tag_pattern": r"^\d+\.\d+\.\d+\.\d+$",
    },
    {
        "name": "seerr",
        "path": "stable/seerr",
        "image_repo": "seerr/seerr",
        "tag_format": "v{version}",
        "version_source": "github_release",
        "github_repo": "seerr-team/seerr",
    },
    {
        "name": "threadfin",
        "path": "stable/threadfin",
        "image_repo": "fyb3roptik/threadfin",
        "tag_format": "{version}",
        "version_source": "github_release",
        "github_repo": "Threadfin/Threadfin",
    },
    {
        # qBittorrent's chart runs the rootless ghcr.io/home-operations image
        # (that's what the working install uses). The old oci.trueforge.org
        # registry lags upstream (e.g. missing 5.2.2), so upgrading to the latest
        # GitHub release ErrImagePulls and the pod fails to start. Query
        # home-operations' own published tags instead (same approach as plex).
        "name": "qbittorrent",
        "path": "stable/qbittorrent",
        "image_repo": "ghcr.io/home-operations/qbittorrent",
        "tag_format": "{version}",
        "version_source": "ghcr_tags",
        "ghcr_repo": "home-operations/qbittorrent",
        "tag_pattern": r"^\d+\.\d+\.\d+$",
    },
]


# ── Logging ──────────────────────────────────────────────────────────────────

def log(msg, level="INFO"):
    print(f"[{level}] {msg}")


# ── Version fetching ─────────────────────────────────────────────────────────

def github_api(url):
    """Make a GitHub API request with optional token auth."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        log(f"GitHub API error for {url}: {e.code}", "ERROR")
        return None
    except Exception as e:
        log(f"GitHub API error for {url}: {e}", "ERROR")
        return None


def get_latest_github_release(repo):
    """Get latest release version from GitHub (strips 'v' prefix)."""
    data = github_api(f"https://api.github.com/repos/{repo}/releases/latest")
    if data and "tag_name" in data:
        return data["tag_name"].lstrip("v")
    return None


def get_latest_github_tag(repo, prefix=""):
    """Get latest version from GitHub tags with optional prefix stripping."""
    data = github_api(f"https://api.github.com/repos/{repo}/tags?per_page=20")
    if not data:
        return None
    for tag_info in data:
        name = tag_info["name"]
        if prefix and name.startswith(prefix):
            return name[len(prefix):]
        elif not prefix and re.match(r"^v?\d+\.\d+", name):
            return name.lstrip("v")
    return None


def get_latest_ghcr_tag(repo, pattern):
    """Get latest version tag from GitHub Container Registry."""
    try:
        token_url = f"https://ghcr.io/token?scope=repository:{repo}:pull"
        with urllib.request.urlopen(token_url, timeout=15) as resp:
            token = json.loads(resp.read())["token"]

        req = urllib.request.Request(
            f"https://ghcr.io/v2/{repo}/tags/list",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            tags = json.loads(resp.read()).get("tags", [])

        regex = re.compile(pattern)
        version_tags = [t for t in tags if regex.match(t)]
        if not version_tags:
            return None

        version_tags.sort(
            key=lambda v: [int(x) for x in re.findall(r"\d+", v)],
            reverse=True,
        )
        return version_tags[0]
    except Exception as e:
        log(f"GHCR tag lookup failed for {repo}: {e}", "ERROR")
        return None


def get_latest_version(app_config):
    """Get the latest upstream version for an app."""
    source = app_config.get("version_source", "github_release")

    if source == "github_release":
        return get_latest_github_release(app_config["github_repo"])
    elif source == "github_tags":
        prefix = app_config.get("github_tag_prefix", "")
        return get_latest_github_tag(app_config["github_repo"], prefix)
    elif source == "ghcr_tags":
        return get_latest_ghcr_tag(app_config["ghcr_repo"], app_config["tag_pattern"])
    return None


# ── Image digest fetching ────────────────────────────────────────────────────

def get_digest_docker(image_repo, tag):
    """Get image digest via docker CLI."""
    try:
        result = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect", f"{image_repo}:{tag}"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("Digest:"):
                    return line.split("Digest:", 1)[1].strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def get_digest_registry(image_repo, tag):
    """Get image digest via container registry HTTP API."""
    try:
        if image_repo.startswith("ghcr.io/"):
            repo_path = image_repo[len("ghcr.io/"):]
            token_url = f"https://ghcr.io/token?scope=repository:{repo_path}:pull"
            manifest_url = f"https://ghcr.io/v2/{repo_path}/manifests/{tag}"
        elif image_repo.startswith("docker.io/"):
            repo_path = image_repo[len("docker.io/"):]
            token_url = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo_path}:pull"
            manifest_url = f"https://registry-1.docker.io/v2/{repo_path}/manifests/{tag}"
        elif not image_repo.startswith(("oci.", "http")):
            # Assume Docker Hub (e.g. linuxserver/jackett)
            repo_path = image_repo
            token_url = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo_path}:pull"
            manifest_url = f"https://registry-1.docker.io/v2/{repo_path}/manifests/{tag}"
        else:
            return None

        with urllib.request.urlopen(token_url, timeout=15) as resp:
            token = json.loads(resp.read())["token"]

        accept = ", ".join([
            "application/vnd.oci.image.index.v1+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.docker.distribution.manifest.v2+json",
        ])
        req = urllib.request.Request(manifest_url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": accept,
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.headers.get("docker-content-digest")
    except Exception as e:
        log(f"Registry digest lookup failed for {image_repo}:{tag}: {e}", "WARN")
        return None


def get_image_digest(image_repo, tag):
    """Get image digest, trying docker CLI first then registry API."""
    digest = get_digest_docker(image_repo, tag)
    if not digest:
        digest = get_digest_registry(image_repo, tag)
    return digest


def image_exists(image_repo, tag):
    """Check that image_repo:tag is actually published.

    Returns True (exists), False (definitively missing / 404), or None (lookup
    error). Guards against bumping a chart to a version the image registry has
    not published yet (upstream release out before the container image, a
    registry that lags, or a registry/repo mismatch) -- which produces an
    ErrImagePull and a pod that never starts.
    """
    accept = ", ".join([
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
    ])
    try:
        if image_repo.startswith("ghcr.io/"):
            path = image_repo[len("ghcr.io/"):]
            tok = json.loads(urllib.request.urlopen(
                f"https://ghcr.io/token?scope=repository:{path}:pull", timeout=15).read())["token"]
            url = f"https://ghcr.io/v2/{path}/manifests/{tag}"
            hdr = {"Authorization": f"Bearer {tok}", "Accept": accept}
        elif image_repo.startswith(("oci.", "http")):
            host = image_repo.split("/")[0]
            path = image_repo[len(host) + 1:]
            url = f"https://{host}/v2/{path}/manifests/{tag}"
            hdr = {"Accept": accept}
        else:  # docker hub (docker.io/x or bare owner/image)
            path = image_repo[len("docker.io/"):] if image_repo.startswith("docker.io/") else image_repo
            if "/" not in path:
                path = "library/" + path
            tok = json.loads(urllib.request.urlopen(
                f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{path}:pull",
                timeout=15).read())["token"]
            url = f"https://registry-1.docker.io/v2/{path}/manifests/{tag}"
            hdr = {"Authorization": f"Bearer {tok}", "Accept": accept}
        urllib.request.urlopen(urllib.request.Request(url, method="GET", headers=hdr), timeout=15)
        return True
    except urllib.error.HTTPError as e:
        return False if e.code in (404, 401) else None
    except Exception:
        return None


# ── Chart manipulation ───────────────────────────────────────────────────────

def get_current_version_info(app_path):
    """Find the latest chart version folder and read its appVersion."""
    app_dir = REPO_ROOT / app_path
    if not app_dir.exists():
        return None, None, None

    versions = []
    for item in app_dir.iterdir():
        if item.is_dir() and (item / "Chart.yaml").exists():
            try:
                parts = tuple(int(x) for x in item.name.split("."))
                versions.append((parts, item.name))
            except ValueError:
                continue

    if not versions:
        return None, None, None

    versions.sort(reverse=True)
    latest_version = versions[0][1]
    version_dir = app_dir / latest_version

    chart_text = (version_dir / "Chart.yaml").read_text()
    m = re.search(r"^appVersion:\s*(.+)$", chart_text, re.MULTILINE)
    app_version = m.group(1).strip().strip("'\"") if m else None

    return latest_version, app_version, version_dir


def increment_patch(version):
    """Increment the last segment of a dotted version string."""
    parts = version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


# ── Main update logic ────────────────────────────────────────────────────────

def update_app(app_config, dry_run=False):
    """
    Check and update a single app.
    Returns (app_name, new_app_version) if updated, or (None, None).
    """
    name = app_config["name"]
    path = app_config["path"]

    chart_version, current_app_version, version_dir = get_current_version_info(path)
    if not chart_version:
        log(f"{name}: could not find current version", "WARN")
        return None, None

    latest_version = get_latest_version(app_config)
    if not latest_version:
        log(f"{name}: could not determine latest upstream version", "WARN")
        return None, None

    if latest_version == current_app_version:
        log(f"{name}: up to date ({current_app_version})")
        return None, None

    log(f"{name}: {current_app_version} -> {latest_version}")

    # Guard: don't bump to a version whose image isn't published yet (avoids
    # ErrImagePull / pods that never start). None = lookup error -> proceed.
    probe_tag = app_config.get("tag_format", "{version}").format(version=latest_version)
    if image_exists(app_config["image_repo"], probe_tag) is False:
        log(f"{name}: image {app_config['image_repo']}:{probe_tag} not published yet; skipping", "WARN")
        return None, None

    if dry_run:
        return name, latest_version

    # Create new chart version folder
    new_chart_version = increment_patch(chart_version)
    app_dir = REPO_ROOT / path
    new_version_dir = app_dir / new_chart_version
    shutil.copytree(version_dir, new_version_dir)

    # Update Chart.yaml
    chart_file = new_version_dir / "Chart.yaml"
    chart_text = chart_file.read_text()
    chart_text = re.sub(
        r"^(appVersion:\s*).*$", rf"\g<1>{latest_version}",
        chart_text, count=1, flags=re.MULTILINE,
    )
    chart_text = re.sub(
        r"^(version:\s*).*$", rf"\g<1>{new_chart_version}",
        chart_text, count=1, flags=re.MULTILINE,
    )
    chart_file.write_text(chart_text)

    # Build new image tag
    tag_format = app_config.get("tag_format", "{version}")
    image_tag = tag_format.format(version=latest_version)

    digest = None
    if not app_config.get("skip_digest", False):
        log(f"{name}: fetching digest for {app_config['image_repo']}:{image_tag}...")
        digest = get_image_digest(app_config["image_repo"], image_tag)
        if digest:
            log(f"{name}: digest {digest[:20]}...")
        else:
            log(f"{name}: no digest found, using tag only", "WARN")

    new_tag_value = f"{image_tag}@{digest}" if digest else image_tag

    # Update ix_values.yaml (first tag: line only = main image)
    ix_file = new_version_dir / "ix_values.yaml"
    ix_text = ix_file.read_text()
    ix_text = re.sub(
        r"""^(\s*tag:\s*)['"]?[^\n]*$""",
        rf'\1"{new_tag_value}"',
        ix_text, count=1, flags=re.MULTILINE,
    )
    ix_file.write_text(ix_text)

    log(f"{name}: created chart {new_chart_version} (appVersion={latest_version})")
    return name, latest_version


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Auto-update TrueCharts apps to latest upstream versions")
    parser.add_argument("--dry-run", action="store_true", help="Check for updates without making changes")
    parser.add_argument("--apps", nargs="*", help="Specific app names to check (default: all)")
    args = parser.parse_args()

    apps_to_check = APPS
    if args.apps:
        apps_to_check = [a for a in APPS if a["name"] in args.apps]
        if not apps_to_check:
            log(f"No matching apps found. Available: {', '.join(a['name'] for a in APPS)}", "ERROR")
            sys.exit(1)

    log(f"Checking {len(apps_to_check)} app(s) for updates...")
    updated = []
    updated_paths = []

    for app in apps_to_check:
        try:
            name, version = update_app(app, dry_run=args.dry_run)
            if name:
                updated.append((name, version))
                updated_paths.append(app["path"])
        except Exception as e:
            log(f"{app['name']}: unexpected error - {e}", "ERROR")

    # Run build_app_versions.py and build_catalog.py for updated apps
    if updated_paths and not args.dry_run:
        log(f"Updating app_versions.json for {len(updated_paths)} app(s)...")
        try:
            subprocess.run(
                [sys.executable, str(REPO_ROOT / "build_app_versions.py")] + updated_paths,
                cwd=REPO_ROOT, check=True,
            )
        except subprocess.CalledProcessError as e:
            log(f"build_app_versions.py failed: {e}", "ERROR")

        log("Updating catalog.json...")
        try:
            subprocess.run(
                [sys.executable, str(REPO_ROOT / "build_catalog.py")],
                cwd=REPO_ROOT, check=True,
            )
        except subprocess.CalledProcessError as e:
            log(f"build_catalog.py failed: {e}", "ERROR")

    # Summary
    print()
    if updated:
        action = "would update" if args.dry_run else "updated"
        summary_parts = [f"{name.capitalize()} {ver}" for name, ver in updated]
        log(f"{'DRY RUN: ' if args.dry_run else ''}{action.capitalize()} {len(updated)} app(s): {', '.join(summary_parts)}")
    else:
        log("All apps are up to date. No changes needed.")


if __name__ == "__main__":
    main()
