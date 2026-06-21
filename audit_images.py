#!/usr/bin/env python3
"""Audit: for each app, does the pipeline-derived image:tag actually exist?"""
import sys, io, urllib.request, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import update_apps as u

def manifest_exists(repo, tag):
    """Return True/False if repo:tag manifest exists, or None on lookup error."""
    accept = ", ".join([
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
    ])
    try:
        if repo.startswith("ghcr.io/"):
            path = repo[len("ghcr.io/"):]
            tok = json.load(urllib.request.urlopen(
                f"https://ghcr.io/token?scope=repository:{path}:pull", timeout=15))["token"]
            url = f"https://ghcr.io/v2/{path}/manifests/{tag}"; hdr = {"Authorization": f"Bearer {tok}", "Accept": accept}
        elif repo.startswith(("oci.", "http")):
            host = repo.split("/")[0]; path = repo[len(host)+1:]
            url = f"https://{host}/v2/{path}/manifests/{tag}"; hdr = {"Accept": accept}
        else:  # docker hub (docker.io/x or bare linuxserver/x)
            path = repo[len("docker.io/"):] if repo.startswith("docker.io/") else repo
            if "/" not in path: path = "library/" + path
            tok = json.load(urllib.request.urlopen(
                f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{path}:pull", timeout=15))["token"]
            url = f"https://registry-1.docker.io/v2/{path}/manifests/{tag}"; hdr = {"Authorization": f"Bearer {tok}", "Accept": accept}
        req = urllib.request.Request(url, method="GET", headers=hdr)
        urllib.request.urlopen(req, timeout=15)
        return True
    except urllib.error.HTTPError as e:
        return False if e.code in (404, 401) else None
    except Exception:
        return None

print(f"{'APP':14} {'DERIVED IMAGE:TAG':62} EXISTS")
print("-"*90)
for app in u.APPS:
    name = app["name"]
    try:
        v = u.get_latest_version(app)
    except Exception:
        v = None
    if not v:
        print(f"{name:14} {'<version lookup FAILED>':62} ??"); continue
    tag = app.get("tag_format", "{version}").format(version=v)
    img = f"{app['image_repo']}:{tag}"
    ex = manifest_exists(app["image_repo"], tag)
    mark = "OK" if ex is True else ("*** MISSING ***" if ex is False else "? (lookup err)")
    print(f"{name:14} {img:62} {mark}")
