# TrueCharts Archive

A maintained fork of the [TrueCharts](https://github.com/truecharts/charts) Helm chart catalog for TrueNAS SCALE (23.10 / 24.04).

## Background

TrueCharts was a community-maintained catalog of Helm charts for TrueNAS SCALE's Kubernetes-based app system. When iX-Systems announced the move from Kubernetes to Docker in SCALE 25.10, TrueCharts deprecated their catalog — leaving users on SCALE 23.10/24.04 with no way to update their apps.

[v3DJG6GL](https://github.com/v3DJG6GL) forked the archive and manually maintained updates for over a year. This repo is a fork of that fork, now with **fully automated daily updates** via GitHub Actions.

## How it works

A daily GitHub Actions cron job ([`update_apps.py`](update_apps.py)):

1. Checks upstream GitHub releases / container registries for new versions
2. Creates new chart version folders with updated `Chart.yaml` and `ix_values.yaml` (digest-pinned images)
3. Rebuilds `app_versions.json` (per-app version index) and `catalog.json` (master TrueNAS catalog index)
4. Commits and pushes — your TrueNAS picks up the changes on catalog refresh

### Tracked apps

| App | Source |
|-----|--------|
| Plex | ghcr.io/home-operations/plex |
| Jackett | linuxserver/jackett |
| Syncthing | docker.io/syncthing/syncthing |
| Tautulli | ghcr.io/tautulli/tautulli |
| Tailscale | docker.io/tailscale/tailscale |
| Overseerr | ghcr.io/sct/overseerr |
| Bazarr | ghcr.io/home-operations/bazarr |
| Ombi | linuxserver/ombi |
| SABnzbd | ghcr.io/home-operations/sabnzbd |
| FlareSolverr | ghcr.io/flaresolverr/flaresolverr |
| Threadfin | fyb3roptik/threadfin |
| qBittorrent | oci.trueforge.org/containerforge/qbittorrent |

Radarr, Sonarr, Prowlarr, and Lidarr use `rolling` image tags and auto-update via container restart.

The catalog also contains **740+ other stable apps** from the original TrueCharts archive (not auto-updated).

## Setup

Add this repo as a catalog in TrueNAS SCALE:

1. Go to **Apps** > **Discover Apps** > **Manage Catalogs**
2. Delete your existing (deprecated) TrueCharts catalog if present
3. **Add Catalog** > **Continue**:
   - **Catalog Name:** `TrueCharts`
   - **Repository:** `https://github.com/Flangvik/truecharts_archive`
   - **Preferred Trains:** `stable, incubator, premium, system`
   - **Branch:** `main`

## Repository structure

```
├── stable/                  # 740+ production apps (plex, radarr, jackett, etc.)
├── incubator/               # Experimental apps
├── system/                  # System charts (cert-manager, prometheus, traefik)
├── premium/                 # Premium apps (authelia, nextcloud, vaultwarden)
├── catalog.json             # Master index — TrueNAS reads this to discover apps
├── update_apps.py           # Automated updater — checks upstream, creates new versions
├── build_catalog.py         # Rebuilds catalog.json from chart data on disk
├── build_app_versions.py    # Rebuilds per-app app_versions.json
├── cleanup_versions.py      # Prunes old chart versions (keeps 3 latest)
└── .github/workflows/
    ├── weekly-update.yaml   # Daily cron: runs update_apps.py
    └── build-catalog.yaml   # Rebuilds indexes on push
```

Each app follows this structure:
```
stable/{app-name}/
├── item.yaml              # Icon, categories
├── app_versions.json      # Version index (auto-generated)
└── {chart-version}/       # e.g., 18.3.8
    ├── Chart.yaml          # appVersion + chart version
    ├── ix_values.yaml      # Image repo, tag, config defaults
    ├── questions.yaml      # TrueNAS UI form
    ├── charts/common-23.0.10.tgz  # Shared dependency
    └── templates/
```

## Manual usage

```bash
# Check for updates without making changes
python3 update_apps.py --dry-run

# Update all tracked apps
python3 update_apps.py

# Update specific apps only
python3 update_apps.py --apps plex jackett

# Rebuild catalog.json from disk
python3 build_catalog.py

# Clean up old versions (keep 3 latest per app)
python3 cleanup_versions.py
```

## Adding more apps to auto-update

Edit the `APPS` list in [`update_apps.py`](update_apps.py). Each entry needs:

```python
{
    "name": "app-name",
    "path": "stable/app-name",
    "image_repo": "ghcr.io/org/image",
    "tag_format": "{version}",          # or "v{version}" if image tags have v prefix
    "version_source": "github_release", # or "github_tags" or "ghcr_tags"
    "github_repo": "org/repo",
}
```

## Credits

- [TrueCharts](https://truecharts.org) — original chart catalog
- [v3DJG6GL](https://github.com/v3DJG6GL/truecharts_archive) — maintained the fork for 1.5 years
